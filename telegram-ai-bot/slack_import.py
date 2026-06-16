"""Slack の会話から営業ノウハウを抽出し、ボットの知識ベースに学習させるツール.

Slack の指定チャンネルの会話を取得 → Claude で「再利用できる営業ノウハウ」へ整理 →
mega_bot の知識ベースへ保存する。保存後は Telegram アシスタントと LINE 営業ボットの
両方が、その知見を踏まえて応答する。

必要な環境変数:
  SLACK_BOT_TOKEN   … Slack アプリの Bot User OAuth Token（xoxb-...）
                      スコープ: channels:history（公開）/ groups:history（非公開）
                      対象チャンネルに Bot を /invite しておくこと
  ANTHROPIC_API_KEY … Claude（mega_bot と共通）
任意:
  SLACK_CHANNEL_ID  … 取り込むチャンネルID（未指定なら第1引数）
  SLACK_IMPORT_LIMIT… 取得メッセージ数（既定 300）
  KB_CHAT_ID        … 保存先の知識ベースキー（既定 0。TEAM_MODE なら全員共有）

使い方:
  SLACK_BOT_TOKEN=xoxb-... python3 slack_import.py C0XXXXXXX
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys

import httpx

try:
    import mega_bot

    _MB = True
except Exception:
    _MB = False

from anthropic import AsyncAnthropic

SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
# 複数チャンネル対応: 環境変数(カンマ/空白区切り) または引数で複数指定できる
_chan_src = os.environ.get("SLACK_CHANNEL_ID", "") or " ".join(sys.argv[1:])
CHANNELS = [c for c in _chan_src.replace(",", " ").split() if c]
LIMIT = int(os.environ.get("SLACK_IMPORT_LIMIT", "300"))
KB_CHAT_ID = int(os.environ.get("KB_CHAT_ID", "0"))
KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")

DISTILL_PROMPT = (
    "以下は不動産営業チームの Slack の会話ログです。ここから、接客・反響対応で"
    "再利用できる営業ノウハウを抽出して、構造化してまとめてください：\n"
    "①よくある質問と模範回答（FAQ）\n"
    "②刺さった切り返し・トーク例（お客様の不安や断りへの返し）\n"
    "③アポ（内見・来店）獲得のコツ\n"
    "④やってはいけないNG対応・注意点\n"
    "固有名詞・個人情報・電話番号などは一般化し、再利用できる形にすること。"
    "簡潔な箇条書きで、実務でそのまま使えるようにまとめてください。"
)


def resolve_channel(name_or_id: str) -> str:
    """チャンネル名(#30 等)を ID(C0...) へ解決する。すでに ID ならそのまま返す。"""
    s = name_or_id.lstrip("#")
    if s[:1] in ("C", "G", "D") and s[1:].isalnum() and s.upper() == s:
        return s  # 既に ID 形式
    target = s.lower()
    cursor = ""
    with httpx.Client(timeout=30) as cli:
        while True:
            params = {"limit": "200", "types": "public_channel,private_channel"}
            if cursor:
                params["cursor"] = cursor
            r = cli.get(
                "https://slack.com/api/conversations.list",
                headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
                params=params,
            )
            data = r.json()
            if not data.get("ok"):
                raise RuntimeError(f"チャンネル一覧の取得に失敗: {data.get('error')}")
            for ch in data.get("channels", []):
                if (ch.get("name", "").lower() == target):
                    return ch["id"]
            cursor = data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break
    raise RuntimeError(f"チャンネル『{name_or_id}』が見つかりません（Botが参加しているか確認）。")


def fetch_messages(channel: str) -> list[str]:
    """Slack conversations.history から本文テキストを古い順に取得する。"""
    channel_id = resolve_channel(channel)
    texts: list[str] = []
    cursor = ""
    with httpx.Client(timeout=30) as cli:
        while len(texts) < LIMIT:
            params = {"channel": channel_id, "limit": str(min(200, LIMIT - len(texts)))}
            if cursor:
                params["cursor"] = cursor
            r = cli.get(
                "https://slack.com/api/conversations.history",
                headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
                params=params,
            )
            data = r.json()
            if not data.get("ok"):
                raise RuntimeError(f"Slack API エラー: {data.get('error')}")
            for m in data.get("messages", []):
                t = (m.get("text") or "").strip()
                if t and m.get("subtype") not in ("channel_join", "channel_leave"):
                    texts.append(t)
            cursor = data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break
    texts.reverse()  # 古い順に
    return texts


async def distill(log_text: str) -> str:
    claude = AsyncAnthropic(api_key=KEY)
    resp = await claude.messages.create(
        model=MODEL,
        max_tokens=2000,
        system="あなたは不動産営業の知見を整理する有能なアシスタントです。",
        messages=[{"role": "user", "content": DISTILL_PROMPT + "\n\n---\n" + log_text}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


def main() -> int:
    if not SLACK_TOKEN:
        print("❌ SLACK_BOT_TOKEN が未設定です。")
        return 1
    if not CHANNELS:
        print("❌ チャンネルIDを指定してください（引数 or SLACK_CHANNEL_ID）。")
        return 1
    if not _MB:
        print("❌ mega_bot を import できませんでした（同じディレクトリで実行してください）。")
        return 1

    msgs: list[str] = []
    for ch in CHANNELS:
        try:
            got = fetch_messages(ch)
            print(f"▶ {ch}: {len(got)} 件取得")
            msgs.extend(got)
        except Exception as e:
            print(f"⚠️ {ch}: 取得に失敗（{e}）。スキップします。")
    if not msgs:
        print("⚠️ 取得できるメッセージがありません（Botがチャンネルに参加しているか確認）。")
        return 1
    print(f"  合計 {len(msgs)} 件。Claude で営業ノウハウへ整理中…")

    joined = "\n".join(msgs)[:60000]
    knowhow = asyncio.run(distill(joined))
    if not knowhow:
        print("⚠️ 整理結果が空でした。")
        return 1

    title = f"Slack営業ノウハウ {dt.datetime.now().strftime('%Y-%m-%d')}"
    mega_bot.add_knowledge(KB_CHAT_ID, title, knowhow)
    print(f"✅ 知識ベースに保存しました: 「{title}」（{len(knowhow)}字）")
    print("   → Telegramボットと LINE営業ボットの両方が、このノウハウを踏まえて応答します。")
    print("\n--- 抽出されたノウハウ（先頭部分）---")
    print(knowhow[:1200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
