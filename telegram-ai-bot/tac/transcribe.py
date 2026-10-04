"""通話録音の文字起こし＋AI要約。

録音完了 Webhook（/tac/recording-status）から呼ばれ:
  1. Twilio から録音音声(mp3)をダウンロード
  2. OpenAI Whisper API で文字起こし
  3. Anthropic Claude で「要点・温度感・次アクション」を JSON 要約
  4. calllog に insight として追記

キー未設定時は安全にスキップ（「要約なし」）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import base64
import threading

from .config import CONFIG


def process_recording(recording_url: str, call_sid: str, room: str = "") -> dict:
    """録音を文字起こし→AI要約してcalllogに保存する。"""
    from . import calllog

    if not recording_url:
        return {"ok": False, "reason": "no_recording_url"}

    transcript = _transcribe(recording_url)
    if not transcript:
        calllog.append("outbound", "", "insight",
                        call_sid=call_sid, room=room,
                        transcript="", summary="", temperature="",
                        next_action="", reason="transcribe_failed")
        return {"ok": False, "reason": "transcribe_failed"}

    insight = _summarize(transcript)
    calllog.append("outbound", "", "insight",
                    call_sid=call_sid, room=room,
                    transcript=transcript,
                    summary=insight.get("summary", ""),
                    temperature=insight.get("temperature", ""),
                    next_action=insight.get("next_action", ""))
    return {"ok": True, "call_sid": call_sid, "transcript": transcript, **insight}


def _transcribe(recording_url: str) -> str:
    """OpenAI Whisper API で文字起こし。キー未設定なら空文字。"""
    api_key = CONFIG.openai_key
    if not api_key:
        return ""

    mp3_url = recording_url.rstrip("/") + ".mp3"
    try:
        audio_data = _download_recording(mp3_url)
    except Exception:
        return ""
    if not audio_data:
        return ""

    boundary = "----TACBoundary9k3m"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\n'
        f"{CONFIG.transcribe_model}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="language"\r\n\r\n'
        f"ja\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="recording.mp3"\r\n'
        f"Content-Type: audio/mpeg\r\n\r\n"
    ).encode() + audio_data + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
        return (result.get("text") or "").strip()
    except Exception:
        return ""


def _summarize(transcript: str) -> dict:
    """Anthropic Claude で要約。キー未設定なら空の辞書。"""
    api_key = CONFIG.anthropic_key
    if not api_key or not transcript:
        return {}

    prompt = (
        "以下は電話通話の文字起こしです。JSON形式で以下を返してください:\n"
        '{"summary": "要点を2〜3行で", '
        '"temperature": "高/中/低（お客様の購買意欲・関心度）", '
        '"next_action": "次にやるべきこと1行"}\n\n'
        f"文字起こし:\n{transcript[:3000]}"
    )

    payload = json.dumps({
        "model": CONFIG.operator_model,
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}],
    }, ensure_ascii=False).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        method="POST",
    )
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
        text = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        return {"summary": text.strip()}
    except Exception:
        return {}


def _download_recording(url: str) -> bytes:
    """Twilio から録音ファイルをダウンロード（Basic認証付き）。"""
    req = urllib.request.Request(url, method="GET")
    if CONFIG.twilio_account_sid and CONFIG.twilio_auth_token:
        auth = base64.b64encode(
            f"{CONFIG.twilio_account_sid}:{CONFIG.twilio_auth_token}".encode()
        ).decode()
        req.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def process_recording_async(recording_url: str, call_sid: str, room: str = "") -> None:
    """非同期（バックグラウンドスレッド）で録音処理する。Webhook応答を遅延させない。"""
    t = threading.Thread(
        target=process_recording,
        args=(recording_url, call_sid, room),
        daemon=True,
    )
    t.start()
