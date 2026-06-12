"""LINE 連動: LINE 公式アカウント宛のメッセージに Claude が自動応答する Webhook サーバー.

ジムの「受付AI」。お客さんが LINE 公式アカウントに送った質問に対し、
mega_bot に登録した知識ベース（料金表・FAQ・規約など）を参照して 24 時間自動応答する。
受け取った問い合わせは mega_bot の顧客台帳へも記録され、朝のブリーフィングにも乗る。

仕組み:
  LINE Messaging API Webhook → このサーバー POST /callback
  → 署名検証(X-Line-Signature) → Claude で応答生成 → LINE reply API で返信

必要な環境変数:
  LINE_CHANNEL_SECRET        … LINE Developers のチャネルシークレット
  LINE_CHANNEL_ACCESS_TOKEN  … 長期のチャネルアクセストークン
  ANTHROPIC_API_KEY          … Claude（mega_bot と共通）
任意:
  LINE_PERSONA               … 受付AIの人物像（既定あり）
  LINE_WEB_SEARCH=0          … ウェブ検索を無効化（既定は有効）
  LINE_LOG_CRM=0             … 問い合わせを顧客台帳に記録しない（既定は記録する）
  LINE_PORT                  … 待受ポート（既定 8200）

起動例:
  uvicorn line_agent:app --host 0.0.0.0 --port 8200
公開（HTTPS が必要）:
  cloudflared tunnel --url http://localhost:8200
  → 払い出された https URL + "/callback" を LINE Developers の Webhook URL に設定

⚠️ 個人情報・各種規約に従って運用してください（自動応答である旨の明示を推奨）。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
from collections import defaultdict, deque

import httpx
from anthropic import AsyncAnthropic
from fastapi import FastAPI, Request, Response

# mega_bot の知識ベース・顧客台帳を共有（同じマシンで同じ DATA_DIR を読む）。
# 取り込めない環境でも単体で動くようにフォールバックする。
try:
    import mega_bot  # noqa: F401

    _MB = True
except Exception:
    _MB = False

CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")
WEB_SEARCH = os.environ.get("LINE_WEB_SEARCH", "1") not in ("0", "false", "False", "")
LOG_CRM = os.environ.get("LINE_LOG_CRM", "1") not in ("0", "false", "False", "")
HISTORY_TURNS = int(os.environ.get("LINE_HISTORY_TURNS", "8"))

PERSONA = os.environ.get(
    "LINE_PERSONA",
    "あなたは不動産会社の、礼儀正しく親切な LINE 接客AIです。"
    "お客様からの物件に関するお問い合わせ（賃貸・売買・空き状況・家賃・価格・間取り・"
    "エリア・初期費用・設備・ペット可否・内見/来店予約など）に、簡潔で分かりやすい"
    "日本語で対応します。"
    "物件情報・条件・費用は、提供された『店舗の知識』に厳密に基づいて答えます。"
    "知識に無い個別の空き状況・契約可否・価格交渉・重要事項などは断定せず、"
    "『担当者より折り返しご連絡します』とご案内したうえで、ご希望条件"
    "（エリア・ご予算・間取り・入居/購入時期・お名前・ご連絡先）をさりげなくヒアリングします。"
    "内見や来店のご希望があれば、希望日時を伺い『担当者から確定のご連絡をいたします』と"
    "ご案内します。宅地建物取引業法に反する断定（重要事項の確定説明・誇大広告・"
    "おとり的表現）はしません。絵文字は控えめに、長文になりすぎないように。"
    "相手がAIかと尋ねたら正直に答えます。",
)

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("line-agent")

claude = AsyncAnthropic(api_key=KEY)
app = FastAPI()

# LINE userId -> 直近の会話履歴
_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=HISTORY_TURNS * 2))


def _knowledge_block() -> str:
    """mega_bot に登録された全知識ベースを連結して『店舗の知識』として渡す。"""
    if not _MB:
        return ""
    parts: list[str] = []
    budget = 8000
    try:
        for _key, items in mega_bot.knowledge.items():
            for item in items:
                block = f"■{item.get('title', 'メモ')}\n{item.get('content', '')}"
                parts.append(block[:budget])
                budget -= len(block)
                if budget <= 0:
                    return "\n\n".join(parts)
    except Exception:
        log.exception("知識ベース読み込み失敗")
    return "\n\n".join(parts)


def _system_prompt() -> str:
    s = PERSONA
    kb = _knowledge_block()
    if kb:
        s += "\n\n[店舗の知識（この内容に基づいて回答する）]\n" + kb
    return s


def verify_signature(body: bytes, signature: str) -> bool:
    """X-Line-Signature を検証（チャネルシークレットによる HMAC-SHA256）。"""
    if not CHANNEL_SECRET:
        return False
    mac = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, signature or "")


async def _generate_reply(user_id: str, text: str) -> str:
    """会話履歴＋知識ベースを使って Claude で返信を生成（ウェブ検索可）。"""
    h = _hist[user_id]
    msgs = list(h) + [{"role": "user", "content": text}]
    tools = [{"type": "web_search_20260209", "name": "web_search"}] if WEB_SEARCH else []
    out = ""
    try:
        for _ in range(4):  # web_search の pause_turn を継続
            resp = await claude.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=_system_prompt(),
                tools=tools,
                messages=msgs,
            )
            msgs.append({"role": "assistant", "content": resp.content})
            out = "".join(
                b.text for b in resp.content if getattr(b, "type", None) == "text"
            ).strip()
            if getattr(resp, "stop_reason", None) == "pause_turn":
                continue
            break
    except Exception:
        log.exception("返信生成失敗")
        return "申し訳ありません。ただ今こみ合っております。担当者より折り返しご連絡いたします。"
    h.append({"role": "user", "content": text})
    h.append({"role": "assistant", "content": out})
    return out or "ご連絡ありがとうございます。担当者より折り返しご連絡いたします。"


async def _line_reply(reply_token: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=20) as cli:
        await cli.post(
            "https://api.line.me/v2/bot/message/reply",
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": text[:4900]}]},
        )


async def _line_profile_name(user_id: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.get(
                f"https://api.line.me/v2/bot/profile/{user_id}",
                headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
            )
        if r.status_code == 200:
            return r.json().get("displayName", "") or user_id
    except Exception:
        pass
    return user_id


def _log_inquiry(name: str, text: str) -> None:
    """問い合わせを mega_bot の顧客台帳へ記録（朝のブリーフィングにも反映される）。"""
    if not (_MB and LOG_CRM):
        return
    try:
        # TEAM_MODE 時はキーが "team" に集約されるため chat_id は任意で良い
        mega_bot.add_customer_note(0, f"LINE: {name}", f"[LINE問い合わせ] {text}")
    except Exception:
        log.exception("顧客台帳への記録に失敗")


async def _handle_message_event(ev: dict) -> None:
    reply_token = ev.get("replyToken", "")
    user_id = ev.get("source", {}).get("userId", "")
    text = ev.get("message", {}).get("text", "").strip()
    if not (reply_token and text):
        return
    name = await _line_profile_name(user_id) if user_id else "お客様"
    _log_inquiry(name, text)
    reply = await _generate_reply(user_id or reply_token, text)
    try:
        await _line_reply(reply_token, reply)
    except Exception:
        log.exception("LINE 返信送信失敗")


@app.get("/")
async def health():
    return {
        "ok": True,
        "service": "line-agent",
        "knowledge_linked": _MB,
        "web_search": WEB_SEARCH,
    }


@app.post("/callback")
async def callback(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")
    if not verify_signature(body, signature):
        log.warning("署名検証に失敗（不正なリクエスト）")
        return Response(status_code=400, content="bad signature")
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return Response(status_code=400, content="bad body")
    # LINE は素早い 200 応答を期待するため、処理は背後で走らせる
    for ev in data.get("events", []):
        if ev.get("type") == "message" and ev.get("message", {}).get("type") == "text":
            asyncio.create_task(_handle_message_event(ev))
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("LINE_PORT", "8200"))
    if not (CHANNEL_SECRET and ACCESS_TOKEN):
        log.warning("LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN が未設定です。")
    uvicorn.run(app, host="0.0.0.0", port=port)
