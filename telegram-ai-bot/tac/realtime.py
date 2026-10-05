"""Speech-to-Speech リアルタイム音声オペレーター「さくら」（最先端・音声ネイティブ）。

Twilio Media Streams（生の μ-law 8kHz 音声）と OpenAI Realtime API を
WebSocket でブリッジする。STT→LLM→TTS の変換を挟まず音声を直接やり取りするため、
遅延が極小で、相づち・感情・割り込み（barge-in）が人間レベルになる。

構成:
  電話 → Twilio → <Connect><Stream> → このサーバー(/tac/media-stream)
       ↕（μ-law 音声をそのまま中継・g711_ulaw で無変換）
  OpenAI Realtime（音声ネイティブモデル）

設計方針（「いちばん会話が成立する」ために）:
  1. 低遅延   : g711_ulaw を無変換で中継
  2. ターン   : server_VAD を電話向けにチューニング（長めの無音・控えめ閾値）
  3. 聞き取り : 日本語を明示した文字起こしを有効化
  4. 割り込み : 相手が話し出したら即停止し、どこまで聞こえたかを truncate で正確に伝える
  5. 人格     : 「恐れ入ります」で終わらせず、必ず一歩踏み込んで会話を前に進める

純粋関数（build_*/on_*）に切り出してあり、音声I/O無しで単体テストできる。

依存: fastapi, uvicorn[standard], websockets
起動: uvicorn tac.realtime:app --host 0.0.0.0 --port 8090
番号の Voice Webhook を /tac/voice-stream に向ける。

※ 頭脳は OpenAI Realtime。要 OPENAI_API_KEY（Realtime 利用可の有料アカウント）。
"""

from __future__ import annotations

import asyncio
import base64
import html
import json
import os
import urllib.parse
import urllib.request

import websockets
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse

from .config import CONFIG

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
REALTIME_MODEL = os.environ.get("TAC_REALTIME_MODEL", "gpt-realtime")
# OpenAI Realtime の音声（alloy / echo / shimmer / marin / cedar 等）
REALTIME_VOICE = os.environ.get("TAC_REALTIME_VOICE", "marin")

# --- 担当者への生転送（ライブハンドオフ） ------------------------------
# お客様が「人と話したい／担当に代わって」と望んだら、さくらが transfer_to_agent を
# 呼び、通話を担当者の電話へ <Dial> で引き継ぐ（Twilio REST で通話を更新）。
AGENT_NUMBER = os.environ.get("TAC_AGENT_NUMBER", "")
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
HANDOFF_VOICE = os.environ.get("TWILIO_VOICE", "Polly.Takumi-Neural")
HANDOFF_LANG = os.environ.get("TWILIO_VOICE_LANG", "ja-JP")

# --- 電話向け会話チューニング（環境変数で微調整可） ---------------------
# 無音判定[ms]: 長いほど相手が話し終わるまで待つ（短すぎると途中で切って聞き返す）
VAD_SILENCE_MS = int(os.environ.get("TAC_VAD_SILENCE_MS", "700"))
# 発話検知の閾値: 低いほど小さい声も拾う（電話回線のノイズ下では控えめが有利）
VAD_THRESHOLD = float(os.environ.get("TAC_VAD_THRESHOLD", "0.45"))
# 第一声の前に含める音声[ms]（頭切れ防止）
VAD_PREFIX_MS = int(os.environ.get("TAC_VAD_PREFIX_MS", "300"))
# 応答の自由度（高いほど表現豊か・低いほど安定）
TEMPERATURE = float(os.environ.get("TAC_REALTIME_TEMPERATURE", "0.8"))

app = FastAPI()


# ======================================================================
# 人格・方針（システム指示）
# ======================================================================
def build_instructions() -> str:
    """エージェントの人格・方針（システム指示）。さくら＋御社情報。"""
    base = CONFIG.persona if CONFIG.persona else ""
    s = base + (
        "\n\n# あなたの役割\n"
        "あなたは株式会社MartialArtsの電話受付AI「さくら」。"
        "一流ホテルのコンシェルジュのように、明るく、気が利いて、頼れる存在です。\n\n"
        "# 話し方\n"
        "・常に自然な日本語の話し言葉。硬すぎず、人間らしい温かいトーン。\n"
        "・1回の発話は基本1〜2文で短く。相手に喋らせる“間”を大切にする。\n"
        "・相手の言葉に必ず具体的に反応する。『恐れ入ります』『確認します』だけで終わらせない。"
        "必ず一歩踏み込み、要点を言い換えて確認したり、次の質問や提案を返す。\n"
        "・お名前・ご連絡先・ご用件を会話の流れで自然に聞き出し、聞いた内容は復唱して確認する。\n"
        "・相手が急いでいそう／不機嫌なときは、先回りして要点を短くまとめる。雑談にも気さくに応じる。\n\n"
        "# 聞き取れなかったとき\n"
        "・無言になったり『恐れ入ります』を繰り返したりしない。\n"
        "・一度だけ、短く具体的に聞き返す。例『お電話が少し遠いようです。もう一度だけ、ご用件をうかがえますか？』\n"
        "・固有名詞や番号が怪しいときは、聞き取れた範囲を復唱して『〜で合っていますか？』と確認する。\n\n"
        "# 会話の進め方\n"
        "1. まず用件をしっかり聴き、相手が何を求めているかを言葉にして確認する。\n"
        "2. こちらで分かることは具体的に答える。日時・場所・手順などは明確に伝える。\n"
        "3. 不動産の売却・買取・購入の相談は、担当者におつなぎするか折り返しを手配すると伝え、"
        "ご都合の良い時間帯と連絡先を確認する。\n"
        "4. 相手が『人と話したい』『担当に代わって』と言ったら、"
        "『ただ今おつなぎします』と一言添えてから、必ず transfer_to_agent を呼んで担当者に生転送する。\n\n"
        "# 必ず守ること\n"
        "・最初に『お電話ありがとうございます、株式会社MartialArtsのAI受付さくらです』と名乗る。\n"
        "・金額・利回り・融資の可否・審査結果など、確定的な数字や判断は断定しない"
        "（『担当者が詳しくご案内します』と取り次ぐ）。\n"
        "・社内の財務情報や他のお客様の情報は一切話さない。\n"
        "・分からないことは正直に『確認いたします』と伝え、推測で断定しない。\n"
        "・しつこい勧誘はしない。相手がお断りの意思を示したら丁寧に通話を終える。"
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


# 後方互換のエイリアス
def _instructions() -> str:
    return build_instructions()


def build_greeting_response(mode: str = "", name: str = "") -> dict:
    """開口一番の response.create。

    mode="followup"（自動フォローの折り返し発信）なら、こちらから掛けた前提で
    お客様名を添えて自然に切り出す。それ以外（着信）は受付の挨拶。
    """
    if mode == "followup":
        who = f"{name}さま" if name else "お客様"
        txt = (
            "まず明るく、やわらかい声で"
            f"『お世話になっております。株式会社MartialArtsのさくらと申します。"
            f"{who}、先日のお約束のその後について、確認のお電話でございます。"
            "今、少しだけお話してもよろしいでしょうか？』"
            "と自然に切り出して、相手の返事を待って。"
        )
    else:
        txt = (
            "まず明るく『お電話ありがとうございます、株式会社MartialArtsのAI受付さくらです。"
            "本日はどういったご用件でしょうか？』と自然に挨拶して、相手の話を待って。"
        )
    return {"type": "response.create", "response": {"instructions": txt}}


def transfer_tool() -> dict:
    """「担当者に代わる」ための function tool 定義（OpenAI Realtime）。"""
    return {
        "type": "function",
        "name": "transfer_to_agent",
        "description": (
            "お客様が『人と話したい』『担当者に代わってほしい』『今つないで』など、"
            "その場で担当者（人間）と話すことを望んだときに呼ぶ。折り返しで良い場合は呼ばない。"
        ),
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "取次ぎ理由の要約"}},
            "required": [],
        },
    }


def build_session_config(instructions: str, voice: str) -> dict:
    """session.update ペイロード。会話品質の心臓部。

    - g711_ulaw を入出力とも使い Twilio と無変換で中継（低遅延）
    - server_VAD を電話向けにチューニング（長めの無音・控えめ閾値・自動応答/自動中断）
    - 日本語を明示した文字起こしで聞き取り精度を底上げ
    """
    return {
        "type": "session.update",
        "session": {
            "turn_detection": {
                "type": "server_vad",
                "threshold": VAD_THRESHOLD,
                "prefix_padding_ms": VAD_PREFIX_MS,
                "silence_duration_ms": VAD_SILENCE_MS,
                "create_response": True,
                "interrupt_response": True,
            },
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "input_audio_transcription": {"model": "whisper-1", "language": "ja"},
            "voice": voice,
            "instructions": instructions,
            "modalities": ["audio", "text"],
            "temperature": TEMPERATURE,
            "tools": [transfer_tool()],
            "tool_choice": "auto",
        },
    }


def build_twiml(host: str) -> str:
    """双方向 Media Stream を開始する TwiML。"""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<Stream url="wss://{host}/tac/media-stream" />'
        "</Connect></Response>"
    )


# ======================================================================
# イベント変換（純粋ロジック・音声I/O非依存＝単体テスト可能）
# ======================================================================
def new_state() -> dict:
    """1通話ぶんの状態。"""
    return {
        "stream_sid": "",
        "latest_media_ts": 0,       # Twilio から届いた最新音声の時刻[ms]
        "response_start_ts": None,  # さくらの応答再生が始まった時刻[ms]
        "last_assistant_item": None,  # いま再生中の応答アイテムID（barge-in 用）
        "mode": "",                 # start の customParameters（followup 等）
        "customer_name": "",
        "greeted": False,           # 開口一番を送ったか
        "call_sid": "",             # Twilio CallSid（担当者への生転送に使う）
    }


def on_twilio_event(data: dict, state: dict) -> tuple[list[tuple[str, dict]], bool]:
    """Twilio からのイベントを処理。(送信コマンド列, 通話終了か) を返す。"""
    out: list[tuple[str, dict]] = []
    ev = data.get("event")
    if ev == "start":
        st = data["start"]
        state["stream_sid"] = st["streamSid"]
        state["call_sid"] = st.get("callSid", "")
        params = st.get("customParameters") or {}
        state["mode"] = params.get("mode", "")
        state["customer_name"] = params.get("customer_name", "")
        # 開口一番はここで（customParameters を反映した挨拶）送る。二重送信しない。
        if not state.get("greeted"):
            state["greeted"] = True
            out.append(("openai", build_greeting_response(state["mode"], state["customer_name"])))
    elif ev == "media":
        m = data["media"]
        ts = m.get("timestamp")
        if ts is not None:
            state["latest_media_ts"] = int(ts)
        out.append(("openai", {"type": "input_audio_buffer.append", "audio": m["payload"]}))
    elif ev == "stop":
        return out, True
    return out, False


def _handle_barge_in(state: dict) -> list[tuple[str, dict]]:
    """相手が話し出した → 再生中の応答を正確に中断する。"""
    out: list[tuple[str, dict]] = []
    sid = state.get("stream_sid")
    item = state.get("last_assistant_item")
    if not (sid and item):
        return out  # 誰も喋っていなければ何もしない（無駄な cancel を避ける）
    elapsed = max(0, state.get("latest_media_ts", 0) - (state.get("response_start_ts") or 0))
    # モデルに「ここまでしか聞こえていない」と伝える（続きの記憶を消す）
    out.append((
        "openai",
        {
            "type": "conversation.item.truncate",
            "item_id": item,
            "content_index": 0,
            "audio_end_ms": elapsed,
        },
    ))
    # Twilio 側のバッファ済み音声を破棄して即無音に
    out.append(("twilio", {"event": "clear", "streamSid": sid}))
    # 生成中の応答を止める
    out.append(("openai", {"type": "response.cancel"}))
    state["last_assistant_item"] = None
    state["response_start_ts"] = None
    return out


def on_openai_event(evt: dict, state: dict) -> list[tuple[str, dict]]:
    """OpenAI Realtime からのイベントを処理し、送信コマンド列を返す。"""
    out: list[tuple[str, dict]] = []
    t = evt.get("type")
    sid = state.get("stream_sid")

    if t == "response.audio.delta" and sid:
        if state.get("response_start_ts") is None:
            state["response_start_ts"] = state.get("latest_media_ts", 0)
        item = evt.get("item_id")
        if item:
            state["last_assistant_item"] = item
        out.append((
            "twilio",
            {"event": "media", "streamSid": sid, "media": {"payload": evt["delta"]}},
        ))
    elif t == "input_audio_buffer.speech_started":
        out.extend(_handle_barge_in(state))
    elif t == "response.function_call_arguments.done" and evt.get("name") == "transfer_to_agent":
        # さくらが「担当に代わる」と判断 → 通話を担当者へ生転送する
        out.append(("transfer", {"call_sid": state.get("call_sid", "")}))
    return out


def transfer_twiml(agent_number: str) -> str:
    """担当者へつなぐ TwiML（生転送用）。"""
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'<Say voice="{HANDOFF_VOICE}" language="{HANDOFF_LANG}">'
        "担当者におつなぎします。少々お待ちください。</Say>"
        f"<Dial>{html.escape(agent_number)}</Dial></Response>"
    )


def redirect_call(call_sid: str, twiml: str) -> dict:
    """Twilio REST で進行中の通話を新しい TwiML に差し替える（担当者へDial）。"""
    if not (call_sid and TWILIO_SID and TWILIO_TOKEN):
        return {"ok": False, "error": "call_sid/Twilio認証が不足"}
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Calls/{call_sid}.json"
    data = urllib.parse.urlencode({"Twiml": twiml}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    auth = base64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {"ok": True, "status": resp.status}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


# ======================================================================
# FastAPI エンドポイント
# ======================================================================
@app.api_route("/tac/voice-stream", methods=["GET", "POST"])
async def voice_stream(request: Request) -> HTMLResponse:
    """双方向 Media Stream を開始する TwiML を返す。"""
    return HTMLResponse(content=build_twiml(request.url.hostname), media_type="text/xml")


@app.get("/")
async def health() -> dict:
    return {
        "ok": True,
        "service": "tac-realtime",
        "model": REALTIME_MODEL,
        "vad": {"silence_ms": VAD_SILENCE_MS, "threshold": VAD_THRESHOLD},
    }


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
        await oa_ws.send(json.dumps(build_session_config(build_instructions(), REALTIME_VOICE)))
        # 開口一番は start イベント受信時に送る（customParameters で挨拶を出し分けるため）。

        state = new_state()

        async def twilio_to_openai() -> None:
            try:
                while True:
                    data = json.loads(await twilio_ws.receive_text())
                    cmds, stop = on_twilio_event(data, state)
                    for dest, payload in cmds:
                        if dest == "openai":
                            await oa_ws.send(json.dumps(payload))
                    if stop:
                        break
            except Exception:
                pass

        async def openai_to_twilio() -> None:
            try:
                async for raw in oa_ws:
                    for dest, payload in on_openai_event(json.loads(raw), state):
                        if dest == "twilio":
                            await twilio_ws.send_text(json.dumps(payload))
                        elif dest == "transfer":
                            # 担当者へ生転送（Twilio REST で通話を差し替え）→ ブリッジ終了
                            await asyncio.to_thread(
                                redirect_call, payload.get("call_sid", ""),
                                transfer_twiml(AGENT_NUMBER),
                            )
                            break
                        else:
                            await oa_ws.send(json.dumps(payload))
            except Exception:
                pass

        await asyncio.gather(twilio_to_openai(), openai_to_twilio())
