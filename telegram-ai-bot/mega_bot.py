"""最強 Telegram ボット v4 (Claude 統合・なんでも作れる).

機能:
- 💬 Claude チャット（文脈記憶）+ ⚡ ストリーミング表示
- 🌐 ウェブ検索（最新情報を自動取得）
- 🏭 ファイル生成（コードを書いて実行し、グラフ/画像/Word/Excel/PPT/PDF/CSV/
  コード等を作って自動送信）
- 🧠 長期記憶（あなたの事実・好みを自分で判断して保存。再起動後も記憶）
- ⏰ 自動スケジュール（定時にウェブ検索して自動送信）
- 🖼 画像 / 📄 PDF・文書 / 🎤 音声メッセージ
- 🛠 /code で Claude Code 操作
- 🛡 Conflict 根絶（ロック＋webhook削除＋ハンドラ）
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import html
import io
import json
import logging
import os
import signal
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque

from anthropic import AsyncAnthropic
from telegram import Update, constants
from telegram.error import Conflict
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

try:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    _CC = True
except Exception:
    _CC = False

try:
    from faster_whisper import WhisperModel

    _WHISPER = True
except Exception:
    _WHISPER = False

try:
    import twilio  # noqa: F401

    _TWILIO = True
except Exception:
    _TWILIO = False

# --------------------------------------------------------------------------- #
# 設定
# --------------------------------------------------------------------------- #

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")
EFFORT = os.environ.get("CLAUDE_EFFORT", "high")  # v3: 既定を賢く
MAXTOK = int(os.environ.get("CLAUDE_MAX_TOKENS", "4096"))
TURNS = int(os.environ.get("HISTORY_TURNS", "12"))
WEB_SEARCH = os.environ.get("WEB_SEARCH", "1") not in ("0", "false", "False", "")
CODE_EXEC = os.environ.get("CODE_EXEC", "1") not in ("0", "false", "False", "")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
# 画像とみなす拡張子
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")

DATA_DIR = Path(os.environ.get("BOT_DATA_DIR", str(Path.home() / ".telegram-mega-bot")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEM_PATH = DATA_DIR / "memory.json"
SCHED_PATH = DATA_DIR / "schedules.json"
CALL_SCHED_PATH = DATA_DIR / "call_schedules.json"
PROACTIVE_PATH = DATA_DIR / "proactive.json"

SYS = os.environ.get(
    "SYSTEM_PROMPT",
    "あなたは Telegram 上で動く親切で有能なAIアシスタントです。"
    "最新情報が必要なときは web_search を使い、簡潔で分かりやすく、"
    "ユーザーの言語に合わせて回答します。"
    "会話からユーザーの名前・好み・繰り返し役立つ重要な事実を学んだら、"
    "save_memory ツールで保存してください（長期的に有用なものだけ。"
    "一時的な雑談や些細な内容は保存しない）。"
    "グラフ・図・画像・Word(.docx)・Excel(.xlsx)・PowerPoint(.pptx)・PDF・"
    "CSV・コードなどファイルの作成を求められたら、code_execution で実際に"
    "コードを書いて実行し、ファイルを生成してください。生成したファイルは"
    "自動的にユーザーへ送信されます。",
)

_raw_ids = os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "")
IDS = {int(x) for x in _raw_ids.replace(" ", "").split(",") if x.strip().isdigit()}
CWD = os.environ.get("CLAUDE_CODE_CWD", os.getcwd())
PMODE = os.environ.get("CLAUDE_CODE_PERMISSION_MODE", "acceptEdits")
TOOLS_CC = [
    t.strip()
    for t in os.environ.get(
        "CLAUDE_CODE_ALLOWED_TOOLS", "Read,Edit,Write,Bash,Glob,Grep"
    ).split(",")
    if t.strip()
]
CCTURNS = int(os.environ.get("CLAUDE_CODE_MAX_TURNS", "30"))

# 📞 電話発信 (Twilio)
TW_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TW_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TW_FROM = os.environ.get("TWILIO_FROM_NUMBER", "")
TW_LANG = os.environ.get("TWILIO_VOICE_LANG", "ja-JP")
# 自然な日本語音声 (Amazon Polly)。女性=Polly.Mizuki / 男性=Polly.Takumi
# よりリアルな neural 音声例: Polly.Kazuha-Neural(女) / Polly.Tomoko-Neural(女) / Polly.Takumi-Neural(男)
TW_VOICE = os.environ.get("TWILIO_VOICE", "Polly.Mizuki")
# 双方向AI通話サーバー(voice_agent.py)の公開URL。設定すると /call が会話型になる
VOICE_AGENT_URL = os.environ.get("VOICE_AGENT_URL", "").rstrip("/")
_tw_client = None

LOCK = Path(os.environ.get("BOT_LOCK_PATH", "/tmp/telegram-mega-bot.lock"))
MAXLEN = 4096
EDIT_INTERVAL = 1.3
LOCAL_TZ = dt.datetime.now().astimezone().tzinfo

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("mega-bot")

claude = AsyncAnthropic(api_key=KEY)
_whisper_model = None


# --------------------------------------------------------------------------- #
# 永続化（記憶・スケジュール）
# --------------------------------------------------------------------------- #


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        log.exception("保存失敗: %s", path)


# chat_id(str) -> [事実, ...]
memory: dict[str, list[str]] = _load_json(MEM_PATH, {})
# [{id, chat_id, hour, minute, instruction}, ...]
schedules: list[dict] = _load_json(SCHED_PATH, [])
# [{id, chat_id, hour, minute, number, topic}, ...]
call_schedules: list[dict] = _load_json(CALL_SCHED_PATH, [])
# 先回り秘書: {chat_id(str): {"hour":int,"minute":int}}
proactive: dict[str, dict] = _load_json(PROACTIVE_PATH, {})


def get_memory(chat_id: int) -> list[str]:
    return memory.get(str(chat_id), [])


def add_memory(chat_id: int, fact: str) -> None:
    key = str(chat_id)
    memory.setdefault(key, [])
    if fact not in memory[key]:
        memory[key].append(fact)
        memory[key] = memory[key][-50:]  # 上限
        _save_json(MEM_PATH, memory)


# --------------------------------------------------------------------------- #
# ロック
# --------------------------------------------------------------------------- #


class Lock:
    def __init__(self, p: Path) -> None:
        self.p, self.fd = p, None

    def acquire(self) -> None:
        import fcntl

        self.fd = os.open(self.p, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log.error("別インスタンスが起動中です。二重起動は Conflict の原因のため中止。")
            os.close(self.fd)
            sys.exit(1)
        os.ftruncate(self.fd, 0)
        os.write(self.fd, str(os.getpid()).encode())
        log.info("ロック取得 pid=%s", os.getpid())

    def release(self) -> None:
        if self.fd is not None:
            try:
                import fcntl

                fcntl.flock(self.fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(self.fd)
            self.fd = None
            try:
                self.p.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# 状態
# --------------------------------------------------------------------------- #

modes: dict[int, str] = defaultdict(lambda: "chat")
hist: dict[int, Deque[dict]] = defaultdict(lambda: deque(maxlen=TURNS * 2))
ccsess: dict[int, str] = {}


def auth(u: int | None) -> bool:
    return u is not None and u in IDS


def split(t: str, n: int = MAXLEN) -> list[str]:
    if len(t) <= n:
        return [t]
    r: list[str] = []
    while t:
        if len(t) <= n:
            r.append(t)
            break
        c = t.rfind("\n", 0, n)
        c = c if c > 0 else n
        r.append(t[:c])
        t = t[c:].lstrip("\n")
    return r


async def _safe_edit(msg, text: str) -> None:
    try:
        await msg.edit_text(text)
    except Exception:
        pass


def _system_for(chat_id: int) -> str:
    mems = get_memory(chat_id)
    if not mems:
        return SYS
    bullet = "\n".join(f"- {m}" for m in mems)
    return f"{SYS}\n\n[このユーザーについて記憶していること]\n{bullet}"


# --------------------------------------------------------------------------- #
# クライアントツール定義
# --------------------------------------------------------------------------- #

CLIENT_TOOLS = [
    {
        "name": "save_memory",
        "description": "ユーザーに関する長期的に有用な事実・好み・重要な文脈を保存する。"
        "名前、職業、好み、繰り返し参照される情報など。一時的・些細な内容は保存しない。",
        "input_schema": {
            "type": "object",
            "properties": {"fact": {"type": "string", "description": "保存する事実（簡潔に）"}},
            "required": ["fact"],
        },
    }
]


def _tools_for_chat():
    tools = list(CLIENT_TOOLS)
    if WEB_SEARCH:
        tools.append({"type": "web_search_20260209", "name": "web_search"})
    if CODE_EXEC:
        tools.append({"type": "code_execution_20260120", "name": "code_execution"})
    return tools


def _collect_file_ids(obj, out: set) -> None:
    """応答ブロックを再帰的に走査し、コード実行で生成された file_id を集める。"""
    try:
        fid = getattr(obj, "file_id", None)
        if isinstance(fid, str):
            out.add(fid)
        sub = getattr(obj, "content", None)
        if isinstance(sub, list):
            for x in sub:
                _collect_file_ids(x, out)
        elif sub is not None and not isinstance(sub, (str, bytes)):
            _collect_file_ids(sub, out)
    except Exception:
        pass


async def _send_artifacts(context, chat_id: int, file_ids: set) -> int:
    """生成ファイルをダウンロードして Telegram に送信。送信数を返す。"""
    sent = 0
    for fid in file_ids:
        try:
            meta = await claude.beta.files.retrieve_metadata(fid)
            fname = getattr(meta, "filename", None) or f"{fid}.bin"
            binr = await claude.beta.files.download(fid)
            try:
                data = await binr.aread()
            except Exception:
                data = binr.read()
        except Exception:
            log.exception("生成ファイル取得失敗: %s", fid)
            continue
        bio = io.BytesIO(data)
        bio.name = fname
        try:
            if fname.lower().endswith(_IMG_EXT):
                await context.bot.send_photo(chat_id=chat_id, photo=bio, caption=fname)
            else:
                await context.bot.send_document(chat_id=chat_id, document=bio, filename=fname)
            sent += 1
        except Exception:
            log.exception("ファイル送信失敗: %s", fname)
    return sent


async def _exec_client_tool(chat_id: int, name: str, inp: dict) -> str:
    if name == "save_memory":
        fact = (inp or {}).get("fact", "").strip()
        if fact:
            add_memory(chat_id, fact)
            return f"記憶しました: {fact}"
        return "保存する内容がありません。"
    return f"未知のツール: {name}"


# --------------------------------------------------------------------------- #
# Claude 応答（ストリーミング＋ツールループ）
# --------------------------------------------------------------------------- #


async def answer(update, context, chat_id: int, content, history_repr=None) -> None:
    h = hist[chat_id]
    api_messages = list(h) + [{"role": "user", "content": content}]
    tools = _tools_for_chat()

    placeholder = await update.message.reply_text("🤔 …")
    acc = ""
    last_edit = 0.0
    final = None
    file_ids: set = set()

    try:
        for _ in range(6):  # ツール/検索の継続ループ
            async with claude.messages.stream(
                model=MODEL,
                max_tokens=MAXTOK,
                system=_system_for(chat_id),
                thinking={"type": "adaptive"},
                output_config={"effort": EFFORT},
                tools=tools,
                messages=api_messages,
            ) as stream:
                async for ev in stream:
                    if (
                        ev.type == "content_block_delta"
                        and getattr(ev.delta, "type", None) == "text_delta"
                    ):
                        acc += ev.delta.text
                        now = time.monotonic()
                        if now - last_edit > EDIT_INTERVAL and acc.strip():
                            last_edit = now
                            await _safe_edit(placeholder, acc[:4000] + " ▌")
                    elif ev.type == "content_block_start":
                        bt = getattr(ev.content_block, "type", None)
                        if bt == "server_tool_use":
                            label = "🛠 コード実行中…" if getattr(ev.content_block, "name", "") == "code_execution" else "🌐 検索中…"
                            await _safe_edit(placeholder, (acc[:3900] + f"\n\n{label}").strip())
                final = await stream.get_final_message()

            api_messages.append({"role": "assistant", "content": final.content})
            for b in final.content:
                _collect_file_ids(b, file_ids)
            sr = getattr(final, "stop_reason", None)

            if sr == "tool_use":
                results = []
                for b in final.content:
                    if getattr(b, "type", None) == "tool_use":
                        out = await _exec_client_tool(chat_id, b.name, b.input)
                        results.append(
                            {"type": "tool_result", "tool_use_id": b.id, "content": out}
                        )
                if results:
                    api_messages.append({"role": "user", "content": results})
                    continue
            if sr == "pause_turn":
                continue
            break
    except Exception:
        log.exception("応答生成に失敗")
        await _safe_edit(placeholder, "⚠️ 応答生成中にエラーが発生しました。")
        return

    text = acc.strip()
    if not text and final is not None:
        text = "".join(
            b.text for b in final.content if getattr(b, "type", None) == "text"
        ).strip()
    if not text:
        text = "(応答を生成できませんでした。)"

    h.append({"role": "user", "content": history_repr if history_repr is not None else content})
    h.append({"role": "assistant", "content": text})

    chunks = split(text)
    await _safe_edit(placeholder, chunks[0])
    for c in chunks[1:]:
        await update.message.reply_text(c)

    # 🛠 生成されたファイル（グラフ/画像/docx/xlsx/pdf 等）を送信
    if file_ids:
        await context.bot.send_chat_action(
            chat_id=chat_id, action=constants.ChatAction.UPLOAD_DOCUMENT
        )
        await _send_artifacts(context, chat_id, file_ids)


async def _claude_oneshot(chat_id: int, instruction: str) -> str:
    """スケジュール実行用の非ストリーミング呼び出し（ウェブ検索可）。"""
    msgs = [{"role": "user", "content": instruction}]
    tools = [{"type": "web_search_20260209", "name": "web_search"}] if WEB_SEARCH else []
    text = ""
    for _ in range(4):
        resp = await claude.messages.create(
            model=MODEL,
            max_tokens=MAXTOK,
            system=_system_for(chat_id),
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            tools=tools,
            messages=msgs,
        )
        msgs.append({"role": "assistant", "content": resp.content})
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        if getattr(resp, "stop_reason", None) == "pause_turn":
            continue
        break
    return text or "(出力なし)"


# --------------------------------------------------------------------------- #
# スケジュール（自動実行）
# --------------------------------------------------------------------------- #


async def _scheduled_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    cid = data["chat_id"]
    instruction = data["instruction"]
    try:
        text = await _claude_oneshot(cid, instruction)
    except Exception:
        log.exception("スケジュール実行失敗")
        return
    header = f"⏰ 定時タスク「{instruction}」\n\n"
    chunks = split(header + text)
    for c in chunks:
        await context.bot.send_message(chat_id=cid, text=c)


def _register_job(app: Application, sch: dict) -> bool:
    jq = app.job_queue
    if jq is None:
        return False
    t = dt.time(hour=int(sch["hour"]), minute=int(sch["minute"]), tzinfo=LOCAL_TZ)
    jq.run_daily(
        _scheduled_job,
        time=t,
        data={"chat_id": sch["chat_id"], "instruction": sch["instruction"]},
        name=sch["id"],
        chat_id=sch["chat_id"],
    )
    return True


async def cmd_schedule(update, context):
    """/schedule HH:MM 指示文"""
    cid = update.effective_chat.id
    if context.application.job_queue is None:
        await update.message.reply_text(
            "⚠️ スケジュール機能は未導入です。\n"
            "`pip install \"python-telegram-bot[job-queue]\"` 後に再起動してください。"
        )
        return
    args = (update.message.text or "").split(maxsplit=2)
    if len(args) < 3 or ":" not in args[1]:
        await update.message.reply_text(
            "使い方: /schedule HH:MM 指示文\n"
            "例: /schedule 07:00 今日の主要ニュースを3つ、要点だけ教えて"
        )
        return
    try:
        hh, mm = args[1].split(":")
        hh, mm = int(hh), int(mm)
        assert 0 <= hh < 24 and 0 <= mm < 60
    except Exception:
        await update.message.reply_text("時刻は HH:MM 形式（例 07:00）で指定してください。")
        return
    instruction = args[2].strip()
    sid = f"sch_{cid}_{int(time.time())}"
    sch = {"id": sid, "chat_id": cid, "hour": hh, "minute": mm, "instruction": instruction}
    if not _register_job(context.application, sch):
        await update.message.reply_text("⚠️ スケジューラが利用できません。")
        return
    schedules.append(sch)
    _save_json(SCHED_PATH, schedules)
    await update.message.reply_text(
        f"⏰ 登録しました: 毎日 {hh:02d}:{mm:02d} に「{instruction}」\n"
        "一覧: /schedules ・ 削除: /unschedule <番号>"
    )


async def cmd_schedules(update, context):
    cid = update.effective_chat.id
    mine = [s for s in schedules if s["chat_id"] == cid]
    if not mine:
        await update.message.reply_text("登録済みのスケジュールはありません。/schedule で追加できます。")
        return
    lines = ["⏰ 登録中のスケジュール:"]
    for i, s in enumerate(mine, 1):
        lines.append(f"{i}. {int(s['hour']):02d}:{int(s['minute']):02d} — {s['instruction']}")
    lines.append("\n削除: /unschedule <番号>")
    await update.message.reply_text("\n".join(lines))


async def cmd_unschedule(update, context):
    cid = update.effective_chat.id
    args = (update.message.text or "").split()
    mine = [s for s in schedules if s["chat_id"] == cid]
    if len(args) < 2 or not args[1].isdigit():
        await update.message.reply_text("使い方: /unschedule <番号>（番号は /schedules で確認）")
        return
    idx = int(args[1]) - 1
    if not (0 <= idx < len(mine)):
        await update.message.reply_text("その番号はありません。")
        return
    target = mine[idx]
    jq = context.application.job_queue
    if jq is not None:
        for j in jq.get_jobs_by_name(target["id"]):
            j.schedule_removal()
    schedules.remove(target)
    _save_json(SCHED_PATH, schedules)
    await update.message.reply_text(f"🗑 削除しました: {target['instruction']}")


# --------------------------------------------------------------------------- #
# 📞 電話発信 (Twilio)
# --------------------------------------------------------------------------- #


def _twilio_ready() -> bool:
    return _TWILIO and bool(TW_SID and TW_TOKEN and TW_FROM)


def _twilio_client():
    global _tw_client
    if _tw_client is None:
        from twilio.rest import Client

        _tw_client = Client(TW_SID, TW_TOKEN)
    return _tw_client


async def _compose_call_script(topic: str) -> str:
    """用件から、電話で自然に読み上げる短い日本語原稿を生成。"""
    try:
        resp = await claude.messages.create(
            model=MODEL,
            max_tokens=512,
            system=(
                "あなたは電話で読み上げる短い日本語の原稿を作成します。"
                "挨拶→用件→結びの簡潔な話し言葉で、20〜60秒程度。"
                "記号・箇条書き・URLは使わず、自然に話せる文だけを出力してください。"
            ),
            messages=[{"role": "user", "content": f"次の用件を電話で伝える原稿にして:\n{topic}"}],
        )
        t = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        ).strip()
        return t or topic
    except Exception:
        log.exception("原稿生成失敗")
        return topic


async def _place_call(number: str, topic: str):
    """発信を実行して (sid, モード表示, 詳細) を返す。/call と自動発信で共用。"""
    client = _twilio_client()
    if VOICE_AGENT_URL:
        from urllib.parse import quote

        url = f"{VOICE_AGENT_URL}/twilio/voice?goal={quote(topic)}"
        call = await asyncio.to_thread(
            lambda: client.calls.create(to=number, from_=TW_FROM, url=url, method="POST")
        )
        return call.sid, "🗣 双方向AI通話", f"用件: {topic}"
    script = await _compose_call_script(topic)
    safe = html.escape(script)
    twiml = (
        f'<Response><Say voice="{TW_VOICE}" language="{TW_LANG}">{safe}</Say>'
        f'<Pause length="1"/>'
        f'<Say voice="{TW_VOICE}" language="{TW_LANG}">繰り返します。{safe}</Say></Response>'
    )
    call = await asyncio.to_thread(
        lambda: client.calls.create(to=number, from_=TW_FROM, twiml=twiml)
    )
    return call.sid, "📢 読み上げ(片方向)", f"読み上げ内容:\n「{script}」"


def _valid_e164(number: str) -> bool:
    return number.startswith("+") and number[1:].isdigit() and len(number) >= 8


async def cmd_call(update, context):
    """/call +819012345678 用件"""
    u = update.effective_user.id if update.effective_user else None
    if not auth(u):
        await update.message.reply_text(
            f"⛔ /call は認可ユーザー専用です (ID: {u})。"
            "悪用防止のため ALLOWED_TELEGRAM_USER_IDS の登録が必要。"
        )
        return
    if not _twilio_ready():
        await update.message.reply_text(
            "⚠️ 電話機能が未設定です。次を設定して再起動してください:\n"
            "・`pip install twilio`\n"
            "・環境変数 TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER\n"
            "（Twilio で電話番号を購入し、購入した番号を FROM に指定）"
        )
        return
    args = (update.message.text or "").split(maxsplit=2)
    if len(args) < 3:
        await update.message.reply_text(
            "使い方: /call +819012345678 用件\n"
            "例: /call +819012345678 明日の打ち合わせは10時に変更とお伝えください"
        )
        return
    number = args[1].strip().replace(" ", "").replace("-", "")
    topic = args[2].strip()
    if not _valid_e164(number):
        await update.message.reply_text("番号は国際形式(E.164)で。例: +819012345678")
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING
    )
    try:
        sid, mode, detail = await _place_call(number, topic)
    except Exception as e:
        log.exception("発信失敗")
        await update.message.reply_text(f"⚠️ 発信に失敗しました: {e}")
        return
    log.info("発信: user=%s to=%s sid=%s mode=%s", u, number, sid, mode)
    await update.message.reply_text(
        f"📞 発信しました → {number}\n{mode}\n{detail}\nSID: {sid}"
    )


# --------------------------------------------------------------------------- #
# ⏰📞 自動電話（指定時刻に自動発信）
# --------------------------------------------------------------------------- #


async def _scheduled_call_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    d = context.job.data
    cid, number, topic = d["chat_id"], d["number"], d["topic"]
    if not _twilio_ready():
        return
    try:
        sid, mode, detail = await _place_call(number, topic)
    except Exception as e:
        log.exception("自動発信失敗")
        try:
            await context.bot.send_message(cid, f"⚠️ 自動発信に失敗: {e}")
        except Exception:
            pass
        return
    try:
        await context.bot.send_message(
            cid, f"⏰📞 自動発信 → {number}\n{mode}\n{detail}\nSID: {sid}"
        )
    except Exception:
        pass


def _register_call_job(app: Application, sch: dict) -> bool:
    jq = app.job_queue
    if jq is None:
        return False
    t = dt.time(hour=int(sch["hour"]), minute=int(sch["minute"]), tzinfo=LOCAL_TZ)
    jq.run_daily(
        _scheduled_call_job,
        time=t,
        data={"chat_id": sch["chat_id"], "number": sch["number"], "topic": sch["topic"]},
        name=sch["id"],
        chat_id=sch["chat_id"],
    )
    return True


async def cmd_callat(update, context):
    """/callat HH:MM +819012345678 用件"""
    u = update.effective_user.id if update.effective_user else None
    cid = update.effective_chat.id
    if not auth(u):
        await update.message.reply_text(f"⛔ /callat は認可ユーザー専用 (ID: {u})。")
        return
    if not _twilio_ready():
        await update.message.reply_text("⚠️ 電話機能が未設定です（Twilio 設定が必要）。")
        return
    if context.application.job_queue is None:
        await update.message.reply_text(
            "⚠️ スケジューラ未導入。`pip install \"python-telegram-bot[job-queue]\"` 後に再起動。"
        )
        return
    args = (update.message.text or "").split(maxsplit=3)
    if len(args) < 4 or ":" not in args[1]:
        await update.message.reply_text(
            "使い方: /callat HH:MM +番号 用件\n"
            "例: /callat 18:00 +819012345678 本日の予約確認の電話です"
        )
        return
    try:
        hh, mm = map(int, args[1].split(":"))
        assert 0 <= hh < 24 and 0 <= mm < 60
    except Exception:
        await update.message.reply_text("時刻は HH:MM 形式で。例 18:00")
        return
    number = args[2].strip().replace(" ", "").replace("-", "")
    topic = args[3].strip()
    if not _valid_e164(number):
        await update.message.reply_text("番号は国際形式(E.164)で。例: +819012345678")
        return
    sid = f"call_{cid}_{int(time.time())}"
    sch = {"id": sid, "chat_id": cid, "hour": hh, "minute": mm, "number": number, "topic": topic}
    if not _register_call_job(context.application, sch):
        await update.message.reply_text("⚠️ スケジューラが利用できません。")
        return
    call_schedules.append(sch)
    _save_json(CALL_SCHED_PATH, call_schedules)
    await update.message.reply_text(
        f"⏰📞 自動発信を登録: 毎日 {hh:02d}:{mm:02d} に {number} へ「{topic}」\n"
        "一覧: /callats ・ 削除: /uncallat <番号>"
    )


async def cmd_callats(update, context):
    cid = update.effective_chat.id
    mine = [s for s in call_schedules if s["chat_id"] == cid]
    if not mine:
        await update.message.reply_text("自動発信の登録はありません。/callat で追加できます。")
        return
    lines = ["⏰📞 自動発信の登録:"]
    for i, s in enumerate(mine, 1):
        lines.append(f"{i}. {int(s['hour']):02d}:{int(s['minute']):02d} → {s['number']} 「{s['topic']}」")
    lines.append("\n削除: /uncallat <番号>")
    await update.message.reply_text("\n".join(lines))


async def cmd_uncallat(update, context):
    cid = update.effective_chat.id
    args = (update.message.text or "").split()
    mine = [s for s in call_schedules if s["chat_id"] == cid]
    if len(args) < 2 or not args[1].isdigit():
        await update.message.reply_text("使い方: /uncallat <番号>（/callats で確認）")
        return
    idx = int(args[1]) - 1
    if not (0 <= idx < len(mine)):
        await update.message.reply_text("その番号はありません。")
        return
    target = mine[idx]
    jq = context.application.job_queue
    if jq is not None:
        for j in jq.get_jobs_by_name(target["id"]):
            j.schedule_removal()
    call_schedules.remove(target)
    _save_json(CALL_SCHED_PATH, call_schedules)
    await update.message.reply_text(f"🗑 自動発信を削除: {target['number']} 「{target['topic']}」")


# --------------------------------------------------------------------------- #
# 🤖 先回り秘書（頼まれる前に提案・準備して送る）
# --------------------------------------------------------------------------- #

PROACTIVE_PROMPT = (
    "あなたは私の優秀な秘書AIです。私が頼む前に先回りして役立ってください。"
    "記憶している私の情報と、今日の日付・状況（必要ならウェブ検索で最新情報を確認）を踏まえ、"
    "次を簡潔にまとめて送ってください：①今日の要点（天気・関連ニュース・私に関係しそうな話題）"
    "②私の予定や過去の文脈から、今日やっておくべきこと・準備すべきこと・気をつける点"
    "③先回りの提案（例：『○○の連絡をしておきますか？』『この資料を作りましょうか？』）。"
    "押し付けず、実用的で短く。最後に『何かやることがあれば言ってください』と添えてください。"
)


async def _proactive_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = context.job.data["chat_id"]
    try:
        text = await _claude_oneshot(cid, PROACTIVE_PROMPT)
    except Exception:
        log.exception("先回り秘書 実行失敗")
        return
    try:
        for c in split("🤖 先回りアシスト\n\n" + text):
            await context.bot.send_message(cid, c)
    except Exception:
        pass


def _register_proactive(app: Application, cid_str: str, conf: dict) -> bool:
    jq = app.job_queue
    if jq is None:
        return False
    t = dt.time(hour=int(conf["hour"]), minute=int(conf["minute"]), tzinfo=LOCAL_TZ)
    jq.run_daily(
        _proactive_job,
        time=t,
        data={"chat_id": int(cid_str)},
        name=f"proactive_{cid_str}",
        chat_id=int(cid_str),
    )
    return True


async def cmd_proactive(update, context):
    """/proactive HH:MM で有効化 / off で無効 / 引数なしで状態"""
    cid = update.effective_chat.id
    key = str(cid)
    args = (update.message.text or "").split()
    jq = context.application.job_queue

    if len(args) == 1:
        if key in proactive:
            c = proactive[key]
            await update.message.reply_text(
                f"🤖 先回り秘書: ON（毎日 {int(c['hour']):02d}:{int(c['minute']):02d}）\n"
                "停止: /proactive off ・ 時刻変更: /proactive HH:MM"
            )
        else:
            await update.message.reply_text(
                "🤖 先回り秘書: OFF\n"
                "有効化: /proactive 07:30 のように時刻を指定（毎朝その時刻に先回りで提案します）"
            )
        return

    if args[1].lower() in ("off", "stop", "0"):
        proactive.pop(key, None)
        _save_json(PROACTIVE_PATH, proactive)
        if jq is not None:
            for j in jq.get_jobs_by_name(f"proactive_{key}"):
                j.schedule_removal()
        await update.message.reply_text("🤖 先回り秘書を停止しました。")
        return

    if ":" not in args[1]:
        await update.message.reply_text("時刻は HH:MM で。例: /proactive 07:30")
        return
    if jq is None:
        await update.message.reply_text(
            "⚠️ スケジューラ未導入。`pip install \"python-telegram-bot[job-queue]\"` 後に再起動。"
        )
        return
    try:
        hh, mm = map(int, args[1].split(":"))
        assert 0 <= hh < 24 and 0 <= mm < 60
    except Exception:
        await update.message.reply_text("時刻は HH:MM で。例: /proactive 07:30")
        return
    # 既存ジョブを消して登録し直し
    for j in jq.get_jobs_by_name(f"proactive_{key}"):
        j.schedule_removal()
    proactive[key] = {"hour": hh, "minute": mm}
    _save_json(PROACTIVE_PATH, proactive)
    _register_proactive(context.application, key, proactive[key])
    await update.message.reply_text(
        f"🤖 先回り秘書を ON にしました。毎日 {hh:02d}:{mm:02d} に、"
        "あなたの記憶と今日の状況を踏まえて先回りで提案・準備して送ります。\n"
        "今すぐ試す: /assist"
    )


async def cmd_assist(update, context):
    """先回り提案を今すぐ1回実行"""
    cid = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=cid, action=constants.ChatAction.TYPING)
    try:
        text = await _claude_oneshot(cid, PROACTIVE_PROMPT)
    except Exception:
        log.exception("assist失敗")
        await update.message.reply_text("⚠️ エラーが発生しました。")
        return
    for c in split("🤖 先回りアシスト\n\n" + text):
        await update.message.reply_text(c)


# --------------------------------------------------------------------------- #
# Claude Code
# --------------------------------------------------------------------------- #


async def run_cc(update, context, chat_id: int, prompt: str) -> None:
    opt = ClaudeAgentOptions(
        cwd=CWD, permission_mode=PMODE, allowed_tools=TOOLS_CC, max_turns=CCTURNS
    )
    if ccsess.get(chat_id):
        opt.resume = ccsess[chat_id]
    sid = fin = None
    sent = False
    try:
        async for m in query(prompt=prompt, options=opt):
            if isinstance(m, AssistantMessage):
                t = "\n".join(
                    b.text for b in m.content if isinstance(b, TextBlock) and b.text
                ).strip()
                if t:
                    for c in split(t):
                        await update.message.reply_text(c)
                        sent = True
                await context.bot.send_chat_action(
                    chat_id=chat_id, action=constants.ChatAction.TYPING
                )
            elif isinstance(m, ResultMessage):
                sid = m.session_id
                fin = m.result
    except Exception:
        log.exception("CC失敗")
        await update.message.reply_text("⚠️ Claude Code 実行エラー")
        return
    if sid:
        ccsess[chat_id] = sid
    if fin and not sent:
        for c in split(fin):
            await update.message.reply_text(c)
    elif not sent and not fin:
        await update.message.reply_text("✅ 完了（出力なし）")


# --------------------------------------------------------------------------- #
# 音声
# --------------------------------------------------------------------------- #


def _transcribe(path: str) -> str:
    global _whisper_model
    if _whisper_model is None:
        log.info("Whisper モデル読み込み: %s", WHISPER_MODEL)
        _whisper_model = WhisperModel(WHISPER_MODEL, compute_type="int8")
    segments, _ = _whisper_model.transcribe(path, beam_size=1)
    return "".join(seg.text for seg in segments).strip()


# --------------------------------------------------------------------------- #
# コマンド
# --------------------------------------------------------------------------- #


async def c_start(update, context):
    await update.message.reply_text(
        "🤖 最強 Claude ボット v4（なんでも作れる）\n\n"
        "💬 質問（文脈記憶）/ 🌐 自動ウェブ検索 / ⚡ リアルタイム表示\n"
        "🏭 グラフ・画像・Word/Excel/PDF などファイル生成\n"
        "🧠 あなたのことを自動で記憶 / ⏰ 定時タスク自動実行\n"
        "🖼 画像 / 📄 PDF / 🎤 音声 / 🛠 /code\n\n"
        "/help で詳細"
    )


async def c_help(update, context):
    await update.message.reply_text(
        "できること:\n"
        "・💬 テキスト → ⚡表示で回答（🌐必要なら自動検索）\n"
        "・🏭 ファイル作成 → 「売上の棒グラフ作って」「請求書のExcel作って」等で\n"
        "  実際にファイルを生成して送信\n"
        "・🧠 名前や好みを伝えると自動で記憶（/memory で確認, /forget で消去）\n"
        "・⏰ /schedule HH:MM 指示 → 毎日その時刻に自動実行して送信\n"
        "・📞 /call 番号 用件 → 今すぐ電話してAIが応対（要認可・Twilio）\n"
        "・⏰📞 /callat HH:MM 番号 用件 → 毎日その時刻に自動で電話\n"
        "・🤖 /proactive HH:MM → 毎朝こちらから先回りで提案・準備（/assist で今すぐ）\n"
        "・🖼 写真 / 📄 PDF・文書 / 🎤 音声メッセージ\n"
        "・🛠 /code → Claude Code（要認可）\n\n"
        "/memory 記憶一覧 ・ /forget 記憶消去 ・ /schedules 予定一覧\n"
        "/chat ・ /code ・ /reset ・ /status"
    )


async def c_memory(update, context):
    mems = get_memory(update.effective_chat.id)
    if not mems:
        await update.message.reply_text("🧠 まだ記憶はありません。会話から自動で覚えていきます。")
        return
    await update.message.reply_text("🧠 記憶していること:\n" + "\n".join(f"・{m}" for m in mems))


async def c_forget(update, context):
    memory.pop(str(update.effective_chat.id), None)
    _save_json(MEM_PATH, memory)
    await update.message.reply_text("🧠 記憶を消去しました。")


async def c_chat(update, context):
    modes[update.effective_chat.id] = "chat"
    await update.message.reply_text("💬 チャットモードに切替。")


async def c_code(update, context):
    u = update.effective_user.id if update.effective_user else None
    if not _CC:
        await update.message.reply_text("⚠️ claude-agent-sdk 未導入です。")
        return
    if not auth(u):
        await update.message.reply_text(f"⛔ /code は認可ユーザー専用 (ID: {u})。")
        return
    modes[update.effective_chat.id] = "code"
    await update.message.reply_text(f"🛠 Claude Code モード。cwd: {CWD}\n/chat で戻る")


async def c_reset(update, context):
    cid = update.effective_chat.id
    if modes[cid] == "code":
        ccsess.pop(cid, None)
        await update.message.reply_text("🔄 CC セッション初期化。")
    else:
        hist.pop(cid, None)
        await update.message.reply_text("🔄 会話履歴を消去（記憶は /forget で別途消去）。")


async def c_status(update, context):
    cid = update.effective_chat.id
    m = modes[cid]
    jq = "ON" if context.application.job_queue is not None else "OFF (未導入)"
    await update.message.reply_text(
        f"モード: {'🛠 Code' if m == 'code' else '💬 Chat'}\n"
        f"モデル: {MODEL} (effort={EFFORT})\n"
        f"🌐 ウェブ検索: {'ON' if WEB_SEARCH else 'OFF'}\n"
        f"🏭 ファイル生成: {'ON' if CODE_EXEC else 'OFF'}\n"
        f"🧠 記憶件数: {len(get_memory(cid))}\n"
        f"⏰ スケジューラ: {jq}\n"
        f"📞 電話発信: {'利用可' if _twilio_ready() else '未設定'}"
        f"（{'🗣双方向AI通話' if VOICE_AGENT_URL else '📢読み上げ'}・声: {TW_VOICE}）\n"
        f"🎤 音声: {'利用可' if _WHISPER else '不可'} / 🛠 CC: {'利用可' if _CC else '不可'}"
    )


# --------------------------------------------------------------------------- #
# メッセージ
# --------------------------------------------------------------------------- #


async def _code_guard(update, chat_id: int) -> bool:
    if modes[chat_id] == "code":
        await update.message.reply_text("🛠 Code モード中はこの入力を扱えません。/chat へ。")
        return True
    return False


async def on_text(update, context):
    if not update.message or not update.message.text:
        return
    cid = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=cid, action=constants.ChatAction.TYPING)
    if modes[cid] == "code":
        u = update.effective_user.id if update.effective_user else None
        if not auth(u):
            await update.message.reply_text("⛔ 認可ユーザー専用。")
            return
        await run_cc(update, context, cid, update.message.text)
        return
    await answer(update, context, cid, update.message.text)


async def on_photo(update, context):
    if not update.message or not update.message.photo:
        return
    cid = update.effective_chat.id
    if await _code_guard(update, cid):
        return
    await context.bot.send_chat_action(chat_id=cid, action=constants.ChatAction.TYPING)
    f = await update.message.photo[-1].get_file()
    b64 = base64.standard_b64encode(bytes(await f.download_as_bytearray())).decode()
    cap = update.message.caption or "この画像について説明して。"
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
        {"type": "text", "text": cap},
    ]
    await answer(update, context, cid, content, history_repr=f"[画像] {cap}")


async def on_document(update, context):
    if not update.message or not update.message.document:
        return
    cid = update.effective_chat.id
    if await _code_guard(update, cid):
        return
    await context.bot.send_chat_action(chat_id=cid, action=constants.ChatAction.TYPING)
    doc = update.message.document
    f = await doc.get_file()
    data = bytes(await f.download_as_bytearray())
    name = doc.file_name or "file"
    mime = doc.mime_type or ""
    cap = update.message.caption or "この文書を要約し、重要点を教えて。"
    if mime == "application/pdf" or name.lower().endswith(".pdf"):
        b64 = base64.standard_b64encode(data).decode()
        content = [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}},
            {"type": "text", "text": cap},
        ]
        repr_ = f"[PDF: {name}] {cap}"
    else:
        text = data.decode("utf-8", errors="replace")[:100000]
        content = f"次のファイル「{name}」の内容です:\n\n{text}\n\n---\n{cap}"
        repr_ = f"[ファイル: {name}] {cap}"
    await answer(update, context, cid, content, history_repr=repr_)


async def on_voice(update, context):
    msg = update.message
    if not msg or not (msg.voice or msg.audio):
        return
    cid = update.effective_chat.id
    if await _code_guard(update, cid):
        return
    if not _WHISPER:
        await update.message.reply_text("🎤 音声機能は未導入です（faster-whisper）。")
        return
    await context.bot.send_chat_action(chat_id=cid, action=constants.ChatAction.TYPING)
    media = msg.voice or msg.audio
    f = await media.get_file()
    tmp = f"/tmp/voice_{cid}_{msg.message_id}.ogg"
    await f.download_to_drive(tmp)
    try:
        text = await asyncio.to_thread(_transcribe, tmp)
    except Exception:
        log.exception("文字起こし失敗")
        await update.message.reply_text("⚠️ 音声の文字起こしに失敗しました。")
        return
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if not text:
        await update.message.reply_text("🎤 音声を認識できませんでした。")
        return
    await update.message.reply_text(f"🎤 「{text}」")
    await answer(update, context, cid, text, history_repr=f"[音声] {text}")


# --------------------------------------------------------------------------- #
# エラー / 起動
# --------------------------------------------------------------------------- #


async def on_err(update, context):
    e = context.error
    if isinstance(e, Conflict):
        log.error("Conflict検出: 別インスタンスが getUpdates 実行中の可能性。")
        return
    log.exception("未処理例外", exc_info=e)


async def post_init(app: Application):
    await app.bot.delete_webhook(drop_pending_updates=True)
    me = await app.bot.get_me()
    # 保存済みスケジュールを復元
    restored = 0
    for sch in schedules:
        if _register_job(app, sch):
            restored += 1
    for sch in call_schedules:
        if _register_call_job(app, sch):
            restored += 1
    for cid_str, conf in proactive.items():
        if _register_proactive(app, cid_str, conf):
            restored += 1
    log.info(
        "起動: @%s (id=%s) web_search=%s code_exec=%s whisper=%s cc=%s call=%s jobs=%s/%s",
        me.username, me.id, WEB_SEARCH, CODE_EXEC, _WHISPER, _CC, _twilio_ready(), restored, len(schedules),
    )


def main():
    if not TOKEN:
        log.error("TELEGRAM_BOT_TOKEN 未設定")
        sys.exit(1)
    if not KEY:
        log.error("ANTHROPIC_API_KEY 未設定")
        sys.exit(1)

    lk = Lock(LOCK)
    try:
        lk.acquire()
    except ImportError:
        log.warning("fcntl 不可: ロック省略")

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", c_start))
    app.add_handler(CommandHandler("help", c_help))
    app.add_handler(CommandHandler("memory", c_memory))
    app.add_handler(CommandHandler("forget", c_forget))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("schedules", cmd_schedules))
    app.add_handler(CommandHandler("unschedule", cmd_unschedule))
    app.add_handler(CommandHandler("call", cmd_call))
    app.add_handler(CommandHandler("callat", cmd_callat))
    app.add_handler(CommandHandler("callats", cmd_callats))
    app.add_handler(CommandHandler("uncallat", cmd_uncallat))
    app.add_handler(CommandHandler("proactive", cmd_proactive))
    app.add_handler(CommandHandler("assist", cmd_assist))
    app.add_handler(CommandHandler("chat", c_chat))
    app.add_handler(CommandHandler("code", c_code))
    app.add_handler(CommandHandler("reset", c_reset))
    app.add_handler(CommandHandler("status", c_status))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_err)

    try:
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            stop_signals=(signal.SIGINT, signal.SIGTERM),
        )
    finally:
        lk.release()
        log.info("シャットダウン完了")


if __name__ == "__main__":
    main()
