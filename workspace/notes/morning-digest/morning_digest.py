#!/usr/bin/env python3
"""朝のダイジェスト daemon (毎朝 9:00 に launchd で起動)

昨日 (前日 0:00 〜 当日 0:00 JST) の Slack 投稿を Supabase から取得し、
Claude が「重要トピック・決定事項・要対応 action item」を抽出 →
指定の Slack チャンネルに投稿する。

必要な env (launchd plist で渡す):
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY   (publishable でも OK ・RLS off 前提)
    ANTHROPIC_API_KEY
    SLACK_WEBHOOK               (投稿先 webhook)
    DIGEST_MODEL                (任意・既定 claude-sonnet-4-6)
    DIGEST_MAX_MSGS             (任意・既定 300)
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

import anthropic

JST = timezone(timedelta(hours=9))
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
SLACK_WEBHOOK = os.environ["SLACK_WEBHOOK"]
DIGEST_MODEL = os.environ.get("DIGEST_MODEL", "claude-sonnet-4-6")
DIGEST_MAX_MSGS = int(os.environ.get("DIGEST_MAX_MSGS", "300"))


def yesterday_window_jst() -> tuple[float, float]:
    """昨日 0:00 〜 今日 0:00 (JST) を Unix timestamp の (start, end) で返す。"""
    now_jst = datetime.now(JST)
    today_midnight = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_midnight = today_midnight - timedelta(days=1)
    return yesterday_midnight.timestamp(), today_midnight.timestamp()


def fetch_yesterday_messages() -> list[dict]:
    start, end = yesterday_window_jst()
    params = urllib.parse.urlencode({
        "select": "channel_id,channel:channels(name),slack_ts,username,body,permalink",
        "slack_ts": f"gte.{start}",
        "and": f"(slack_ts.lt.{end})",
        "order": "slack_ts.asc",
        "limit": str(DIGEST_MAX_MSGS),
    })
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/messages?{params}",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def clean_body(text: str) -> str:
    text = re.sub(r"<@U[A-Z0-9]+>", "", text)
    text = re.sub(r"<#C[A-Z0-9]+\|([^>]+)>", r"#\1", text)
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    text = re.sub(r":[a-z0-9_+-]+:", "", text)
    return re.sub(r"\s+", " ", text).strip()


def format_for_claude(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        ts = float(r.get("slack_ts") or 0)
        when = datetime.fromtimestamp(ts, JST).strftime("%H:%M")
        ch = (r.get("channel") or {}).get("name") or r.get("channel_id") or "?"
        user = r.get("username") or "?"
        body = clean_body(r.get("body") or "")[:400]
        lines.append(f"[{when}] #{ch} @{user}: {body}")
    return "\n".join(lines)


def claude_digest(slack_log: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    yesterday = (datetime.now(JST) - timedelta(days=1)).strftime("%Y-%m-%d (%a)")
    system = (
        "あなたは MA 不動産代表 (長谷川光) の秘書アシスタント。"
        "前日の Slack 全社ログを読んで朝の経営者向けダイジェストを作る。"
        "形式: 5 セクション。1) 重要決定事項、2) 要対応 action item (誰が何をいつまで)、"
        "3) 顕在化したリスク・クレーム、4) 注目すべき動き、5) その他補足。"
        "簡潔・具体的・固有名詞や金額を残す。各セクション 3〜5 個の箇条書き。"
    )
    user = (
        f"日付: {yesterday}\n\n"
        f"以下は前日全社 Slack ログ (時刻順・チャンネル横断):\n\n{slack_log}\n\n"
        "上のフォーマットでダイジェストを作って。"
    )
    resp = client.messages.create(
        model=DIGEST_MODEL,
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text.strip()


def post_to_slack(digest: str) -> None:
    yesterday = (datetime.now(JST) - timedelta(days=1)).strftime("%Y-%m-%d (%a)")
    payload = {
        "text": f"🌅 朝のダイジェスト ({yesterday})",
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"🌅 *朝のダイジェスト* — {yesterday}"}},
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": digest[:2900]}},
        ],
    }
    if len(digest) > 2900:
        for i in range(2900, len(digest), 2800):
            payload["blocks"].append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": digest[i:i + 2800]},
            })
    req = urllib.request.Request(
        SLACK_WEBHOOK,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Slack {resp.status}: {resp.read()!r}")


def main() -> int:
    try:
        rows = fetch_yesterday_messages()
    except urllib.error.HTTPError as e:
        print(f"Supabase error {e.code}: {e.read().decode('utf-8', errors='replace')}", file=sys.stderr)
        return 1
    if not rows:
        post_to_slack("昨日の Slack には拾うべき投稿がありませんでした。")
        print("no rows, posted placeholder")
        return 0
    print(f"fetched {len(rows)} messages", file=sys.stderr)
    log = format_for_claude(rows)
    digest = claude_digest(log)
    post_to_slack(digest)
    print("digest posted", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
