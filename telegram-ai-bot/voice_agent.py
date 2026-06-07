"""自然な双方向AI電話エージェント (Twilio Voice Webhook).

Twilio の通話を受け、相手の発話を聞いて Claude が短い口語で自然に応答する
会話サーバー。Polly のニューラル日本語音声で、本物の電話のように応対します。

公開URL（Replit / Render / ngrok 等）で動かし、その
  https://<あなたのURL>/twilio/voice
を Twilio の Voice Webhook(POST) に設定します。発信時に ?goal=... で用件を渡せます。

⚠️ 法令順守: 国・地域によってはAI/ボットであることの開示や通話録音の同意が
必要です。正当な相手・正当な目的にのみ使用し、各地の法令に従ってください。
本エージェントは相手に「AIか」と問われたら正直に答えます。
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict

from anthropic import Anthropic
from flask import Flask, Response, request
from twilio.twiml.voice_response import Gather, VoiceResponse

KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")
VOICE = os.environ.get("TWILIO_VOICE", "Polly.Takumi-Neural")
LANG = os.environ.get("TWILIO_VOICE_LANG", "ja-JP")
PERSONA = os.environ.get(
    "AGENT_PERSONA",
    "あなたは電話で受け答えをする、礼儀正しく自然な日本語の話者です。",
)
GREETING = os.environ.get("AGENT_GREETING", "")  # 空なら Claude が生成

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("voice-agent")

client = Anthropic(api_key=KEY)
app = Flask(__name__)

convos: dict[str, list] = defaultdict(list)  # CallSid -> messages
goals: dict[str, str] = {}  # CallSid -> 用件


def _system(sid: str) -> str:
    goal = goals.get(sid, "")
    s = (
        PERSONA
        + " これは電話越しの会話です。返答は必ず1〜2文の短い話し言葉にし、"
        "自然な相づちや間を意識してください。箇条書き・記号・URLは読み上げません。"
        "相手の発言に丁寧に応じ、用件を前に進めます。"
        "相手があなたをAIかと尋ねたら、正直に答えてください。"
    )
    if goal:
        s += f"\n\nこの電話の目的: {goal}"
    return s


def _reply(sid: str, user_text: str) -> str:
    msgs = convos[sid]
    if user_text:
        msgs.append({"role": "user", "content": user_text})
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=200,
            system=_system(sid),
            thinking={"type": "disabled"},  # 電話は低遅延優先
            output_config={"effort": "low"},
            messages=msgs or [{"role": "user", "content": "(電話がつながりました。自然にあいさつしてください)"}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception:
        log.exception("Claude 応答失敗")
        text = "申し訳ありません、少々お待ちください。"
    if not text:
        text = "はい。"
    msgs.append({"role": "assistant", "content": text})
    return text


def _gather(sid: str, say_text: str) -> Response:
    vr = VoiceResponse()
    g = Gather(
        input="speech",
        language=LANG,
        speech_timeout="auto",
        action="/twilio/respond",
        method="POST",
    )
    g.say(say_text, voice=VOICE, language=LANG)
    vr.append(g)
    # 無言が続いたら応答ハンドラへ（再度促す）
    vr.redirect("/twilio/respond", method="POST")
    return Response(str(vr), mimetype="text/xml")


@app.route("/twilio/voice", methods=["POST", "GET"])
def voice():
    sid = request.values.get("CallSid", "anon")
    goal = request.values.get("goal", "")
    if goal:
        goals[sid] = goal
    convos[sid] = []
    greet = GREETING or _reply(sid, "")
    if GREETING:
        convos[sid].append({"role": "assistant", "content": GREETING})
    log.info("通話開始 sid=%s goal=%s", sid, goal[:60])
    return _gather(sid, greet)


@app.route("/twilio/respond", methods=["POST", "GET"])
def respond():
    sid = request.values.get("CallSid", "anon")
    speech = (request.values.get("SpeechResult", "") or "").strip()
    if not speech:
        text = _reply(sid, "(相手は無言です。もう一度やさしく促してください)")
        return _gather(sid, text)
    log.info("相手: %s", speech)
    text = _reply(sid, speech)
    log.info("AI: %s", text)
    return _gather(sid, text)


@app.route("/twilio/status", methods=["POST", "GET"])
def status():
    sid = request.values.get("CallSid", "")
    convos.pop(sid, None)
    goals.pop(sid, None)
    return ("", 204)


@app.route("/", methods=["GET"])
def health():
    return "voice-agent OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
