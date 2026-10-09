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

import hmac
import html
import json
import os
import re
import threading
import urllib.parse

from flask import Flask, Response, jsonify, request, send_from_directory

from .config import CONFIG
from .connector import TACConnector
from .models import Channel, Status

app = Flask(__name__)
conn = TACConnector()

# 着信 Webhook として Twilio 署名検証の対象にするパス
_TWILIO_WEBHOOK_PATHS = {
    "/tac/voice",
    "/tac/voice/respond",
    "/tac/voice/status",
    "/tac/message",
    "/tac/voice-relay",
    "/tac/autofollow/dtmf",
}


def _signed_request_url() -> str:
    """Twilio が署名した実際の公開 URL を再構成する（ngrok 裏側対策）。"""
    if CONFIG.public_base_url:
        url = CONFIG.public_base_url.rstrip("/") + request.path
    else:
        proto = request.headers.get("X-Forwarded-Proto", request.scheme)
        host = request.headers.get("X-Forwarded-Host", request.host)
        url = f"{proto}://{host}{request.path}"
    if request.query_string:
        url += "?" + request.query_string.decode()
    return url


@app.before_request
def _enforce_twilio_signature():
    """着信 Webhook の Twilio 署名を検証（設定 ON 時のみ）。不正は 403。"""
    if not CONFIG.verify_twilio_signature or request.path not in _TWILIO_WEBHOOK_PATHS:
        return None
    from .twilio_sig import is_valid

    token = CONFIG.twilio_auth_token
    if not token:
        return ("署名検証が有効ですが TWILIO_AUTH_TOKEN が未設定です。", 503)
    params = request.form.to_dict() if request.method == "POST" else {}
    sig = request.headers.get("X-Twilio-Signature")
    if not is_valid(token, _signed_request_url(), params, sig):
        return ("Twilio signature verification failed", 403)
    return None

VOICE = os.environ.get("TWILIO_VOICE", "Polly.Takumi-Neural")
LANG = os.environ.get("TWILIO_VOICE_LANG", "ja-JP")
SPEECH_MODEL = os.environ.get("TWILIO_SPEECH_MODEL", "experimental_conversations")
# 発話終了の無音待ち（秒）。"auto" は安全だがやや長め。"1" 前後でテンポが上がる
SPEECH_TIMEOUT = os.environ.get("TWILIO_SPEECH_TIMEOUT", "auto")
# 着信時の第一声（固定）。LLM を待たず即座に話し始め、立ち上がりを自然にする
GREETING = os.environ.get(
    "TAC_GREETING", "お電話ありがとうございます。AI音声案内担当、ライフパートナーです。ご用件をうかがいます。"
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
    # 第一声は固定。LLM を待たず即座に話し始め、立ち上がりの無音をなくす。
    # 録音ON時は冒頭に録音同意の告知を前置する。
    from . import consent
    greeting = consent.prefix(GREETING)
    conn.add_agent_line(sid, greeting)
    return _twiml_gather(greeting)


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


# ---------------- 録音完了 Webhook（Twilio が叩く） ----------------
# Twilio の RecordingStatusCallback。録音が完了したら文字起こし＋AI要約を非同期で
# 実行する。Twilio 署名検証は TAC_VERIFY_TWILIO_SIGNATURE ON 時にこのパスも対象。
_TWILIO_WEBHOOK_PATHS.add("/tac/recording-status")


@app.route("/tac/recording-status", methods=["POST"])
def recording_status():
    from . import transcribe

    recording_url = request.values.get("RecordingUrl", "")
    call_sid = request.values.get("CallSid", "")
    status = request.values.get("RecordingStatus", "")
    if status == "completed" and recording_url:
        room = request.values.get("ConferenceSid", "")
        transcribe.process_recording_async(recording_url, call_sid, room)
    return ("", 204)


# ---------------- AMD（留守電判定）コールバック ----------------
_TWILIO_WEBHOOK_PATHS.add("/tac/amd-status")


@app.route("/tac/amd-status", methods=["POST"])
def amd_status():
    from . import autofollow, calllog, idempotency, outbound

    call_sid = request.values.get("CallSid", "")
    answered_by = request.values.get("AnsweredBy", "")
    # 冪等性: 同じ CallSid の AMD を重複受信しても一度しか処理しない
    # （二重の calllog 記録・二重 redirect を防ぐ）。
    if call_sid and idempotency.seen(f"{call_sid}:amd"):
        return ("", 204)
    dec = autofollow.amd_decision(answered_by)
    if dec["machine"] and call_sid:
        to = request.values.get("To", "")
        calllog.append("outbound", to, "amd_machine",
                        call_sid=call_sid, answered_by=answered_by)
        # 留守電/FAXなら即切断（さくらが留守電に喋り続けない・担当を繋がない）
        if dec["hangup"]:
            outbound.redirect_call(call_sid, '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>')
    return ("", 204)


# ---------------- アウトバウンド発信（click-to-call ブリッジ） ----------------
# 相手に発信 → 出たら保留 → あなたの電話が鳴り、出た瞬間に会話開始。
# 1 件ずつ手動発信のみ（一斉自動発信・断った相手への再架電は非対応）。
#
# セキュリティ: この Flask は ngrok 等で公開されるため、認証なしだと第三者が
# 口座課金の発信を勝手に起こせてしまう。操作者トークン（TAC_OUTBOUND_TOKEN）を
# 必須とし、未設定なら発信 API を無効化（fail closed）。GET は許可せず POST のみ
# （リンク/クローラ/埋め込みからの drive-by 発火を防ぐ）。
@app.route("/tac/call", methods=["POST"])
def outbound_call():
    from . import agents, phone
    from .outbound import bridge_call

    ok, err = _check_outbound_token()
    if not ok:
        return err

    raw_to = (request.values.get("to") or "").strip()
    agent_in = (request.values.get("agent") or "").strip() or None
    agent_name = (request.values.get("agent_name") or "").strip() or None
    if not raw_to:
        return jsonify({"ok": False, "error": "パラメータ to が必要です（例: +81901234567）"}), 400
    # 090-1234-5678 のような国内表記も E.164 に直して受け付ける（iPhone アプリ入力用）
    to = phone.to_e164(raw_to)
    if not to:
        return jsonify({"ok": False, "error": f"電話番号として読めません: {raw_to}"}), 400
    # 担当者: 名前/番号で指定されたら名簿から解決、未指定なら名簿をラウンドロビン。
    # 名簿が空なら None（bridge_call が CONFIG.agent_number にフォールバック）。
    if agent_in:
        agent = agents.resolve(agent_in)
        if not agent:
            return jsonify({"ok": False,
                            "error": f"担当者 '{agent_in}' が名簿に見つかりません"}), 400
    else:
        agent = agents.next_agent()
    result = bridge_call(to, agent=agent, agent_name=agent_name)
    result.setdefault("to", to)
    code = 200 if result.get("ok") else 502
    return jsonify(result), code


# iPhone 用 発信アプリ（PWA）。ページ自体は静的で個人情報もトークンも含まないため
# 認証なしで配信し、中から叩く API 側で X-TAC-Token を検証する。
@app.route("/tac/app", methods=["GET"])
def mobile_app_page():
    from . import mobile_app

    return Response(mobile_app.render(), mimetype="text/html",
                    headers={"Cache-Control": "no-cache"})


@app.route("/tac/app/manifest.webmanifest", methods=["GET"])
def mobile_app_manifest():
    from . import mobile_app

    resp = jsonify(mobile_app.manifest())
    resp.mimetype = "application/manifest+json"
    return resp


# ホーム画面アイコン（PWA / apple-touch-icon）。tac/assets/ の PNG をそのまま配信する。
# 個人情報・トークンは含まない静的画像なので認証不要。ファイル名は許可リストで固定。
_ICON_NAMES = {"icon-180.png", "icon-192.png", "icon-512.png", "icon-1024.png"}
_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


@app.route("/tac/app/<name>", methods=["GET"])
def mobile_app_icon(name):
    if name not in _ICON_NAMES:
        return jsonify({"ok": False, "error": "not found"}), 404
    resp = send_from_directory(_ASSETS_DIR, name, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


# 担当者名簿（Agent Roster）。登録済みの担当者を確認する（発信APIと同じトークン認証）。
@app.route("/tac/agents", methods=["GET"])
def agents_list():
    from . import agents

    ok, err = _check_outbound_token()
    if not ok:
        return err
    r = agents.roster()
    return jsonify({"ok": True, "count": len(r), "agents": r})


# go-live プリフライト点検。creds・発信元番号・名簿・安全ゲートを点検し、
# 本番に出して安全か（go_live）を判定する（発信APIと同じトークン認証・秘密は出さない）。
@app.route("/tac/preflight", methods=["GET"])
def preflight_check():
    from . import check

    ok, err = _check_outbound_token()
    if not ok:
        return err
    return jsonify({"ok": True, **check.preflight()})


def _check_outbound_token() -> tuple[bool, tuple]:
    """発信系 API 共通のトークン認証。(ok, エラー応答) を返す。"""
    expected = CONFIG.outbound_token
    if not expected:
        return False, (jsonify({
            "ok": False,
            "error": "この API は無効です。安全のため .env に TAC_OUTBOUND_TOKEN を設定してください。",
        }), 503)
    provided = request.headers.get("X-TAC-Token") or request.values.get("token") or ""
    if not hmac.compare_digest(str(provided).encode("utf-8"), str(expected).encode("utf-8")):
        return False, (jsonify({"ok": False, "error": "認証エラー: 正しい token が必要です。"}), 401)
    return True, (None, 0)


# 架電記録（Call Log）。直近の発信記録を返す（監査証跡・運用可視化）。
@app.route("/tac/calls", methods=["GET"])
def calls():
    from . import calllog

    ok, err = _check_outbound_token()
    if not ok:
        return err
    try:
        limit = max(1, min(int(request.values.get("limit", "50")), 500))
    except ValueError:
        limit = 50
    records = calllog.recent(limit=limit)
    return jsonify({"ok": True, "count": len(records), "calls": records})


# 架電記録の CSV 書き出し。監査提出・月次報告・Excel 集計用。電話番号=個人情報を
# 含むので発信 API と同じトークン認証を必須にし、ブラウザにキャッシュさせない。
# ?from=YYYY-MM-DD&to=YYYY-MM-DD で現地日付（既定 JST）の範囲に絞れる。
@app.route("/tac/calls.csv", methods=["GET"])
def calls_csv():
    from datetime import UTC, date, datetime, timedelta

    from . import calllog, calls_export

    ok, err = _check_outbound_token()
    if not ok:
        return err
    def date_param(name: str) -> date | None:
        value = (request.values.get(name) or "").strip()
        return date.fromisoformat(value) if value else None

    try:
        start, end = date_param("from"), date_param("to")
    except ValueError:
        return jsonify({"ok": False, "error": "from / to は YYYY-MM-DD 形式で指定してください"}), 400
    off = CONFIG.call_hours_utc_offset
    text = calls_export.to_csv(calls_export.in_range(calllog._all(), start, end, off), off)
    today = (datetime.now(UTC) + timedelta(hours=off)).strftime("%Y%m%d")
    return Response(text, mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename=calls_{today}.csv",
        "Cache-Control": "no-store",
    })


# 架電サマリー（Call Summary）。結果別件数などを集計して返す（運用可視化・
# コンプライアンス報告: blocked 件数 = DNC 遵守の証明）。
@app.route("/tac/calls/queue", methods=["GET", "POST"])
def calls_queue():
    from . import queue

    ok, err = _check_outbound_token()
    if not ok:
        return err
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        # まるごと入れ替え（同期用途・重複防止）: ?replace=1 か body {"mode":"replace","items":[...]}
        replace_flag = (request.values.get("replace") or "").strip() in ("1", "true", "yes")
        if isinstance(data, dict) and str(data.get("mode", "")).lower() == "replace":
            items = data.get("items") or []
            n = queue.replace(items)
            return jsonify({"ok": True, "replaced": n})
        if isinstance(data, list):
            if replace_flag:
                n = queue.replace(data)
                return jsonify({"ok": True, "replaced": n})
            queue.add_bulk(data)
            return jsonify({"ok": True, "added": len(data)})
        number = data.get("number") or (request.values.get("number") or "").strip()
        if not number:
            return jsonify({"ok": False, "error": "パラメータ number が必要です"}), 400
        entry = queue.add(
            number=number,
            name=data.get("name", ""),
            area=data.get("area", ""),
            score=int(data.get("score", 0)),
            note=data.get("note", ""),
            folder=data.get("folder", ""),
        )
        return jsonify({"ok": True, "entry": entry})
    sort = (request.values.get("sort") or "").strip()
    q = (request.values.get("q") or "").strip()
    folder_param = request.values.get("folder")
    entries = queue.load(sort=sort, q=q, folder=folder_param)
    return jsonify({"ok": True, "count": len(entries), "queue": entries})


@app.route("/tac/calls/queue/folders", methods=["GET"])
def calls_queue_folders():
    from . import queue

    ok, err = _check_outbound_token()
    if not ok:
        return err
    return jsonify({"ok": True, "folders": queue.folders()})


# 自動フォロー（分類台帳）。Slack/Drive/Sheets 等から読み込んだお客様の記録を
# 内容で分類して貯め、確認済みだけを既存スマートリスト（queue）へ流し込む。
# 既存の発信ガード（DNC・時間帯・1日上限）はそのまま効く。発信系と同じトークン認証。
@app.route("/tac/follow", methods=["GET"])
def follow_list():
    from . import followup

    ok, err = _check_outbound_token()
    if not ok:
        return err
    category = (request.values.get("category") or "").strip()
    entries = followup.load(category=category)
    return jsonify({"ok": True, "count": len(entries),
                    "counts": followup.counts(), "items": entries})


@app.route("/tac/follow/ingest", methods=["POST"])
def follow_ingest():
    from . import followup

    ok, err = _check_outbound_token()
    if not ok:
        return err
    data = request.get_json(silent=True) or {}
    records = data.get("records") if isinstance(data, dict) else data
    if not isinstance(records, list):
        return jsonify({"ok": False, "error": "records(配列)が必要です"}), 400
    overwrite = bool(data.get("overwrite")) if isinstance(data, dict) else False
    result = followup.ingest(records, overwrite=overwrite)
    return jsonify({"ok": True, **result})


@app.route("/tac/follow/correct", methods=["POST"])
def follow_correct():
    from . import followup

    ok, err = _check_outbound_token()
    if not ok:
        return err
    data = request.get_json(silent=True) or {}
    entry_id = str(data.get("id") or (request.values.get("id") or "")).strip()
    category = str(data.get("category") or (request.values.get("category") or "")).strip()
    if not entry_id or not category:
        return jsonify({"ok": False, "error": "id と category が必要です"}), 400
    if not followup.correct(entry_id, category):
        return jsonify({"ok": False, "error": "対象が見つからない／不正なカテゴリ"}), 400
    return jsonify({"ok": True})


@app.route("/tac/follow/promote", methods=["POST"])
def follow_promote():
    from . import followup

    ok, err = _check_outbound_token()
    if not ok:
        return err
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") if isinstance(data, dict) else data
    if not isinstance(ids, list):
        return jsonify({"ok": False, "error": "ids(配列)が必要です"}), 400
    moved = followup.promote([str(i) for i in ids])
    return jsonify({"ok": True, "moved": moved})


# ---------------- 自動フォロー架電エンジン（操作API） ----------------
@app.route("/tac/autofollow/status", methods=["GET"])
def autofollow_status():
    """エンジンの状態（ON/一時停止）＋「次に掛ける1件」のプレビューを返す。"""
    from . import autofollow, followup

    ok, err = _check_outbound_token()
    if not ok:
        return err
    entry, dec = autofollow.select_next(followup.load())
    nxt = None
    if entry is not None:
        nxt = {"name": entry.get("name", ""), "category": entry.get("category", ""),
               "number": entry.get("number", "")}
    from . import autofollow_scheduler
    return jsonify({"ok": True, "engine": autofollow.status(),
                    "next": nxt, "reason": dec.reason,
                    "auto_last": autofollow_scheduler.last_run()})


@app.route("/tac/autofollow/toggle", methods=["POST"])
def autofollow_toggle():
    """エンジンの ON/OFF・一時停止を切り替える。"""
    from . import autofollow

    ok, err = _check_outbound_token()
    if not ok:
        return err
    data = request.get_json(silent=True) or {}
    if "enabled" in data:
        autofollow.set_enabled(bool(data.get("enabled")))
    if "paused" in data:
        autofollow.set_paused(bool(data.get("paused")))
    if "auto" in data:
        autofollow.set_auto(bool(data.get("auto")))
    return jsonify({"ok": True, "engine": autofollow.status()})


@app.route("/tac/autofollow/run", methods=["POST"])
def autofollow_run():
    """次の1件を処理する。既定はプレビュー（発信しない）。

    execute=true かつ エンジンON のときだけ実際に1件発信する（全ガード込み）。
    一斉自動発信ではなく、1リクエストで最大1件。
    """
    from . import autofollow, followup

    ok, err = _check_outbound_token()
    if not ok:
        return err
    data = request.get_json(silent=True) or {}
    execute = bool(data.get("execute")) or request.values.get("execute") in ("1", "true")
    if not execute:
        entry, dec = autofollow.select_next(followup.load())
        return jsonify({"ok": True, "preview": True, "would_place": entry is not None,
                        "reason": dec.reason})
    res = autofollow.run_once()
    return jsonify({"ok": True, "preview": False, **res})


@app.route("/tac/autofollow/run-batch", methods=["POST"])
def autofollow_run_batch():
    """連続オート発信。対象を上から順に、止まらず自動で掛ける（最大 max 件）。

    エンジンが ON でなければ1件も発信しない。全ガードは各発信で再チェック。
    一斉無差別ではなく、同意済みフォロー対象のみ・上限内。
    """
    from . import autofollow

    ok, err = _check_outbound_token()
    if not ok:
        return err
    data = request.get_json(silent=True) or {}
    kwargs = {}
    mx = data.get("max")
    if isinstance(mx, int) and mx > 0:
        kwargs["max_calls"] = mx
    res = autofollow.run_batch(**kwargs)
    return jsonify({"ok": True, **res})


@app.route("/tac/autofollow/dtmf", methods=["POST", "GET"])
def autofollow_dtmf():
    """フォロー架電の IVR 入力（DTMF）を受けて次の音声(TwiML)を返す。

    Twilio からの Webhook。9=連絡不要なら連絡停止＋DNC 登録（副作用）。
    署名検証は _TWILIO_WEBHOOK_PATHS で有効時のみ。
    """
    from . import autofollow, phone

    digit = (request.values.get("Digits") or "").strip()
    raw = (request.values.get("num") or "").strip()
    number = phone.to_e164(raw) or raw
    if digit:
        autofollow.on_dtmf({"number": number}, digit)
    xml = autofollow.twiml_after_dtmf(digit, handoff_number=CONFIG.agent_number)
    return Response(xml, mimetype="text/xml")


_TWILIO_WEBHOOK_PATHS.add("/tac/autofollow/call-status")


@app.route("/tac/autofollow/call-status", methods=["POST", "GET"])
def autofollow_call_status():
    """自動フォロー架電の通話結果(Twilio StatusCallback)を台帳へ反映する。"""
    from . import autofollow, idempotency, phone

    status = (request.values.get("CallStatus") or "").strip()
    call_sid = (request.values.get("CallSid") or "").strip()
    raw = (request.values.get("num") or request.values.get("To") or "").strip()
    number = phone.to_e164(raw) or raw
    # 冪等性: 同じ CallSid+CallStatus の重複/再送で outcome を二重計上しない。
    # 異なる CallStatus（ringing→completed 等）はそれぞれ1回ずつ記録する。
    if call_sid and idempotency.seen(f"{call_sid}:status:{status.lower()}"):
        return ("", 204)
    if number:
        autofollow.register_outcome(number, status)
    return ("", 204)


@app.route("/tac/autofollow/dashboard", methods=["GET"])
def autofollow_dashboard():
    """自動フォローの操作ダッシュボード（HTML）。トークンは端末側で入力。"""
    from . import autofollow_ui

    return Response(autofollow_ui.render(), mimetype="text/html")


@app.route("/tac/calls/note", methods=["POST"])
def calls_note():
    from . import notes, phone

    ok, err = _check_outbound_token()
    if not ok:
        return err
    raw_to = (request.values.get("to") or "").strip()
    note_text = (request.values.get("note") or "").strip()
    if not raw_to:
        return jsonify({"ok": False, "error": "パラメータ to が必要です"}), 400
    to = phone.to_e164(raw_to) or raw_to
    res = notes.save(to, note_text)
    return jsonify(res)


@app.route("/tac/calls/callbacks", methods=["GET"])
def calls_callbacks():
    from . import callbacks

    ok, err = _check_outbound_token()
    if not ok:
        return err
    today_only = (request.values.get("today") or "").strip().lower() in ("1", "true", "yes")
    if today_only:
        result = callbacks.list_today()
    else:
        result = callbacks.list_callbacks()
    return jsonify({"ok": True, "count": len(result), "callbacks": result})


@app.route("/tac/calls/stats", methods=["GET"])
def calls_stats():
    from . import stats

    ok, err = _check_outbound_token()
    if not ok:
        return err
    return jsonify({"ok": True, "stats": stats.aggregate()})


@app.route("/tac/calls/summary", methods=["GET"])
def calls_summary():
    from . import calllog

    ok, err = _check_outbound_token()
    if not ok:
        return err
    return jsonify({"ok": True, "summary": calllog.summary()})


# 日次発信サマリ。当日(JST)の発信/ブロック/成約/ユニーク番号＋重複Webはじき数。
# ?day=YYYY-MM-DD で任意の日も指定可（発信APIと同じトークン認証）。
@app.route("/tac/calls/daily", methods=["GET"])
def calls_daily():
    from . import stats

    ok, err = _check_outbound_token()
    if not ok:
        return err
    day = (request.values.get("day") or "").strip() or None
    return jsonify({"ok": True, "summary": stats.daily_summary(day=day)})


# 成約確度ランキング（当社の勝ち筋ベース）。自動フォロー台帳を成約見込みの高い順に
# 並べ、説明可能な根拠(reasons)付きで返す。拒否(除外)は落とす。電話番号は出さない。
@app.route("/tac/winscore", methods=["GET"])
def winscore_rank():
    from . import followup, winscore

    ok, err = _check_outbound_token()
    if not ok:
        return err
    leads = followup.load()
    ranked = winscore.prioritize(leads, include_excluded=False)
    top = [{
        "name": x.get("name", ""),
        "area": x.get("area", ""),
        "category": x.get("category", ""),
        "win": x["_win"],
    } for x in ranked]
    return jsonify({"ok": True, "count": len(top), "ranked": top})


# AI要約付き架電記録。insightレコードをcall_sid/roomで紐付けて返す。
@app.route("/tac/calls/insight", methods=["GET"])
def calls_insight():
    from . import calllog

    ok, err = _check_outbound_token()
    if not ok:
        return err
    to_filter = (request.values.get("to") or "").strip()
    records = calllog._all()
    insights = {}
    for rec in records:
        if rec.get("status") == "insight":
            key = rec.get("call_sid") or rec.get("room") or ""
            if key:
                insights[key] = rec
    calls = [r for r in records if r.get("status") in ("dialed", "disposition")]
    if to_filter:
        calls = [r for r in calls if r.get("to") == to_filter]
    calls = calls[-50:]
    calls.reverse()
    for c in calls:
        key = c.get("call_sid") or c.get("room") or ""
        ins = insights.get(key)
        if ins:
            c["ai_summary"] = ins.get("summary", "")
            c["ai_temperature"] = ins.get("temperature", "")
            c["ai_next_action"] = ins.get("next_action", "")
    return jsonify({"ok": True, "count": len(calls), "calls": calls})


# AIが設計する発信順ランキング。followup台帳＋カテゴリスコア＋温度感＋新しさでソート。
@app.route("/tac/calls/ranked", methods=["GET"])
def calls_ranked():
    from . import calllog, followup

    ok, err = _check_outbound_token()
    if not ok:
        return err
    entries = followup.load()
    records = calllog._all()
    temp_map: dict[str, str] = {}
    for rec in records:
        if rec.get("status") == "insight" and rec.get("temperature"):
            to = rec.get("to") or ""
            if to:
                temp_map[to] = rec["temperature"]

    TEMP_SCORE = {"高": 30, "中": 15, "低": 0}
    CAT_SCORE = {"再調整希望": 90, "日程返答待ち": 80, "不在": 70, "要確認": 40, "連絡停止": 0}
    ranked = []
    for e in entries:
        if not followup.can_follow(e):
            continue
        score = CAT_SCORE.get(e.get("category", ""), 40)
        temp = temp_map.get(e.get("number", ""), "")
        score += TEMP_SCORE.get(temp, 5)
        updated = e.get("updated_at", "")
        if updated:
            score += 10
        ranked.append({
            "number": e.get("number", ""),
            "name": e.get("name", ""),
            "area": e.get("area", ""),
            "category": e.get("category", ""),
            "score": score,
            "temperature": temp,
            "next_action": e.get("next_action", ""),
            "follow_count": e.get("follow_count", 0),
        })
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return jsonify({"ok": True, "count": len(ranked), "ranked": ranked})


# 運用ダッシュボード（HTML Console）。架電記録・サマリー・DNC をブラウザで一覧。
# 電話番号=個人情報を表示するため、発信 API と同じトークン認証を必須にする。
@app.route("/tac/console", methods=["GET"])
def console_page():
    from . import calllog, console, dnc, screening_log

    ok, err = _check_outbound_token()
    if not ok:
        return err
    page = console.render(
        summary=calllog.summary(),
        calls=calllog.recent(limit=100),
        dnc_numbers=dnc.all(),
        screenings=screening_log.recent(limit=100) if CONFIG.screening_enabled else None,
    )
    return Response(page, mimetype="text/html")


# 通話結果ラベル（Disposition）＋ DNC ワンクリック登録。
# 結果を記録し、「拒否」なら自動で DNC 登録（再勧誘防止）。POST のみ・トークン必須。
@app.route("/tac/calls/disposition", methods=["POST"])
def calls_disposition():
    from . import disposition

    ok, err = _check_outbound_token()
    if not ok:
        return err
    from . import phone

    raw_to = (request.values.get("to") or "").strip()
    result = (request.values.get("result") or "").strip()
    if not raw_to:
        return jsonify({"ok": False, "error": "パラメータ to が必要です"}), 400
    # 発信時と同じ E.164 にそろえる（DNC の照合がずれないように）
    to = phone.to_e164(raw_to) or raw_to
    # dnc パラメータ: 未指定=自動判定、明示 true/false で上書き
    dnc_param = request.values.get("dnc")
    add_dnc = None
    if dnc_param is not None:
        add_dnc = str(dnc_param).strip().lower() in ("1", "true", "yes", "on")
    callback_at = (request.values.get("callback_at") or "").strip() or None
    res = disposition.record(to, result, add_dnc=add_dnc, callback_at=callback_at)
    return jsonify(res)


# ---------------- 生活意識調査モード「ライフパートナー」（tac/survey.py） ----------------
# 金融リテラシー・保険の見直しの意識調査。決まった質問を Twilio の音声認識（<Gather input="speech">）で聞き、
# 同意・撤回・拒否はサーバーのルールで判定する（LLM を使わない）。既定 OFF（TAC_SURVEY_ENABLED）。
_SURVEY_SESSIONS: dict[str, dict] = {}
_SURVEY_LOCK = threading.Lock()
_TWILIO_WEBHOOK_PATHS.add("/tac/survey/voice")
_TWILIO_WEBHOOK_PATHS.add("/tac/survey/step")


def _survey_action_url(number: str) -> str:
    base = (CONFIG.public_base_url or "").rstrip("/")
    return f"{base}/tac/survey/step?num={urllib.parse.quote(number)}"


@app.route("/tac/survey/call", methods=["POST"])
def survey_call():
    """調査の電話を 1 件だけ掛ける（トークン必須）。対象者リストと可否判定を通ったときだけ。"""
    from . import calllog, outbound, survey, survey_store

    ok, err = _check_outbound_token()
    if not ok:
        return err
    number = (request.values.get("number") or "").strip()
    if not CONFIG.survey_enabled:
        return jsonify({"ok": False, "reason": "DISABLED"}), 422
    entry = survey.find_entry(number)
    if entry is None:
        return jsonify({"ok": False, "reason": "NOT_ON_SURVEY_LIST"}), 422
    allowed, reason = survey.can_call(entry)
    if not allowed:
        calllog.append("outbound", entry["number"], "blocked", reason=f"survey:{reason}", feature="survey")
        return jsonify({"ok": False, "reason": reason}), 422
    base = (CONFIG.public_base_url or "").rstrip("/")
    if not base:
        return jsonify({"ok": False, "reason": "PUBLIC_BASE_URL_MISSING"}), 422
    voice_url = html.escape(f"{base}/tac/survey/voice?num={urllib.parse.quote(entry['number'])}", quote=True)
    twiml = ('<?xml version="1.0" encoding="UTF-8"?><Response>'
             f'<Redirect method="POST">{voice_url}</Redirect></Response>')
    survey_store.record_attempt(entry["number"])
    res = outbound._create_call(to=entry["number"], twiml=twiml, from_=CONFIG.survey_caller_id)
    if res.get("ok"):
        calllog.append("outbound", entry["number"], "dialed", feature="survey")
    return jsonify({"ok": bool(res.get("ok")), "sid": res.get("sid", "")}), (200 if res.get("ok") else 502)


@app.route("/tac/survey/voice", methods=["POST"])
def survey_voice():
    """相手が出たら：冒頭の説明（AI・事業者・目的・任意）と、調査への協力のお願い。"""
    from . import survey

    number = (request.values.get("num") or "").strip()
    sid = (request.values.get("CallSid") or "").strip()
    s = survey.new_session(number, sid)
    reply = survey.opening(s)
    with _SURVEY_LOCK:
        _SURVEY_SESSIONS[sid] = s
    return Response(survey.twiml(reply, _survey_action_url(number)), mimetype="text/xml")


@app.route("/tac/survey/step", methods=["POST"])
def survey_step():
    """音声認識の結果を受けて次へ進む。終わったら記録し、DNC・撤回を反映する。発話の原文は保存しない。"""
    from . import dnc, survey, survey_store

    sid = (request.values.get("CallSid") or "").strip()
    number = (request.values.get("num") or "").strip()
    said = "" if request.values.get("silence") else (request.values.get("SpeechResult") or "")
    with _SURVEY_LOCK:
        s = _SURVEY_SESSIONS.get(sid)
    if s is None or s.get("number") != number:
        # 再起動などで会話の状態が無い：何も記録せず、丁寧に切る
        bye = survey.Reply(say="お電話が途切れてしまい、申し訳ございません。失礼いたします。", end=True)
        return Response(survey.twiml(bye, ""), mimetype="text/xml")
    reply = survey.advance(s, said)
    if reply.end:
        with _SURVEY_LOCK:
            _SURVEY_SESSIONS.pop(sid, None)
        if ("dnc",) in reply.actions:
            dnc.add(number)
        survey_store.save_session(s)
    return Response(survey.twiml(reply, _survey_action_url(number)), mimetype="text/xml")


@app.route("/tac/survey/summary", methods=["GET"])
def survey_summary():
    from . import survey_store

    ok, err = _check_outbound_token()
    if not ok:
        return err
    return jsonify({"ok": True, "summary": survey_store.summary()})


@app.route("/tac/survey/handoffs", methods=["GET"])
def survey_handoffs():
    """保険の案内に同意した人の一覧（登録済みの保険代理店の担当者が後日電話する）。"""
    from . import survey_store

    ok, err = _check_outbound_token()
    if not ok:
        return err
    return jsonify({"ok": True, "handoffs": survey_store.handoffs()})


@app.route("/tac/survey/withdraw", methods=["POST"])
def survey_withdraw():
    """同意の撤回（電話・窓口での申し出）。回答と同意を消す。"""
    from . import survey_store

    ok, err = _check_outbound_token()
    if not ok:
        return err
    number = (request.values.get("number") or "").strip()
    return jsonify({"ok": survey_store.withdraw(number)})


# 緊急停止スイッチ。engaged=true で、再起動なしに以後の全発信（手動・自動フォロー・調査）を止める。
@app.route("/tac/kill-switch", methods=["GET", "POST"])
def kill_switch_api():
    from . import kill_switch

    ok, err = _check_outbound_token()
    if not ok:
        return err
    if request.method == "GET":
        return jsonify({"ok": True, **kill_switch.status()})
    raw = (request.values.get("engaged") or "").strip().lower()
    if raw not in ("true", "false"):
        return jsonify({"ok": False, "error": "パラメータ engaged は true / false のどちらかです"}), 400
    actor = (request.values.get("actor") or "api").strip()[:40]
    reason = (request.values.get("reason") or "").strip()[:200]
    if raw == "true":
        kill_switch.engage(actor=actor, reason=reason)
    else:
        kill_switch.release(actor=actor, reason=reason)
    return jsonify({"ok": True, **kill_switch.status()})


# DNC（発信禁止リスト）管理。断られた相手を登録し、以後は発信をブロックする。
@app.route("/tac/dnc", methods=["GET", "POST"])
def dnc_manage():
    from . import dnc

    ok, err = _check_outbound_token()
    if not ok:
        return err
    if request.method == "GET":
        return jsonify({"ok": True, "count": len(dnc.all()), "numbers": dnc.all()})
    action = (request.values.get("action") or "add").strip().lower()
    number = (request.values.get("number") or "").strip()
    if not number:
        return jsonify({"ok": False, "error": "パラメータ number が必要です"}), 400
    if action == "remove":
        changed = dnc.remove(number)
    else:
        changed = dnc.add(number)
    return jsonify({"ok": True, "action": action, "number": number, "changed": changed})


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
    from . import consent
    welcome = consent.prefix(CONFIG.relay_welcome)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<ConversationRelay url="{html.escape(ws_url)}" '
        f'welcomeGreeting="{html.escape(welcome)}" '
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


# 常駐オート運転（無人スケジューラ）。CONFIG.autofollow_scheduler が ON のときだけ
# バックグラウンドで起動する（既定OFF＝テスト/通常は起動しない）。実発信は runtime の
# 「自動運転(auto)」＋エンジン enabled が ON の時だけ。多重起動防止は start() 側。
# ※ 複数ワーカー構成では各プロセスで起動しうるため、本番は単一プロセス運用を推奨。
if CONFIG.autofollow_scheduler:
    from . import autofollow_scheduler as _afs

    _afs.start()


if __name__ == "__main__":
    # ConversationRelay の WebSocket を開発サーバーで扱うには threaded 必須
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8090")), threaded=True)
