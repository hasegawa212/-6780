"""Slack natural-language search for the Telegram bot.

Exposes one entry point:
    slack_search(query, *, user_token, bot_token, anthropic_key) -> str

Strategy:
  - If a User OAuth Token (xoxp-, with search:read scope) is provided, hit
    Slack's search.messages API for full-workspace text search.
  - Otherwise fall back to bot-token mode: iterate over the channels the bot
    is a member of via conversations.list + conversations.history and grep
    for the query case-insensitively. Slower and bot-scope limited, but no
    user token required.

After collecting hits, ask Claude (Haiku for speed) for a short Japanese
bullet summary so the Telegram reply leads with insight, not raw logs.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

try:
    import anthropic
except ImportError:
    anthropic = None

SLACK_API = "https://slack.com/api"
JST = timezone(timedelta(hours=9))
DEFAULT_SUMMARY_MODEL = os.environ.get("SLACK_SEARCH_SUMMARY_MODEL", "claude-haiku-4-5-20251001")
MAX_HITS = int(os.environ.get("SLACK_SEARCH_MAX_HITS", "20"))
FALLBACK_CHANNEL_SCAN_LIMIT = int(os.environ.get("SLACK_SEARCH_FALLBACK_CHANNELS", "30"))
FALLBACK_HISTORY_LIMIT = int(os.environ.get("SLACK_SEARCH_FALLBACK_HISTORY", "200"))


def slack_search(
    query: str,
    *,
    user_token: str = "",
    bot_token: str = "",
    anthropic_key: str = "",
    max_hits: int = MAX_HITS,
) -> str:
    query = (query or "").strip()
    if not query:
        return "使い方: /slack <検索ワード>"

    if user_token.startswith("xoxp-"):
        hits = _search_via_api(query, user_token, max_hits)
        mode = "search.messages (User token)"
    elif bot_token.startswith("xoxb-"):
        hits = _search_via_history(query, bot_token, max_hits)
        mode = "history scan (Bot token)"
    else:
        return (
            "❌ Slack トークンが見つかりません。\n"
            "`SLACK_USER_TOKEN` (xoxp-, search:read 付き) または\n"
            "`SLACK_BOT_TOKEN` (xoxb-) を環境変数に設定してください。"
        )

    if not hits:
        return f"🔍 「{query}」に該当する Slack 投稿は見つかりませんでした。\n(mode: {mode})"

    summary = _summarize_with_claude(query, hits, anthropic_key)
    formatted_hits = "\n\n".join(_format_hit(h, i) for i, h in enumerate(hits[:max_hits], 1))
    parts = [f"🔍 Slack 検索: `{query}` — {len(hits)} 件", f"(mode: {mode})", ""]
    if summary:
        parts.append("📝 要約:")
        parts.append(summary)
        parts.append("")
    parts.append("📌 上位の投稿:")
    parts.append(formatted_hits)
    return "\n".join(parts)


def _slack_get(method: str, token: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{SLACK_API}/{method}?{qs}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def _search_via_api(query: str, token: str, count: int) -> list[dict]:
    data = _slack_get(
        "search.messages",
        token,
        {"query": query, "count": count, "sort": "timestamp", "sort_dir": "desc"},
    )
    if not data.get("ok"):
        raise RuntimeError(f"Slack search error: {data.get('error')}")
    matches = (data.get("messages") or {}).get("matches") or []
    return [
        {
            "channel_name": (m.get("channel") or {}).get("name", "?"),
            "username": m.get("username") or "?",
            "text": m.get("text") or "",
            "ts": m.get("ts"),
            "permalink": m.get("permalink", ""),
        }
        for m in matches
    ]


def _search_via_history(query: str, token: str, max_hits: int) -> list[dict]:
    needle = query.lower()
    hits: list[dict] = []
    cursor: str | None = None
    seen_channels = 0
    while seen_channels < FALLBACK_CHANNEL_SCAN_LIMIT:
        params = {"limit": 200, "exclude_archived": "true", "types": "public_channel,private_channel"}
        if cursor:
            params["cursor"] = cursor
        data = _slack_get("conversations.list", token, params)
        if not data.get("ok"):
            break
        for ch in data.get("channels", []):
            if seen_channels >= FALLBACK_CHANNEL_SCAN_LIMIT:
                break
            seen_channels += 1
            if not ch.get("is_member"):
                continue
            try:
                hist = _slack_get(
                    "conversations.history",
                    token,
                    {"channel": ch["id"], "limit": FALLBACK_HISTORY_LIMIT},
                )
            except urllib.error.HTTPError:
                continue
            if not hist.get("ok"):
                continue
            for msg in hist.get("messages", []):
                text = msg.get("text") or ""
                if needle not in text.lower():
                    continue
                hits.append({
                    "channel_name": ch.get("name", "?"),
                    "username": msg.get("user") or msg.get("bot_id") or "?",
                    "text": text,
                    "ts": msg.get("ts"),
                    "permalink": "",
                })
                if len(hits) >= max_hits:
                    return hits
        cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break
    return hits


def _summarize_with_claude(query: str, hits: Iterable[dict], anthropic_key: str) -> str:
    if not anthropic_key or anthropic is None:
        return ""
    client = anthropic.Anthropic(api_key=anthropic_key)
    log = "\n---\n".join(
        f"[#{h['channel_name']}] @{h['username']} ({_format_ts(h.get('ts'))})\n{h['text']}"
        for h in list(hits)[:30]
    )
    try:
        resp = client.messages.create(
            model=DEFAULT_SUMMARY_MODEL,
            max_tokens=600,
            system="ユーザーの Slack 検索結果を要約する日本語アシスタント。3〜5 個の箇条書きで、誰が何について話していたかを簡潔にまとめる。固有名詞・日付は残す。",
            messages=[{
                "role": "user",
                "content": f"クエリ: 「{query}」\n\n以下は Slack 検索のヒット:\n\n{log}\n\n要点を箇条書きで日本語に。",
            }],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"(要約生成失敗: {e})"


def _clean_body(text: str) -> str:
    text = re.sub(r"<@U[A-Z0-9]+>", "", text)
    text = re.sub(r"<#C[A-Z0-9]+\|([^>]+)>", r"#\1", text)
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    text = re.sub(r":[a-z0-9_+-]+:", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _format_hit(h: dict, idx: int = 0) -> str:
    ts = _format_ts(h.get("ts"))
    text = _clean_body(h.get("text") or "")[:150]
    prefix = f"{idx}. " if idx else "• "
    header = f"━━━━━━━━━━━━━━━━━━━━\n{prefix}#{h['channel_name']} | @{h['username']} | {ts}"
    parts = [header, text]
    if h.get("permalink"):
        parts.append(f"→ {h['permalink']}")
    return "\n".join(parts)


def _format_ts(ts) -> str:
    if not ts:
        return "?"
    try:
        return datetime.fromtimestamp(float(ts), tz=JST).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "?"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python slack_search.py <query>")
        sys.exit(1)
    print(slack_search(
        " ".join(sys.argv[1:]),
        user_token=os.environ.get("SLACK_USER_TOKEN", ""),
        bot_token=os.environ.get("SLACK_BOT_TOKEN", ""),
        anthropic_key=os.environ.get("ANTHROPIC_API_KEY", ""),
    ))
