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

import html
import logging
import os
from collections import defaultdict

from anthropic import Anthropic
from flask import Flask, Response, request

KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")
VOICE = os.environ.get("TWILIO_VOICE", "Polly.Takumi-Neural")
LANG = os.environ.get("TWILIO_VOICE_LANG", "ja-JP")
PERSONA = os.environ.get(
    "AGENT_PERSONA",
    "あなたは電話で受け答えをする、礼儀正しく自然な日本語の話者です。",
)
GREETING = os.environ.get("AGENT_GREETING", "")  # 空なら Claude が生成
# 聞き取り精度（自由会話向け）。phone_call / experimental_conversations / deepgram_nova-2
SPEECH_MODEL = os.environ.get("TWILIO_SPEECH_MODEL", "experimental_conversations")
RATE = os.environ.get("TWILIO_VOICE_RATE", "97%")  # 話速（少しゆっくりが自然）

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
        + " これは電話越しの会話です。次を厳守してください。"
        "・返答は1文、長くても2文。電話のテンポを最優先。"
        "・自然な話し言葉。『はい』『ええ』『なるほど』『そうですね』などの相づちを適度に。"
        "・硬い書き言葉や説明口調を避け、口語で。語尾を自然に崩してよい。"
        "・箇条書き・記号・絵文字・URL・番号列挙は読み上げない。"
        "・相手の発言を一度受け止めてから、用件を一歩進める。"
        "・長く考え込まず即答する。分からなければ素直に確認する。"
        "・相手があなたをAIかと尋ねたら正直に答える。"
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
            max_tokens=120,  # 短く即答（低遅延）
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


_FAREWELL = ("さようなら", "失礼します", "切りますね", "また連絡", "ありがとうございました失礼", "もう大丈夫")


def _to_ssml(text: str) -> str:
    """句読点に自然な間を入れ、話速を調整した SSML を返す。"""
    t = html.escape(text)
    t = t.replace("、", '、<break time="170ms"/>')
    t = t.replace("。", '。<break time="280ms"/>')
    t = t.replace("！", '！<break time="280ms"/>').replace("？", '？<break time="280ms"/>')
    return f'<prosody rate="{RATE}">{t}</prosody>'


def _say(text: str) -> str:
    return f'<Say voice="{VOICE}" language="{LANG}">{_to_ssml(text)}</Say>'


def _gather(sid: str, say_text: str, hangup: bool = False) -> Response:
    if hangup:
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response>{_say(say_text)}<Hangup/></Response>"
        )
        return Response(xml, mimetype="text/xml")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Gather input="speech" language="{LANG}" speechTimeout="auto" '
        f'enhanced="true" speechModel="{SPEECH_MODEL}" '
        f'action="/twilio/respond" method="POST">'
        f"{_say(say_text)}"
        "</Gather>"
        '<Redirect method="POST">/twilio/respond</Redirect>'
        "</Response>"
    )
    return Response(xml, mimetype="text/xml")


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
    # 相手が終話の意思を示したら、ひと言添えて自然に切る
    bye = any(k in speech for k in _FAREWELL)
    return _gather(sid, text, hangup=bye)


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
