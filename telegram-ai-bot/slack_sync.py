"""Slack -> Supabase sync worker.

Pulls new messages from every channel the bot is a member of, computes
OpenAI text-embedding-3-small embeddings, and upserts them into the
slack-search Supabase project (public.messages).

Designed to run periodically from launchd (see
deploy/com.openclaw.slack-sync.plist). Each invocation resumes from the
per-channel cursor stored in public.sync_state.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

SLACK_API = "https://slack.com/api"
EMBEDDINGS_API = "https://api.openai.com/v1/embeddings"

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
SYNC_HISTORY_LIMIT = int(os.environ.get("SYNC_HISTORY_LIMIT", "200"))
EMBEDDING_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "20"))


def _slack(method: str, params: dict | None = None) -> dict:
    qs = urllib.parse.urlencode(params or {})
    req = urllib.request.Request(
        f"{SLACK_API}/{method}?{qs}",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def _sb(
    path: str,
    *,
    method: str = "GET",
    body=None,
    params: dict | None = None,
    prefer: str | None = None,
):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read()
            return json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase {method} {path} {e.code}: {body_text}") from e


def _embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    payload = {"model": EMBEDDING_MODEL, "input": texts}
    req = urllib.request.Request(
        EMBEDDINGS_API,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return [d["embedding"] for d in data["data"]]


def list_member_channels():
    cursor: str | None = None
    while True:
        params = {
            "limit": 200,
            "exclude_archived": "true",
            "types": "public_channel,private_channel",
        }
        if cursor:
            params["cursor"] = cursor
        data = _slack("conversations.list", params)
        if not data.get("ok"):
            raise RuntimeError(f"conversations.list: {data.get('error')}")
        for ch in data.get("channels", []):
            if ch.get("is_member"):
                yield ch["id"], ch.get("name", "?"), bool(ch.get("is_private"))
        cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break


def upsert_channel(channel_id: str, name: str, is_private: bool) -> None:
    _sb(
        "channels",
        method="POST",
        body=[{"id": channel_id, "name": name, "is_private": is_private}],
        prefer="resolution=merge-duplicates,return=minimal",
    )


def get_sync_cursor(channel_id: str) -> dict | None:
    rows = _sb("sync_state", params={"channel_id": f"eq.{channel_id}", "select": "*"})
    return rows[0] if rows else None


def save_sync_cursor(channel_id: str, oldest_ts: str | None, newest_ts: str | None) -> None:
    _sb(
        "sync_state",
        method="POST",
        body=[{
            "channel_id": channel_id,
            "oldest_synced_ts": oldest_ts,
            "newest_synced_ts": newest_ts,
            "last_synced_at": datetime.now(tz=UTC).isoformat(),
        }],
        prefer="resolution=merge-duplicates,return=minimal",
    )


def fetch_new_messages(channel_id: str, oldest_ts: str | None):
    cursor: str | None = None
    while True:
        params: dict = {
            "channel": channel_id,
            "limit": SYNC_HISTORY_LIMIT,
            "inclusive": "false",
        }
        if oldest_ts:
            params["oldest"] = oldest_ts
        if cursor:
            params["cursor"] = cursor
        data = _slack("conversations.history", params)
        if not data.get("ok"):
            print(f"  warn: history {channel_id}: {data.get('error')}", file=sys.stderr)
            return
        yield from reversed(data.get("messages", []))
        if not data.get("has_more"):
            break
        cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break


def permalink(channel_id: str, ts: str) -> str:
    try:
        data = _slack("chat.getPermalink", {"channel": channel_id, "message_ts": ts})
        return data.get("permalink", "") if data.get("ok") else ""
    except Exception:
        return ""


def get_user_name(user_cache: dict, user_id: str | None) -> str | None:
    if not user_id:
        return None
    if user_id in user_cache:
        return user_cache[user_id]
    try:
        data = _slack("users.info", {"user": user_id})
        if data.get("ok"):
            u = data["user"]
            name = u.get("real_name") or u.get("name") or user_id
            user_cache[user_id] = name
            return name
    except Exception:
        pass
    user_cache[user_id] = user_id
    return user_id


def upsert_messages(rows: list[dict]) -> None:
    if not rows:
        return
    _sb(
        "messages",
        method="POST",
        body=rows,
        prefer="resolution=merge-duplicates,return=minimal",
    )


def sync_channel(channel_id: str, name: str, is_private: bool, user_cache: dict) -> int:
    upsert_channel(channel_id, name, is_private)
    cursor = get_sync_cursor(channel_id)
    oldest_seen = (cursor or {}).get("oldest_synced_ts")
    newest_seen = (cursor or {}).get("newest_synced_ts")

    msgs = [
        m for m in fetch_new_messages(channel_id, newest_seen)
        if m.get("text") and m.get("type") == "message"
    ]
    if not msgs:
        print(f"  {name}: no new messages")
        return 0

    inserted = 0
    pending_texts: list[str] = []
    pending_meta: list[dict] = []

    def flush():
        nonlocal inserted, pending_texts, pending_meta
        if not pending_texts:
            return
        embeddings = _embed(pending_texts)
        rows = [
            {
                "channel_id": channel_id,
                "slack_ts": meta["ts"],
                "user_id": meta["user_id"],
                "username": meta["username"],
                "body": meta["body"],
                "embedding": emb,
                "permalink": meta["permalink"],
                "thread_ts": meta["thread_ts"],
            }
            for emb, meta in zip(embeddings, pending_meta, strict=True)
        ]
        upsert_messages(rows)
        inserted += len(rows)
        pending_texts = []
        pending_meta = []

    for m in msgs:
        ts = m.get("ts")
        if not ts:
            continue
        if oldest_seen is None or ts < oldest_seen:
            oldest_seen = ts
        if newest_seen is None or ts > newest_seen:
            newest_seen = ts
        user_id = m.get("user")
        text = m.get("text") or ""
        pending_texts.append(text)
        pending_meta.append({
            "ts": ts,
            "user_id": user_id,
            "username": get_user_name(user_cache, user_id),
            "body": text,
            "permalink": permalink(channel_id, ts),
            "thread_ts": m.get("thread_ts"),
        })
        if len(pending_texts) >= EMBEDDING_BATCH_SIZE:
            flush()
    flush()

    save_sync_cursor(channel_id, oldest_seen, newest_seen)
    print(f"  {name}: indexed {inserted} new (newest_ts={newest_seen})")
    return inserted


def main() -> int:
    user_cache: dict[str, str] = {}
    total_inserted = 0
    failed = 0
    for ch_id, ch_name, is_priv in list_member_channels():
        print(f"channel {ch_name} ({ch_id}):")
        try:
            total_inserted += sync_channel(ch_id, ch_name, is_priv, user_cache)
        except Exception as e:
            failed += 1
            print(f"  error: {e}", file=sys.stderr)
    print(f"done: {total_inserted} messages indexed across all channels, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
