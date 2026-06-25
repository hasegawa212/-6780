"""TAC の Webhook サーバー（Flask）。

チャネル固有のプロトコルをここで吸収し、コアの TACConnector に橋渡しする。
  - 音声     : Twilio Voice Webhook（<Gather> ベース）→ TwiML を返す
  - メッセージング: Conversations / Messaging Webhook（JSON）→ テキストを返す
  - 支援      : エージェントデスクトップ向けに直近の支援シグナルを返す JSON API

既存の voice_agent.py と同じ TwiML スタイルに合わせている。公開URLを
  /tac/voice    → Voice Webhook(POST)
  /tac/message  → Messaging/Conversations Webhook(POST)
に設定して使う。
"""

from __future__ import annotations

import html
import os

from flask import Flask, Response, jsonify, request

from .connector import TACConnector
from .models import Channel

app = Flask(__name__)
conn = TACConnector()

VOICE = os.environ.get("TWILIO_VOICE", "Polly.Takumi-Neural")
LANG = os.environ.get("TWILIO_VOICE_LANG", "ja-JP")
SPEECH_MODEL = os.environ.get("TWILIO_SPEECH_MODEL", "experimental_conversations")


# ---------------- 音声 ----------------
def _say(text: str) -> str:
    return f'<Say voice="{VOICE}" language="{LANG}">{html.escape(text)}</Say>'


def _twiml_gather(say_text: str, hangup: bool = False) -> Response:
    if hangup:
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response>{_say(say_text)}<Hangup/></Response>"
        )
    else:
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f'<Gather input="speech" language="{LANG}" speechTimeout="auto" '
            f'enhanced="true" speechModel="{SPEECH_MODEL}" '
            f'action="/tac/voice/respond" method="POST">'
            f"{_say(say_text)}"
            "</Gather>"
            '<Redirect method="POST">/tac/voice/respond</Redirect>'
            "</Response>"
        )
    return Response(xml, mimetype="text/xml")


@app.route("/tac/voice", methods=["POST", "GET"])
def voice_start():
    sid = request.values.get("CallSid", "anon")
    frm = request.values.get("From", "")
    goal = request.values.get("goal", "")
    conn.start(sid, Channel.VOICE, customer_identity=frm, goal=goal)
    # 最初の一言（顧客発話なしで挨拶を生成）。通話は低遅延優先で支援解析をスキップ
    result = conn.handle(sid, "(通話がつながりました。自然にあいさつしてください)",
                         realtime_assist=False)
    return _twiml_gather(result.text or "お電話ありがとうございます。")


@app.route("/tac/voice/respond", methods=["POST", "GET"])
def voice_respond():
    sid = request.values.get("CallSid", "anon")
    speech = (request.values.get("SpeechResult", "") or "").strip()
    conv = conn.get(sid)
    if conv is None:
        return _twiml_gather("恐れ入ります、最初からおかけ直しください。", hangup=True)
    if not speech:
        return _twiml_gather("恐れ入ります、もう一度お願いできますか。")
    result = conn.handle(sid, speech, realtime_assist=False)
    if result.handed_off:
        # ハンドオフ後は ConversationRelay/Studio が担当者へつなぐ。AIは締める。
        return _twiml_gather(result.text or "担当者におつなぎします。少々お待ちください。")
    return _twiml_gather(result.text or "はい。")


@app.route("/tac/voice/status", methods=["POST", "GET"])
def voice_status():
    sid = request.values.get("CallSid", "")
    if request.values.get("CallStatus") == "completed":
        conn.close(sid)
    return ("", 204)


# ---------------- メッセージング (SMS / WhatsApp / Chat) ----------------
@app.route("/tac/message", methods=["POST"])
def message():
    """Conversations/Messaging Webhook。JSON か form どちらでも受ける。"""
    data = request.get_json(silent=True) or request.form
    sid = data.get("ConversationSid") or data.get("MessageSid") or "anon"
    frm = data.get("Author") or data.get("From", "")
    body = data.get("Body", "")
    channel = Channel.WHATSAPP if "whatsapp" in str(frm).lower() else Channel.SMS

    if conn.get(sid) is None:
        conn.start(sid, channel, customer_identity=frm)
    result = conn.handle(sid, body, realtime_assist=False)
    return jsonify({
        "reply": result.text,
        "handed_off": result.handed_off,
        "tool_calls": result.tool_calls,
    })


# ---------------- エージェント支援 / インサイト API ----------------
@app.route("/tac/assist/<sid>", methods=["GET"])
def assist(sid: str):
    """エージェントデスクトップが直近の支援シグナルを取得する。"""
    conv = conn.get(sid)
    if conv is None:
        return jsonify({"error": "unknown conversation"}), 404
    frame = conn.intelligence.on_utterance(conv)
    return jsonify({"conversation": sid, "status": conv.status.value, "signals": frame.signals})


@app.route("/tac/insights", methods=["GET"])
def insights():
    """会話横断の集約インサイト（QA/コーチング/レポート）。"""
    return jsonify(conn.intelligence.insights())


@app.route("/tac/close/<sid>", methods=["POST"])
def close(sid: str):
    return jsonify(conn.close(sid))


@app.route("/", methods=["GET"])
def health():
    return "tac-server OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8090")))
