"""Semantic search over indexed Slack messages.

Embeds the user's query with OpenAI text-embedding-3-small, runs the
match_messages RPC on the slack-search Supabase project, and asks Claude
Haiku to summarize the top hits.

Exposed entrypoint: semantic_search(query, ...) -> Telegram-ready text.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    import anthropic
except ImportError:
    anthropic = None

JST = timezone(timedelta(hours=9))
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
DEFAULT_SUMMARY_MODEL = os.environ.get(
    "SLACK_SEARCH_SUMMARY_MODEL", "claude-haiku-4-5-20251001"
)
SEMSEARCH_TOP_K = int(os.environ.get("SEMSEARCH_TOP_K", "10"))
SEMSEARCH_THRESHOLD = float(os.environ.get("SEMSEARCH_THRESHOLD", "0.4"))


def semantic_search(
    query: str,
    *,
    supabase_url: str,
    service_role_key: str,
    openai_key: str,
    anthropic_key: str = "",
    top_k: int = SEMSEARCH_TOP_K,
    threshold: float = SEMSEARCH_THRESHOLD,
) -> str:
    query = (query or "").strip()
    if not query:
        return "使い方: /semsearch <検索ワード>"
    if not supabase_url or not service_role_key:
        return (
            "❌ Supabase が未設定です。\n"
            "`SUPABASE_URL` と `SUPABASE_SERVICE_ROLE_KEY` を環境変数に設定してください。"
        )
    if not openai_key:
        return "❌ OpenAI が未設定です。`OPENAI_API_KEY` を環境変数に設定してください。"

    try:
        emb = _embed(query, openai_key)
    except Exception as e:
        return f"❌ embedding 失敗: {e}"

    try:
        hits = _match(supabase_url, service_role_key, emb, top_k, threshold)
    except Exception as e:
        return f"❌ Supabase 検索失敗: {e}"

    if not hits:
        return (
            f"🔎 「{query}」に該当する Slack 投稿は見つかりませんでした (類似度 >= {threshold:.2f})。\n"
            "閾値を下げるか別ワードで試して。"
        )

    summary = _summarize(query, hits, anthropic_key)
    formatted = "\n\n".join(_format_hit(h, i) for i, h in enumerate(hits, 1))
    parts = [
        f"🔎 セマンティック検索: `{query}` — {len(hits)} 件 (閾値 {threshold:.2f})",
        "",
    ]
    if summary:
        parts.append("📝 要約 (Claude):")
        parts.append(summary)
        parts.append("")
    parts.append("📌 上位ヒット:")
    parts.append(formatted)
    return "\n".join(parts)


def _embed(text: str, openai_key: str) -> list[float]:
    payload = {"model": EMBEDDING_MODEL, "input": text}
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["data"][0]["embedding"]


def _match(
    supabase_url: str,
    service_role_key: str,
    embedding: list[float],
    top_k: int,
    threshold: float,
) -> list[dict]:
    url = f"{supabase_url}/rest/v1/rpc/match_messages"
    payload = {
        "query_embedding": embedding,
        "match_threshold": threshold,
        "match_count": top_k,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase RPC {e.code}: {body}") from e


def _summarize(query: str, hits: list[dict], anthropic_key: str) -> str:
    if not anthropic_key or anthropic is None:
        return ""
    client = anthropic.Anthropic(api_key=anthropic_key)
    log = "\n---\n".join(
        f"[#{h['channel_name']}] @{h.get('username') or '?'} "
        f"({_format_ts(h.get('slack_ts'))}) sim={h.get('similarity', 0):.2f}\n"
        f"{h.get('body', '')}"
        for h in hits[:20]
    )
    try:
        resp = client.messages.create(
            model=DEFAULT_SUMMARY_MODEL,
            max_tokens=600,
            system=(
                "ユーザーのセマンティック Slack 検索結果を要約する日本語アシスタント。"
                "3〜5 個の箇条書きで、誰がいつ何について話していたかを簡潔にまとめる。"
                "固有名詞と日付は残す。"
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"クエリ: 「{query}」\n\n以下は類似度上位の投稿:\n\n{log}\n\n"
                    "要点を箇条書きで日本語に。"
                ),
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


def _format_hit(h: dict, idx: int) -> str:
    ts = _format_ts(h.get("slack_ts"))
    body = _clean_body(h.get("body") or "")[:150]
    sim = h.get("similarity", 0)
    header = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{idx}. #{h.get('channel_name', '?')} | @{h.get('username') or '?'} | {ts} | sim={sim:.2f}"
    )
    parts = [header, body]
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
        print("Usage: python semantic_search.py <query>")
        sys.exit(1)
    print(semantic_search(
        " ".join(sys.argv[1:]),
        supabase_url=os.environ.get("SUPABASE_URL", ""),
        service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
        openai_key=os.environ.get("OPENAI_API_KEY", ""),
        anthropic_key=os.environ.get("ANTHROPIC_API_KEY", ""),
    ))
