"""Speech-to-Speech リアルタイム音声（最先端・音声ネイティブ）。

Twilio Media Streams（生の μ-law 8kHz 音声）と OpenAI Realtime API を
WebSocket でブリッジする。STT→LLM→TTS の変換を挟まず音声を直接やり取りするため、
遅延が極小で、相づち・感情・割り込み（barge-in）が人間レベルになる。

構成:
  電話 → Twilio → <Connect><Stream> → このサーバー(/tac/media-stream)
       ↕（μ-law 音声をそのまま中継・g711_ulaw で無変換）
  OpenAI Realtime（音声ネイティブモデル）

依存: fastapi, uvicorn[standard], websockets  （pip install fastapi 'uvicorn[standard]' websockets）
起動: uvicorn tac.realtime:app --host 0.0.0.0 --port 8090
番号の Voice Webhook を /tac/voice-stream に向ける。

※ 頭脳は OpenAI Realtime。Claude は使わない（ハイブリッドにしたい場合は別途）。
※ 要 OPENAI_API_KEY（Realtime 利用可の有料アカウント）。
"""

from __future__ import annotations

import asyncio
import json
import os

import websockets
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse

from .config import CONFIG

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
REALTIME_MODEL = os.environ.get("TAC_REALTIME_MODEL", "gpt-realtime")
# OpenAI Realtime の音声（alloy / echo / shimmer / marin / cedar 等）
REALTIME_VOICE = os.environ.get("TAC_REALTIME_VOICE", "marin")

app = FastAPI()


def _instructions() -> str:
    """エージェントの人格・方針（システム指示）。さくら＋御社情報。"""
    s = CONFIG.persona + (
        " あなたは電話対応のサポート担当です。常に自然な日本語の話し言葉で、"
        " 高級店のおもてなしの心で、簡潔（基本1〜2文）に、相手に寄り添って話します。"
        " 大切な情報（日時・金額・予約内容など）は復唱して確認します。"
        " 分からないことは正直に伝え、推測で断定しません。"
    )
    path = CONFIG.business_info_file
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                info = f.read().strip()
            if info:
                s += "\n\n# 当社の正確な情報（これに基づいて回答）\n" + info
        except OSError:
            pass
    return s


@app.api_route("/tac/voice-stream", methods=["GET", "POST"])
async def voice_stream(request: Request) -> HTMLResponse:
    """双方向 Media Stream を開始する TwiML を返す。"""
    host = request.url.hostname
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<Stream url="wss://{host}/tac/media-stream" />'
        "</Connect></Response>"
    )
    return HTMLResponse(content=twiml, media_type="text/xml")


@app.get("/")
async def health() -> dict:
    return {"ok": True, "service": "tac-realtime", "model": REALTIME_MODEL}


@app.websocket("/tac/media-stream")
async def media_stream(twilio_ws: WebSocket) -> None:
    """Twilio Media Stream ↔ OpenAI Realtime のブリッジ。"""
    await twilio_ws.accept()
    if not OPENAI_API_KEY:
        await twilio_ws.close()
        return

    url = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1",
    }

    async with websockets.connect(url, additional_headers=headers, max_size=None) as oa_ws:
        # セッション設定: g711_ulaw（Twilio と同じ）・サーバーVAD・日本語人格
        await oa_ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "turn_detection": {"type": "server_vad"},
                "input_audio_format": "g711_ulaw",
                "output_audio_format": "g711_ulaw",
                "voice": REALTIME_VOICE,
                "instructions": _instructions(),
                "modalities": ["audio", "text"],
                "temperature": 0.8,
            },
        }))
        # 開口一番（任意）: 最初に挨拶させる
        await oa_ws.send(json.dumps({
            "type": "response.create",
            "response": {"instructions": "まず『お電話ありがとうございます、さくらです。ご用件をうかがいます』と挨拶して。"},
        }))

        state = {"stream_sid": ""}

        async def twilio_to_openai() -> None:
            """Twilio からの音声を OpenAI へ。"""
            try:
                while True:
                    raw = await twilio_ws.receive_text()
                    data = json.loads(raw)
                    ev = data.get("event")
                    if ev == "start":
                        state["stream_sid"] = data["start"]["streamSid"]
                    elif ev == "media":
                        await oa_ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": data["media"]["payload"],
                        }))
                    elif ev == "stop":
                        break
            except Exception:
                pass

        async def openai_to_twilio() -> None:
            """OpenAI からの音声を Twilio へ。発話開始で割り込み（barge-in）。"""
            try:
                async for raw in oa_ws:
                    evt = json.loads(raw)
                    t = evt.get("type")
                    if t == "response.audio.delta" and state["stream_sid"]:
                        await twilio_ws.send_text(json.dumps({
                            "event": "media",
                            "streamSid": state["stream_sid"],
                            "media": {"payload": evt["delta"]},
                        }))
                    elif t == "input_audio_buffer.speech_started" and state["stream_sid"]:
                        # ユーザーが話し始めた → 再生中の音声を止めて割り込ませる
                        await twilio_ws.send_text(json.dumps({
                            "event": "clear",
                            "streamSid": state["stream_sid"],
                        }))
                        await oa_ws.send(json.dumps({"type": "response.cancel"}))
            except Exception:
                pass

        await asyncio.gather(twilio_to_openai(), openai_to_twilio())
