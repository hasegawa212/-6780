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
import json
import os
import re

from flask import Flask, Response, jsonify, request

from .config import CONFIG
from .connector import TACConnector
from .models import Channel, Status

app = Flask(__name__)
conn = TACConnector()

VOICE = os.environ.get("TWILIO_VOICE", "Polly.Takumi-Neural")
LANG = os.environ.get("TWILIO_VOICE_LANG", "ja-JP")
SPEECH_MODEL = os.environ.get("TWILIO_SPEECH_MODEL", "experimental_conversations")
# 発話終了の無音待ち（秒）。"auto" は安全だがやや長め。"1" 前後でテンポが上がる
SPEECH_TIMEOUT = os.environ.get("TWILIO_SPEECH_TIMEOUT", "auto")
# 着信時の第一声（固定）。LLM を待たず即座に話し始め、立ち上がりを自然にする
GREETING = os.environ.get(
    "TAC_GREETING", "お電話ありがとうございます。さくらです。ご用件をうかがいます。"
)


# ---------------- 音声 ----------------
def _say(text: str) -> str:
    return f'<Say voice="{VOICE}" language="{LANG}">{html.escape(text)}</Say>'


def _clean_for_tts(text: str) -> str:
    """音声合成用にテキストを整形（Markdown 記号・余分な改行を除去）。

    `**太字**` の `*` や見出し `#` を TTS が読み上げてしまうのを防ぎ、改行は
    自然な間（空白）にまとめる。
    """
    if not text:
        return ""
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [text](url) → text
    t = re.sub(r"[*_`#>]+", "", t)                      # 強調/見出し/コード記号
    t = re.sub(r"\s*\n+\s*", " ", t)                    # 改行 → 空白
    return re.sub(r"[ \t]{2,}", " ", t).strip()


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
            f'<Gather input="speech" language="{LANG}" speechTimeout="{SPEECH_TIMEOUT}" '
            f'enhanced="true" speechModel="{SPEECH_MODEL}" bargeIn="true" '
            f'action="/tac/voice/respond" method="POST">'
            f"{_say(say_text)}"
            "</Gather>"
            '<Redirect method="POST">/tac/voice/respond</Redirect>'
            "</Response>"
        )
    return Response(xml, mimetype="text/xml")


def _twiml_handoff(sid: str, say_text: str) -> Response:
    """ライブ通話を Flex/TaskRouter ワークフローへ転送（実ハンドオフ）。

    AI が組み立てたタスク属性（AI 要約・顧客情報・ルーティング）を付けて
    <Enqueue workflowSid> でキューへ入れ、担当者へ橋渡しする。
    """
    conv = conn.get(sid)
    attrs = (conv.attributes.get("handoff_task_attributes") if conv else None) or {}
    task = html.escape(json.dumps(attrs, ensure_ascii=False))
    line = say_text or "担当者におつなぎします。少々お待ちください。"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"{_say(line)}"
        f'<Enqueue workflowSid="{CONFIG.flex_workflow_sid}">'
        f"<Task>{task}</Task>"
        "</Enqueue>"
        "</Response>"
    )
    return Response(xml, mimetype="text/xml")


@app.route("/tac/voice", methods=["POST", "GET"])
def voice_start():
    sid = request.values.get("CallSid", "anon")
    frm = request.values.get("From", "")
    goal = request.values.get("goal", "")
    conn.start(sid, Channel.VOICE, customer_identity=frm, goal=goal)
    # 第一声は固定。LLM を待たず即座に話し始め、立ち上がりの無音をなくす
    conn.add_agent_line(sid, GREETING)
    return _twiml_gather(GREETING)


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
        if CONFIG.flex_workflow_sid:
            # ライブ通話を Flex ワークフローへ実際に転送（担当者キューへ）
            return _twiml_handoff(sid, result.text)
        # ワークフロー未設定時は締めの一言のみ（従来挙動）
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


# ---------------- ConversationRelay（双方向ストリーミング音声） ----------------
# 話しながら同時に処理でき、割り込み(barge-in)が自然。Twilio が STT/TTS を担い、
# 我々は WebSocket でテキストをやり取りする。<Gather> 方式の /tac/voice とは別系統で、
# 番号の Voice Webhook を /tac/voice-relay に向けると有効になる。
@app.route("/tac/voice-relay", methods=["POST", "GET"])
def voice_relay():
    """ConversationRelay を開始する TwiML を返す（WebSocket へ接続）。"""
    ws_url = f"wss://{request.host}/tac/relay"
    # language を日本語に固定（TTS/STT 既定言語）。voice は両方 env 指定時のみ付与
    # （誤った voice 名は英語フォールバックを招くため、既定は付けない）。
    voice_attr = ""
    if CONFIG.relay_tts_provider and CONFIG.relay_voice:
        voice_attr = (
            f' ttsProvider="{html.escape(CONFIG.relay_tts_provider)}" '
            f'voice="{html.escape(CONFIG.relay_voice)}"'
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<ConversationRelay url="{html.escape(ws_url)}" '
        f'welcomeGreeting="{html.escape(CONFIG.relay_welcome)}" '
        f'language="{LANG}"{voice_attr} interruptible="true" />'
        "</Connect></Response>"
    )
    return Response(xml, mimetype="text/xml")


# flask-sock があれば WebSocket ハンドラを登録（未導入でも HTTP 部分は動く）
try:
    from flask_sock import Sock

    _sock = Sock(app)
except Exception:  # noqa: BLE001
    _sock = None

if _sock is not None:
    @_sock.route("/tac/relay")
    def relay(ws):  # pragma: no cover - WebSocket は実機/結合テスト対象
        """ConversationRelay の WebSocket。setup/prompt/interrupt を処理。"""
        sid = "relay"
        print("[relay] WebSocket connected", flush=True)
        while True:
            raw = ws.receive()
            if raw is None:
                print("[relay] closed", flush=True)
                break
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                continue
            mtype = msg.get("type")
            if mtype == "setup":
                sid = msg.get("callSid") or "relay"
                print(f"[relay] setup sid={sid} from={msg.get('from','')}", flush=True)
                if conn.get(sid) is None:
                    conn.start(sid, Channel.VOICE, customer_identity=msg.get("from", ""))
            elif mtype == "prompt":
                # 確定発話のみ処理（途中経過 last=false はスキップ）
                if not msg.get("last", True):
                    continue
                text = (msg.get("voicePrompt") or "").strip()
                print(f"[relay] prompt={text!r}", flush=True)
                if not text:
                    continue
                if conn.get(sid) is None:
                    conn.start(sid, Channel.VOICE)
                # ストリーミング: 生成しながらトークンを送り、TTS を即座に開始させる
                sent = 0
                for chunk in conn.stream_voice(sid, text):
                    if chunk:
                        ws.send(json.dumps(
                            {"type": "text", "token": chunk, "last": False},
                            ensure_ascii=False,
                        ))
                        sent += 1
                ws.send(json.dumps({"type": "text", "token": "", "last": True},
                                   ensure_ascii=False))
                print(f"[relay] streamed chunks={sent}", flush=True)
                cur = conn.get(sid)
                if cur is not None and cur.status == Status.HANDED_OFF:
                    # ハンドオフは TwiML へ戻して <Enqueue> で担当者へ
                    attrs = cur.attributes.get("handoff_task_attributes") or {}
                    ws.send(json.dumps(
                        {"type": "end", "handoffData": json.dumps(attrs, ensure_ascii=False)},
                        ensure_ascii=False,
                    ))
                    break
            else:
                print(f"[relay] type={mtype} {msg if mtype == 'error' else ''}", flush=True)
                if mtype == "error":
                    break


if __name__ == "__main__":
    # ConversationRelay の WebSocket を開発サーバーで扱うには threaded 必須
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8090")), threaded=True)
