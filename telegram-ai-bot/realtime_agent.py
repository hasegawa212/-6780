"""リアルタイムAI電話エージェント (Twilio Media Streams ↔ OpenAI Realtime API).

通話音声を双方向リアルタイムでストリーミングし、OpenAI Realtime API の
speech-to-speech モデルで「聞きながら考え、被せて話せる」人間級の会話を実現する。

仕組み:
  Twilio <Connect><Stream> → このサーバーの WebSocket /twilio/stream
  /twilio/stream ←→ OpenAI Realtime (wss://api.openai.com/v1/realtime)
  音声フォーマットは双方 g711_ulaw(8kHz) で無変換ブリッジ → 低遅延

公開URL（cloudflared 等）で起動し、その https URL を Telegram ボットの
VOICE_AGENT_URL に設定する（/call が <Connect><Stream> を返すこのサーバーを使う）。

必要な環境変数: OPENAI_API_KEY
任意: OPENAI_REALTIME_MODEL / OPENAI_REALTIME_VOICE / AGENT_PERSONA

⚠️ 法令順守: AI/録音の開示・同意など各地の法律に従って利用してください。
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os

import websockets
from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import PlainTextResponse

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("OPENAI_REALTIME_MODEL", "gpt-realtime")
VOICE = os.environ.get("OPENAI_REALTIME_VOICE", "marin")
PERSONA = os.environ.get(
    "AGENT_PERSONA",
    "あなたは電話で受け答えをする、礼儀正しく自然な日本語の話者です。"
    "短い口語で、相づちを交え、自然な間で話します。"
    "硬い説明口調を避け、相手の発言を受け止めてから用件を進めます。"
    "相手がAIかと尋ねたら正直に答えます。",
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("realtime-agent")

app = FastAPI()


@app.get("/")
async def health():
    return PlainTextResponse("realtime-agent OK")


@app.api_route("/twilio/voice", methods=["GET", "POST"])
async def voice(request: Request):
    host = request.headers.get("host", "")
    goal = request.query_params.get("goal", "")
    g = html.escape(goal)
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<Stream url="wss://{host}/twilio/stream">'
        f'<Parameter name="goal" value="{g}"/>'
        "</Stream>"
        "</Connect></Response>"
    )
    return Response(content=twiml, media_type="text/xml")


async def _openai_connect():
    # GA API: /v1/realtime（ベータヘッダなし）
    url = f"wss://api.openai.com/v1/realtime?model={MODEL}"
    headers = {"Authorization": f"Bearer {OPENAI_KEY}"}
    # websockets のバージョン差を吸収（additional_headers / extra_headers）
    try:
        return await websockets.connect(url, additional_headers=headers, max_size=None)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers, max_size=None)


@app.websocket("/twilio/stream")
async def stream(ws: WebSocket):
    await ws.accept()
    stream_sid = None
    try:
        oai = await _openai_connect()
    except Exception:
        log.exception("OpenAI 接続失敗")
        await ws.close()
        return

    async def configure(goal_text: str):
        # GA API のセッション形式（audio.input/output 入れ子、format は audio/pcmu=μ-law）
        instr = PERSONA + (f"\n\nこの電話の目的: {goal_text}" if goal_text else "")
        await oai.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": instr,
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcmu"},
                        "turn_detection": {"type": "server_vad"},
                        "transcription": {"model": "whisper-1"},
                    },
                    "output": {
                        "format": {"type": "audio/pcmu"},
                        "voice": VOICE,
                    },
                },
            },
        }))
        # 先にこちらから自然に挨拶させる
        await oai.send(json.dumps({"type": "response.create"}))

    async def from_twilio():
        nonlocal stream_sid
        try:
            while True:
                data = json.loads(await ws.receive_text())
                ev = data.get("event")
                if ev == "start":
                    stream_sid = data["start"]["streamSid"]
                    params = data["start"].get("customParameters", {}) or {}
                    await configure(params.get("goal", ""))
                    log.info("通話開始 sid=%s", stream_sid)
                elif ev == "media":
                    await oai.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": data["media"]["payload"],
                    }))
                elif ev == "stop":
                    break
        except Exception:
            pass

    async def from_openai():
        try:
            async for raw in oai:
                e = json.loads(raw)
                t = e.get("type", "")
                # 音声出力（API差を吸収: ...audio.delta を広く拾う）
                if t.endswith("audio.delta") and e.get("delta") and stream_sid:
                    await ws.send_text(json.dumps({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": e["delta"]},
                    }))
                # 相手が話し始めたら、再生中の音声を止める（バージイン）
                elif t == "input_audio_buffer.speech_started" and stream_sid:
                    await ws.send_text(json.dumps({"event": "clear", "streamSid": stream_sid}))
                    await oai.send(json.dumps({"type": "response.cancel"}))
                elif t == "conversation.item.input_audio_transcription.completed":
                    log.info("相手: %s", e.get("transcript", ""))
                elif t == "error":
                    log.warning("OpenAI error: %s", e.get("error"))
        except Exception:
            pass

    try:
        await asyncio.gather(from_twilio(), from_openai())
    finally:
        try:
            await oai.close()
        except Exception:
            pass
