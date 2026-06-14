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
import csv
import datetime as dt
import email as emaillib
import html
import imaplib
import io
import json
import logging
import os
import signal
import smtplib
import sys
import time
from collections import defaultdict, deque
from email.header import decode_header
from email.mime.text import MIMEText
from pathlib import Path

import httpx
from anthropic import AsyncAnthropic
from telegram import BotCommand, Update, constants
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
    from gtts import gTTS

    _TTS = True
except Exception:
    _TTS = False

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
EFFORT = os.environ.get("CLAUDE_EFFORT", "high")  # 覚醒: 常に最高品質
MAXTOK = int(os.environ.get("CLAUDE_MAX_TOKENS", "16000"))  # 最大出力(非ストリーム経路も安全な上限)
TURNS = int(os.environ.get("HISTORY_TURNS", "20"))  # 覚醒: 文脈をより長く保持
WEB_SEARCH = os.environ.get("WEB_SEARCH", "1") not in ("0", "false", "False", "")
CODE_EXEC = os.environ.get("CODE_EXEC", "1") not in ("0", "false", "False", "")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
TTS_LANG = os.environ.get("TTS_LANG", "ja")  # 🔊 音声返信の言語
TTS_MAXLEN = int(os.environ.get("TTS_MAXLEN", "800"))  # 読み上げる最大文字数
# 画像とみなす拡張子
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")

DATA_DIR = Path(os.environ.get("BOT_DATA_DIR", str(Path.home() / ".telegram-mega-bot")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEM_PATH = DATA_DIR / "memory.json"
SCHED_PATH = DATA_DIR / "schedules.json"
CALL_SCHED_PATH = DATA_DIR / "call_schedules.json"
PROACTIVE_PATH = DATA_DIR / "proactive.json"
AUTOREPORT_PATH = DATA_DIR / "autoreport.json"  # 📊 毎日の自動日報設定
N8N_PATH = DATA_DIR / "n8n_webhooks.json"
KB_PATH = DATA_DIR / "knowledge.json"
CUST_PATH = DATA_DIR / "customers.json"
REM_PATH = DATA_DIR / "reminders.json"
TEAM_PATH = DATA_DIR / "team.json"  # 👥 社内チーム名簿（個人情報はローカル保存・リポジトリには載せない）
LINKS_PATH = DATA_DIR / "links.json"  # 🔖 よく使うURLのブックマーク（パスワードは保存しない）

# 👥 チーム共有: ON にすると記憶・知識・顧客台帳を認可ユーザー全員で共有する
TEAM_MODE = os.environ.get("TEAM_MODE", "0") in ("1", "true", "True", "on", "ON")

# 🔔 フォロー漏れ判定: この日数以上連絡していない顧客を「要フォロー」とみなす
FOLLOWUP_DAYS = int(os.environ.get("FOLLOWUP_DAYS", "7"))

# 📧 メール送信 (SMTP)。Gmail はアプリパスワードを使う（OAuth不要）。
EMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "") or os.environ.get("EMAIL_ADDRESS", "")
EMAIL_PASS = os.environ.get("GMAIL_APP_PASSWORD", "") or os.environ.get("EMAIL_PASSWORD", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
IMAP_HOST = os.environ.get("IMAP_HOST", "imap.gmail.com")

# 💬 Slack 送信 (chat.postMessage)。xoxb- のボットトークン（chat:write スコープ）が必要。
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")


def _slack_ready() -> bool:
    return bool(SLACK_BOT_TOKEN)


async def _send_slack(to: str, text: str) -> str:
    """名簿のメンバー名・チャンネルID・ユーザーID宛に Slack メッセージを送る。"""
    if not _slack_ready():
        return "Slack送信が未設定です（SLACK_BOT_TOKEN が必要）。"
    to = (to or "").strip()
    text = (text or "").strip()
    if not text:
        return "送信内容が空です。"
    channel = ""
    if to and to.lstrip("@").startswith(("U", "C", "D", "#")) and " " not in to:
        channel = to.lstrip("@")
    else:
        m = next((x for x in _find_members(to) if x.get("slack_id")), None)
        if m:
            channel = m["slack_id"]
    if not channel:
        return (f"宛先『{to}』のSlack IDが分かりません。名簿に登録するか、"
                "ユーザーID(U…)/チャンネルID(C…) を指定してください。")
    try:
        async with httpx.AsyncClient(timeout=20) as cli:
            r = await cli.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                json={"channel": channel, "text": text},
            )
        data = r.json()
        if data.get("ok"):
            return f"💬 Slackに送信しました → {to}（{channel}）"
        return f"Slack送信に失敗: {data.get('error', 'unknown')}"
    except Exception as e:
        log.exception("Slack送信失敗")
        return f"Slack送信に失敗: {e}"


def _slack_name(uid: str) -> str:
    for m in team_members:
        if m.get("slack_id") == uid:
            return m.get("name") or uid
    return uid or "?"


async def _slack_list_channels_raw() -> dict:
    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.get(
            "https://slack.com/api/conversations.list",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            params={"types": "public_channel,private_channel", "limit": 200},
        )
    return r.json()


async def _list_slack_channels_text() -> str:
    if not _slack_ready():
        return "Slackが未設定です（SLACK_BOT_TOKEN が必要）。"
    try:
        data = await _slack_list_channels_raw()
    except Exception as e:
        return f"Slackチャンネル取得に失敗: {e}"
    if not data.get("ok"):
        return f"Slackチャンネル取得に失敗: {data.get('error', 'unknown')}（権限 channels:read 等が必要かも）"
    chs = data.get("channels", [])
    if not chs:
        return "参加可能なチャンネルが見つかりません。"
    return "💬 Slackチャンネル:\n" + "\n".join(
        f"・#{c.get('name')} （{c.get('id')}）" for c in chs[:50]
    )


async def _slack_read(channel: str, limit: int = 15) -> str:
    """チャンネルの最近のメッセージを古い順に読む。channel は #名前 か C… ID。"""
    if not _slack_ready():
        return "Slackが未設定です（SLACK_BOT_TOKEN が必要）。"
    channel = (channel or "").strip().lstrip("@")
    if not channel:
        return "読みたいチャンネルを指定してください（#名前 または C… ID）。"
    cid = ""
    if channel.lstrip("#").startswith(("C", "G", "D")) and " " not in channel and not channel.startswith("#"):
        cid = channel
    else:
        name = channel.lstrip("#")
        try:
            data = await _slack_list_channels_raw()
            if data.get("ok"):
                cid = next((c["id"] for c in data.get("channels", []) if c.get("name") == name), "")
        except Exception:
            cid = ""
    if not cid:
        return f"チャンネル『{channel}』が見つかりません。list_slack_channels でID確認するか C… を指定してください。"
    try:
        async with httpx.AsyncClient(timeout=20) as cli:
            r = await cli.get(
                "https://slack.com/api/conversations.history",
                headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                params={"channel": cid, "limit": max(1, min(int(limit or 15), 30))},
            )
        data = r.json()
    except Exception as e:
        return f"Slack読み取りに失敗: {e}"
    if not data.get("ok"):
        return f"Slack読み取りに失敗: {data.get('error', 'unknown')}（権限 channels:history 等が必要かも）"
    msgs = data.get("messages", [])
    if not msgs:
        return "(このチャンネルにメッセージがありません)"
    lines = []
    for m in reversed(msgs):  # 古い順に並べ替え
        who = _slack_name(m.get("user", ""))
        txt = " ".join((m.get("text", "") or "").split())[:300]
        if txt:
            lines.append(f"{who}: {txt}")
    return f"💬 #{channel.lstrip('#')} の最近の発言:\n" + "\n".join(lines)


def _email_ready() -> bool:
    return bool(EMAIL_ADDRESS and EMAIL_PASS)


def _decode_hdr(s: str) -> str:
    if not s:
        return ""
    out = ""
    for txt, enc in decode_header(s):
        if isinstance(txt, bytes):
            try:
                out += txt.decode(enc or "utf-8", errors="replace")
            except Exception:
                out += txt.decode("utf-8", errors="replace")
        else:
            out += txt
    return out


def _imap_fetch(count: int = 5, unread_only: bool = True) -> list[dict]:
    """受信トレイのメールを取得（既読化しない BODY.PEEK）。"""
    m = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        m.login(EMAIL_ADDRESS, EMAIL_PASS)
        m.select("INBOX")
        _typ, data = m.search(None, "UNSEEN" if unread_only else "ALL")
        ids = data[0].split()[-count:]
        out = []
        for i in reversed(ids):
            _t, msgdata = m.fetch(i, "(BODY.PEEK[])")
            raw = msgdata[0][1]
            msg = emaillib.message_from_bytes(raw)
            snippet = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        try:
                            snippet = part.get_payload(decode=True).decode(
                                part.get_content_charset() or "utf-8", errors="replace"
                            )
                            break
                        except Exception:
                            continue
            else:
                try:
                    snippet = msg.get_payload(decode=True).decode(
                        msg.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:
                    snippet = ""
            out.append({
                "from": _decode_hdr(msg.get("From", "")),
                "subject": _decode_hdr(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
                "snippet": " ".join(snippet.split())[:300],
            })
        return out
    finally:
        try:
            m.logout()
        except Exception:
            pass


def _smtp_send(to: str, subject: str, body: str) -> None:
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = to
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
        srv.starttls()
        srv.login(EMAIL_ADDRESS, EMAIL_PASS)
        srv.send_message(msg)


def _dk(chat_id: int) -> str:
    """データ保存キー。チーム共有時は全員共通、通常はチャットごと。"""
    return "team" if TEAM_MODE else str(chat_id)

SYS = os.environ.get(
    "SYSTEM_PROMPT",
    "あなたはユーザー専属の超優秀な秘書AIです。調査・文章作成・資料生成から、"
    "メール・電話・スケジュール・顧客管理・Claude Code によるPC作業まで、"
    "幅広い実務を最後までやり切ります。\n"
    "【最重要ルール（厳守）】\n"
    "① 実行前に必ず確認する：外向き・取り消せない操作"
    "（メール送信・電話発信・n8n実行・定時タスク/自動電話の登録・リマインダー登録・"
    "Claude Code によるファイル変更やコマンド実行など）は、いきなり実行せず、"
    "まず『何を・誰に・どんな内容で行うか』を具体的に提示し、ユーザーの明確な承認"
    "（「OK」「送って」「実行して」等）を得てから実行する。"
    "ただしユーザーが最初から宛先・内容まで明示して『送って／かけて／やって』と"
    "指示している場合は、その指示自体を承認とみなして実行してよい。"
    "調査・検索・下書き作成・記憶や知識の保存・顧客台帳の参照などの安全な操作は"
    "確認なしで進めてよい。\n"
    "② 嘘をつかない：実際にツールが成功を返したことだけを『やりました／送りました／"
    "登録しました』と報告する。まだ実行していない場合は『これから実行します。"
    "よろしいですか？』『これは下書きです（未送信）』と正直に区別する。"
    "ツールが失敗したら、できなかった事実とエラー内容をそのまま伝える。"
    "成功や実行を捏造しない。やっていないことをやったと言わない。\n"
    "【回答の原則】結論を先に述べる／具体的で実行可能な形にする／簡潔に・冗長を避ける／"
    "事実が必要なら web_search で裏取りし、推測と事実を区別する／"
    "専門領域では一段深い洞察と次の一手まで添える。\n"
    "【姿勢】曖昧な点は最小限の確認だけで前に進め、頼まれる前に役立つ提案を先回りする。"
    "誇張や空疎な相づちはせず、誠実に。ユーザーの言語・文脈・好みに合わせる。\n"
    "【あなたの能力（『何ができる？』等と聞かれたら、この範囲を正確に答える）】"
    "質問応答と最新情報のウェブ検索／グラフ・Word・Excel・PowerPoint・PDF・CSV・"
    "コード等のファイル生成／画像・名刺・PDF・音声の読み取り／顧客台帳(CRM)への記録・"
    "参照・深掘り（履歴から状況と次の打ち手を提案）／フォロー漏れ抽出／"
    "リマインダー・定時タスク・自動電話の登録と確認・取消／朝のブリーフィング（今すぐ/毎朝）／"
    "今日の営業日報の自動作成（daily_report。そのままSlackへ投稿もできる）／"
    "毎日決まった時刻の日報自動送信の設定（set_daily_report）／"
    "よく使うURLのブックマーク（save_link/open_link。『〇〇開いて』で登録リンクを返す。"
    "銀行など重要サイトでもパスワードは絶対に保存・入力せず、端末の自動入力に任せる）／"
    "社内チーム名簿（メンバーの役職・メール・Slack ID を lookup_member で引ける。"
    "『〇〇さんにメール』と言われたら、まず lookup_member で宛先を特定してから send_email する）／"
    "メールの送受信／(認可ユーザーのみ)Slackの送受信（送信は send_slack、"
    "チャンネルの発言を読むのは slack_read、チャンネル一覧は list_slack_channels）／"
    "全データの書き出し／(認可ユーザーのみ)電話発信とPC上の実作業。"
    "ユーザーはコマンドを覚える必要はなく、自然な依頼だけで上記すべてを使える。"
    "できないことは正直に『できません』と伝える。\n"
    "【営業支援】顧客の相談では、必要なら lookup_customer で台帳の履歴を確認し、"
    "状況を踏まえて次の打ち手（再訪・電話・メール案）まで先回りで具体的に提案する。\n"
    "【ツールの使い分け】最新情報は web_search。グラフ・図・画像・Word(.docx)・"
    "Excel(.xlsx)・PowerPoint(.pptx)・PDF・CSV・コード等のファイル作成は code_execution で"
    "実際にコードを書いて実行し生成する（生成物は自動送信される）。"
    "電話は make_call、定時タスクは schedule_task、定時の電話は schedule_call、"
    "リマインダーは set_reminder（確認は list_reminders・取消は cancel_reminder）、"
    "定時タスクの確認/取消は list_scheduled_tasks・cancel_scheduled_task、"
    "自動電話の確認/取消は list_scheduled_calls・cancel_scheduled_call、"
    "今日のまとめは run_briefing・毎朝の自動送信は set_morning_briefing、"
    "顧客一覧は list_customers・データ書き出しは export_data、"
    "メール送受信は send_email / check_email、"
    "顧客記録は save_customer / lookup_customer、フォロー漏れは list_followups、"
    "n8n 連携は run_n8n_workflow、PC上の実作業（コード作成・修正・コマンド実行・"
    "ファイル操作・アプリ構築など）は run_claude_code を使う"
    "（これらは権限のあるユーザーにのみ提供され、上記①の実行前確認の対象）。"
    "コマンド（/call 等）を使わせず、自然な言葉の依頼から適切なツールを自分で選ぶ。"
    "会話からユーザーの名前・好み・繰り返し役立つ重要な事実を学んだら save_memory で"
    "保存する（長期的に有用なものだけ。一時的・些細な内容は保存しない）。",
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

# 🌐 MCP クライアント: 接続する MCP サーバー定義（JSON 配列）。設定すると会話/taskが
# 任意の MCP サーバーのツールを Claude が自律的に使う（n8n / Slack / GitHub / Google …）。
# 例: MCP_SERVERS='[{"type":"url","name":"n8n","url":"https://xxx/mcp-server/http","authorization_token":"..."}]'
try:
    _mcp = json.loads(os.environ.get("MCP_SERVERS", "") or "[]")
    MCP_SERVERS = _mcp if isinstance(_mcp, list) else []
except Exception:
    MCP_SERVERS = []
MCP_BETA = "mcp-client-2025-11-20"
_mcp_disabled = False  # MCP 接続に失敗したら True にして通常パスへフォールバック
_app = None  # Application 参照（自然言語からのスケジュール登録に使う）

# 🔄 自己更新: /update で最新コードを取得して再起動（launchd の KeepAlive で復帰）
UPDATE_URL = os.environ.get(
    "BOT_UPDATE_URL",
    "https://raw.githubusercontent.com/hasegawa212/-6780/refs/heads/"
    "claude/loving-pasteur-KqQsk/telegram-ai-bot/mega_bot.py",
)

LOCK = Path(os.environ.get("BOT_LOCK_PATH", "/tmp/telegram-mega-bot.lock"))
MAXLEN = 4096
EDIT_INTERVAL = 1.3
LOCAL_TZ = dt.datetime.now().astimezone().tzinfo

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("mega-bot")

# 無双: 一時的な通信/APIエラーは自動で粘る（落ちずに押し切る）
claude = AsyncAnthropic(api_key=KEY, max_retries=6, timeout=180.0)
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
# 📊 毎日の自動日報: {chat_id(str): {"hour":int,"minute":int,"slack_channel":str?}}
autoreport: dict[str, dict] = _load_json(AUTOREPORT_PATH, {})
# n8n ワークフロー: {name: webhook_url}
n8n_webhooks: dict[str, str] = _load_json(N8N_PATH, {})
# 単発リマインダー: [{id, chat_id, ts(epoch), message, number}]
reminders: list[dict] = _load_json(REM_PATH, [])
# 👥 社内チーム名簿: [{name, role, email, slack_id}]（会社共通・チャット非依存）
team_members: list[dict] = _load_json(TEAM_PATH, [])


def _member_line(m: dict) -> str:
    parts = [m.get("name", "")]
    if m.get("role"):
        parts.append(f"({m['role']})")
    if m.get("email"):
        parts.append(f"📧 {m['email']}")
    if m.get("slack_id"):
        parts.append(f"💬 {m['slack_id']}")
    return " ".join(p for p in parts if p)


def _find_members(query: str) -> list[dict]:
    q = (query or "").strip().lower()
    if not q:
        return list(team_members)
    out = []
    for m in team_members:
        blob = " ".join(
            str(m.get(k, "")) for k in ("name", "role", "email", "slack_id")
        ).lower()
        if q in blob:
            out.append(m)
    return out


def _lookup_member_text(query: str) -> str:
    hits = _find_members(query)
    if not hits:
        return f"『{query}』に一致する社内メンバーは見つかりませんでした。/team で一覧を確認できます。"
    return "\n".join("・" + _member_line(m) for m in hits[:20])


def _list_team_text() -> str:
    if not team_members:
        return "👥 チーム名簿はまだ登録されていません。"
    return f"👥 チーム名簿（{len(team_members)}名）:\n" + "\n".join(
        "・" + _member_line(m) for m in team_members
    )


def _save_member(name: str, role: str = "", email: str = "", slack_id: str = "") -> str:
    global team_members
    name = (name or "").strip()
    if not name:
        return "メンバー名が必要です。"
    for m in team_members:
        if m.get("name") == name:
            if role:
                m["role"] = role
            if email:
                m["email"] = email
            if slack_id:
                m["slack_id"] = slack_id
            _save_json(TEAM_PATH, team_members)
            return f"🔄 更新しました: {_member_line(m)}"
    rec = {"name": name, "role": role, "email": email, "slack_id": slack_id}
    team_members.append(rec)
    _save_json(TEAM_PATH, team_members)
    return f"✅ 名簿に登録しました: {_member_line(rec)}"


# 🔖 ブックマーク: {chat_id(str): {keyword: url}}（パスワードは保存しない）
links: dict[str, dict] = _load_json(LINKS_PATH, {})


def _save_link(chat_id: int, keyword: str, url: str) -> str:
    keyword = (keyword or "").strip()
    url = (url or "").strip()
    if not keyword or not url:
        return "キーワードとURLが必要です。"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    links.setdefault(_dk(chat_id), {})[keyword] = url
    _save_json(LINKS_PATH, links)
    return f"🔖 登録しました: 「{keyword}」→ {url}"


def _open_link(chat_id: int, keyword: str) -> str:
    d = links.get(_dk(chat_id), {})
    kw = (keyword or "").strip()
    if kw in d:
        return f"🔖 {kw}: {d[kw]}\n（タップで開けます。パスワードは端末の自動入力にお任せください）"
    for k, v in d.items():
        if kw and (kw in k or k in kw):
            return f"🔖 {k}: {v}\n（タップで開けます。パスワードは端末の自動入力にお任せください）"
    return f"『{keyword}』のリンクは未登録です。「{keyword} を https://… で登録して」と言えば保存します。"


def _list_links_text(chat_id: int) -> str:
    d = links.get(_dk(chat_id), {})
    if not d:
        return "🔖 登録済みのリンクはありません。「〇〇 を https://… で登録して」で追加できます。"
    return "🔖 登録リンク:\n" + "\n".join(f"・{k} → {v}" for k, v in d.items())


def get_memory(chat_id: int) -> list[str]:
    return memory.get(_dk(chat_id), [])


def add_memory(chat_id: int, fact: str) -> None:
    key = _dk(chat_id)
    memory.setdefault(key, [])
    if fact not in memory[key]:
        memory[key].append(fact)
        memory[key] = memory[key][-50:]  # 上限
        _save_json(MEM_PATH, memory)


# 📚 知識ベース: {chat_id(str): [{"title":..., "content":...}]}
knowledge: dict[str, list] = _load_json(KB_PATH, {})


def get_knowledge(chat_id: int) -> list:
    return knowledge.get(_dk(chat_id), [])


def add_knowledge(chat_id: int, title: str, content: str) -> None:
    key = _dk(chat_id)
    knowledge.setdefault(key, [])
    knowledge[key].append({"title": title or "メモ", "content": content[:20000]})
    knowledge[key] = knowledge[key][-50:]
    _save_json(KB_PATH, knowledge)


# 🗂 顧客台帳（訪問営業向け軽量CRM）: {chat_id(str): {name: {"log":[...],"updated":...}}}
customers: dict[str, dict] = _load_json(CUST_PATH, {})


def add_customer_note(chat_id: int, name: str, note: str) -> None:
    key = _dk(chat_id)
    customers.setdefault(key, {})
    rec = customers[key].setdefault(name, {"log": []})
    stamp = dt.datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")
    rec["log"].append(f"[{stamp}] {note}")
    rec["log"] = rec["log"][-100:]
    rec["updated"] = stamp
    _save_json(CUST_PATH, customers)


def find_customer(chat_id: int, query: str):
    recs = customers.get(_dk(chat_id), {})
    if query in recs:
        return query, recs[query]
    for n, r in recs.items():
        if query and (query in n or n in query):
            return n, r
    return None, None


def stale_customers(chat_id: int, days: int = FOLLOWUP_DAYS):
    """指定日数以上連絡していない顧客を [(名前, 最終接触, 経過日数)] で返す（古い順）。"""
    recs = customers.get(_dk(chat_id), {})
    now = dt.datetime.now(LOCAL_TZ)
    out = []
    for name, r in recs.items():
        updated = r.get("updated", "")
        try:
            u = dt.datetime.strptime(updated, "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ)
        except Exception:
            continue
        days_since = (now - u).days
        if days_since >= days:
            out.append((name, updated, days_since))
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def search_records(chat_id: int, query: str) -> list[str]:
    """記憶・知識ベース・顧客台帳を横断検索してヒット要約を返す。"""
    q = (query or "").strip().lower()
    if not q:
        return []
    hits: list[str] = []
    for m in get_memory(chat_id):
        if q in m.lower():
            hits.append(f"🧠 記憶: {m}")
    for item in get_knowledge(chat_id):
        blob = (item.get("title", "") + " " + item.get("content", "")).lower()
        if q in blob:
            hits.append(f"📚 知識: {item.get('title', 'メモ')}")
    for name, r in customers.get(_dk(chat_id), {}).items():
        matched = [line for line in r.get("log", []) if q in line.lower()]
        if matched or q in name.lower():
            snippet = "；".join(matched)[:300] if matched else "(名前が一致)"
            hits.append(f"🗂 顧客[{name}]: {snippet}")
    return hits


def _backup_payload() -> dict:
    """全データを1つにまとめたバックアップ用辞書。"""
    return {
        "exported_at": dt.datetime.now(LOCAL_TZ).isoformat(),
        "memory": memory,
        "knowledge": knowledge,
        "customers": customers,
        "schedules": schedules,
        "call_schedules": call_schedules,
        "reminders": reminders,
        "n8n_webhooks": n8n_webhooks,
        "proactive": proactive,
    }


def _backup_bytes() -> bytes:
    return json.dumps(_backup_payload(), ensure_ascii=False, indent=2).encode("utf-8")


def _customers_csv(chat_id: int) -> str:
    """顧客台帳を CSV 文字列に書き出す（Excel で開ける）。"""
    recs = customers.get(_dk(chat_id), {})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["顧客名", "最終更新", "記録件数", "履歴"])
    for name, r in sorted(
        recs.items(), key=lambda kv: kv[1].get("updated", ""), reverse=True
    ):
        w.writerow([
            name,
            r.get("updated", ""),
            len(r.get("log", [])),
            " | ".join(r.get("log", [])),
        ])
    return buf.getvalue()


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

# 既定モード: chat=フルアシスタント / prompt=自動プロンプト作成 / code=Claude Code
DEFAULT_MODE = os.environ.get("BOT_DEFAULT_MODE", "chat").strip().lower()
modes: dict[int, str] = defaultdict(lambda: DEFAULT_MODE)
voice_mode: set[int] = set()  # 🔊 常に音声で返信するチャット
hist: dict[int, deque[dict]] = defaultdict(lambda: deque(maxlen=TURNS * 2))
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
    now = dt.datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M (%a)")
    s = (
        SYS
        + f"\n\n現在日時: {now}。「30分後」「明日15時」等の相対時刻は、この現在日時を基準に"
        "絶対時刻 YYYY-MM-DD HH:MM へ変換して set_reminder の at に渡してください。"
    )
    mems = get_memory(chat_id)
    if mems:
        bullet = "\n".join(f"- {m}" for m in mems)
        s += f"\n\n[このユーザーについて記憶していること]\n{bullet}"
    kb = get_knowledge(chat_id)
    if kb:
        parts = []
        budget = 6000  # システムプロンプトに載せる知識の文字数上限
        for item in kb:
            block = f"■{item.get('title', 'メモ')}\n{item.get('content', '')}"
            parts.append(block[:budget])
            budget -= len(block)
            if budget <= 0:
                break
        s += "\n\n[このユーザーの知識ベース（回答時に必ず参照する資料）]\n" + "\n\n".join(parts)
    return s


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
    },
    {
        "name": "save_knowledge",
        "description": "ユーザーが長期的に参照したい資料・情報（料金表・FAQ・規約・"
        "マニュアル・商品情報・プロフィール等）を知識ベースに保存する。以降の回答で"
        "常にこの資料を参照する。「これを覚えて」「資料として登録して」「これを元に"
        "答えて」等と示されたとき、または送られた文書を恒久的に扱うべきときに使う。",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "資料のタイトル"},
                "content": {"type": "string", "description": "保存する本文（資料の中身）"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "save_customer",
        "description": "訪問営業の顧客・見込み客の情報や訪問記録を顧客台帳に保存/追記する。"
        "名刺情報、商談メモ、相手の反応、ステータス、次回アクションなど。"
        "名刺の写真や商談の音声メモを受け取ったら、相手・会社ごとにここへ記録する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "顧客名または会社名"},
                "note": {"type": "string", "description": "記録内容（名刺情報・商談メモ・次回アクション等）"},
            },
            "required": ["name", "note"],
        },
    },
    {
        "name": "lookup_customer",
        "description": "顧客台帳から指定顧客の過去の記録（商談履歴・メモ）を取り出す。"
        "訪問前の準備や状況確認、話のネタ作りに使う。",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "顧客名または会社名"}},
            "required": ["name"],
        },
    },
    {
        "name": "list_followups",
        "description": "しばらく連絡していない顧客（フォロー漏れ）を一覧する。"
        "「追いかけるべき顧客は？」「フォロー漏れない？」「最近連絡してない取引先は？」"
        "等で使う。days を渡すとその日数以上連絡していない顧客に絞る。"
        "結果を受け取ったら、各社に対する今日の打ち手（再訪・電話・メール案）も添えて提案する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": f"この日数以上連絡なし（既定{FOLLOWUP_DAYS}）"},
            },
            "required": [],
        },
    },
    {
        "name": "search_records",
        "description": "記憶・知識ベース・顧客台帳を横断して検索する。"
        "「〇〇について記録あったっけ？」「△△の話、前にしたっけ？」"
        "「□□が含まれる顧客は？」等、過去の蓄積を探すときに使う。",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "検索キーワード"}},
            "required": ["query"],
        },
    },
    {
        "name": "list_reminders",
        "description": "予定中のリマインダー（「30分後に〜」等で登録した単発の通知/電話）を一覧する。"
        "「リマインダー見せて」「予定の通知ある？」「何か登録してたっけ？」等で使う。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cancel_reminder",
        "description": "登録済みのリマインダーを取り消す。「さっきのリマインダー消して」"
        "「コーヒーの通知キャンセル」「全部のリマインダー消して」等で使う。"
        "query に内容の一部を渡すと一致するものを取り消し、all=true で全件取り消す。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "取り消したいリマインダー内容の一部"},
                "all": {"type": "boolean", "description": "全件取り消すなら true"},
            },
            "required": [],
        },
    },
    {
        "name": "set_reminder",
        "description": "指定時刻に1回だけ通知（または電話）するリマインダーを登録する。"
        "「30分後に〜」「明日15時に〜を思い出させて」等で使う。at は現在日時を基準に"
        "絶対時刻へ変換すること。number を渡すとその時刻に電話を発信する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "at": {"type": "string", "description": "YYYY-MM-DD HH:MM（絶対時刻）"},
                "message": {"type": "string", "description": "通知/電話で伝える内容"},
                "number": {"type": "string", "description": "（任意）電話番号 例 +8190... 指定時は電話発信"},
            },
            "required": ["at", "message"],
        },
    },
    {
        "name": "list_customers",
        "description": "顧客台帳に登録されている顧客の一覧を見る。"
        "「顧客一覧見せて」「誰を登録してたっけ？」「台帳見せて」等で使う。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_scheduled_tasks",
        "description": "毎日決まった時刻に自動実行する定時タスク（schedule_taskで登録したもの）を一覧する。"
        "「定時タスク見せて」「毎日の予約なに入ってる？」等で使う。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cancel_scheduled_task",
        "description": "定時タスクを取り消す。「毎朝のニュースやめて」「定時タスク全部消して」等で使う。"
        "query に内容の一部を渡すと一致するものを取消、all=true で全件取消。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "取り消したい定時タスク内容の一部"},
                "all": {"type": "boolean", "description": "全件取り消すなら true"},
            },
            "required": [],
        },
    },
    {
        "name": "list_scheduled_calls",
        "description": "毎日決まった時刻に自動発信する電話予約（schedule_callで登録したもの）を一覧する。"
        "「自動電話の予約見せて」等で使う。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cancel_scheduled_call",
        "description": "自動電話の予約を取り消す。「毎日の確認電話やめて」「自動電話全部消して」等で使う。"
        "query に用件や番号の一部を渡すと一致するものを取消、all=true で全件取消。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "取り消したい用件・番号の一部"},
                "all": {"type": "boolean", "description": "全件取り消すなら true"},
            },
            "required": [],
        },
    },
    {
        "name": "run_briefing",
        "description": "今すぐ朝のブリーフィング（今日の予定・要フォロー顧客・未読メールの集約と提案）を作る。"
        "「今日のまとめ教えて」「ブリーフィングして」「今日やることは？」等で使う。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_morning_briefing",
        "description": "毎朝の自動ブリーフィング送信を設定/解除する。"
        "「毎朝7時にブリーフィング送って」「朝のまとめやめて」等で使う。"
        "time に HH:MM を渡すとON、off=true で停止。",
        "input_schema": {
            "type": "object",
            "properties": {
                "time": {"type": "string", "description": "毎朝送る時刻 HH:MM"},
                "off": {"type": "boolean", "description": "停止するなら true"},
            },
            "required": [],
        },
    },
    {
        "name": "export_data",
        "description": "顧客台帳CSVと全データのバックアップを書き出してこのチャットに送信する。"
        "「データ書き出して」「バックアップ取って」「顧客CSV出して」等で使う。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_daily_report",
        "description": "毎日決まった時刻に営業日報を自動作成して送る設定をする。"
        "「毎日18時に日報送って」「夕方の自動日報やめて」等で使う。"
        "time に HH:MM でON、slack_channel を渡すとそのチャンネルにも自動投稿、off=true で停止。",
        "input_schema": {
            "type": "object",
            "properties": {
                "time": {"type": "string", "description": "毎日作る時刻 HH:MM"},
                "slack_channel": {"type": "string", "description": "（任意）投稿先 #チャンネル名"},
                "off": {"type": "boolean", "description": "停止するなら true"},
            },
            "required": [],
        },
    },
    {
        "name": "save_link",
        "description": "よく使うサイトのURLを、キーワード付きで登録する。"
        "「銀行を https://… で登録して」「〇〇のリンク覚えて」等で使う。"
        "※パスワードは絶対に保存しない（URLのみ）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "呼び出し用キーワード（例: 銀行）"},
                "url": {"type": "string", "description": "URL"},
            },
            "required": ["keyword", "url"],
        },
    },
    {
        "name": "open_link",
        "description": "登録済みのよく使うURLを呼び出す（タップで開けるリンクを返す）。"
        "「銀行開いて」「〇〇のサイト出して」等で使う。ログインのパスワードは扱わない。",
        "input_schema": {
            "type": "object",
            "properties": {"keyword": {"type": "string", "description": "登録時のキーワード"}},
            "required": ["keyword"],
        },
    },
    {
        "name": "list_links",
        "description": "登録済みのよく使うURL一覧を見る。「リンク一覧」「登録サイト見せて」等で使う。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "daily_report",
        "description": "今日の営業日報を作成する。今日対応した顧客・予定・要フォローを集約し、"
        "そのまま提出できる日報にまとめる。「日報作って」「今日のまとめ書いて」等で使う。"
        "作った日報を『Slackの#日報に投稿して』と続けられたら send_slack で投稿する。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "lookup_member",
        "description": "社内チーム名簿から、名前・役職・メール・Slack ID を引く。"
        "「三浦さんのメアド」「上田さんのSlack ID」「営業部長は誰？」等で使う。"
        "メール送信や連絡の宛先を名前から特定したいときにも、まずこれで調べる。",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "名前・役職などの一部"}},
            "required": ["query"],
        },
    },
    {
        "name": "list_team",
        "description": "社内チーム名簿の全員を一覧する。「チーム名簿見せて」「メンバー一覧」等で使う。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "save_member",
        "description": "社内チーム名簿にメンバーを登録/更新する。同名があれば指定項目だけ上書き。"
        "「〇〇さんを名簿に追加、メールは…、役職は…」等で使う。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "氏名"},
                "role": {"type": "string", "description": "役職（任意）"},
                "email": {"type": "string", "description": "メールアドレス（任意）"},
                "slack_id": {"type": "string", "description": "Slack ID（任意）"},
            },
            "required": ["name"],
        },
    },
]


def _tools_for_chat(authorized: bool = False):
    tools = list(CLIENT_TOOLS)
    if WEB_SEARCH:
        tools.append({"type": "web_search_20260209", "name": "web_search"})
    if CODE_EXEC:
        tools.append({"type": "code_execution_20260120", "name": "code_execution"})
    if authorized:
        if _CC:
            tools.append({
                "name": "run_claude_code",
                "description": "Claude Code を使って PC 上の実作業を行う。"
                "コードの作成・修正、コマンド実行、ファイル操作、アプリやスクリプトの構築・"
                "デバッグ、データ処理など『作って』『直して』『動かして』系で使う。"
                "取り消せない変更を伴うことがあるため、何をするかをユーザーに説明し"
                "承認を得てから呼ぶこと。結果（変更点・実行結果）をそのまま正直に報告する。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "instruction": {
                            "type": "string",
                            "description": "Claude Code への具体的な指示（日本語可）",
                        },
                    },
                    "required": ["instruction"],
                },
            })
        if _email_ready():
            tools.append({
                "name": "check_email",
                "description": "受信トレイのメールを確認する。「未読メール教えて」「最近のメール"
                "見せて」「重要なメールある？」等で使う。差出人・件名・抜粋を返すので要約・分類する。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer", "description": "取得件数（既定5）"},
                        "unread_only": {"type": "boolean", "description": "未読のみ（既定true）"},
                    },
                    "required": [],
                },
            })
            tools.append({
                "name": "send_email",
                "description": "メールを送信する。「〜にメールを送って」「お礼メール送って」等で使う。"
                "宛先・件名・本文を整えて送る。送信は取り消せないため、内容が曖昧なら一度確認する。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "宛先メールアドレス"},
                        "subject": {"type": "string", "description": "件名"},
                        "body": {"type": "string", "description": "本文"},
                    },
                    "required": ["to", "subject", "body"],
                },
            })
        if _twilio_ready():
            tools.append({
                "name": "make_call",
                "description": "実際の電話を今すぐ発信し、AIが用件を伝える。"
                "「〜に電話して」「〜へ連絡して」等と頼まれたとき使う。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "number": {"type": "string", "description": "国際形式の番号 例 +818012345678"},
                        "topic": {"type": "string", "description": "電話で伝える用件"},
                    },
                    "required": ["number", "topic"],
                },
            })
        if _slack_ready():
            tools.append({
                "name": "send_slack",
                "description": "Slackにメッセージを送信する。「三浦さんにSlackで連絡して」"
                "「#営業 に共有して」等で使う。to にはチーム名簿のメンバー名、"
                "またはチャンネルID(C…)/ユーザーID(U…)を渡す（名前なら名簿から自動でSlack IDを引く）。"
                "送信は取り消せないため、宛先と内容が曖昧なら一度確認する。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "宛先（メンバー名 / U… / C… / #channel）"},
                        "text": {"type": "string", "description": "送信するメッセージ本文"},
                    },
                    "required": ["to", "text"],
                },
            })
            tools.append({
                "name": "slack_read",
                "description": "Slackチャンネルの最近のメッセージを読む。"
                "「#営業 の最近の話まとめて」「〇〇チャンネル何か動きある？」等で使う。"
                "channel は #名前 または C… のチャンネルID。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "description": "#名前 または C… ID"},
                        "limit": {"type": "integer", "description": "読む件数（既定15・最大30）"},
                    },
                    "required": ["channel"],
                },
            })
            tools.append({
                "name": "list_slack_channels",
                "description": "Botが参加できるSlackチャンネルの一覧（名前とID）を取得する。"
                "「Slackのチャンネル一覧見せて」「どのチャンネルに入ってる？」等で使う。",
                "input_schema": {"type": "object", "properties": {}, "required": []},
            })
        if _app is not None and _app.job_queue is not None:
            tools.append({
                "name": "schedule_task",
                "description": "毎日決まった時刻に自動実行するタスクを登録する。"
                "「毎朝7時にニュースを送って」等で使う。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "time": {"type": "string", "description": "HH:MM（24時間表記）"},
                        "instruction": {"type": "string", "description": "その時刻に実行する内容"},
                    },
                    "required": ["time", "instruction"],
                },
            })
            if _twilio_ready():
                tools.append({
                    "name": "schedule_call",
                    "description": "毎日決まった時刻に自動で電話を発信する予約。"
                    "「毎日18時に〜へ電話して」等で使う。",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "time": {"type": "string", "description": "HH:MM"},
                            "number": {"type": "string", "description": "国際形式の番号"},
                            "topic": {"type": "string", "description": "用件"},
                        },
                        "required": ["time", "number", "topic"],
                    },
                })
    if n8n_webhooks:
        names = "、".join(n8n_webhooks.keys())
        tools.append({
            "name": "run_n8n_workflow",
            "description": (
                f"登録済みのn8n自動化ワークフローを起動する。利用可能な名前: {names}。"
                "メール送信・スプレッドシート記録・外部サービス連携などの実行に使う。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "ワークフロー名"},
                    "payload": {"type": "string", "description": "渡すデータ（テキストまたはJSON文字列）"},
                },
                "required": ["name"],
            },
        })
    return tools


def _stream(**kw):
    """MCP_SERVERS 設定時は beta(mcp) 経由でストリーム。未設定/失敗後は通常。"""
    if MCP_SERVERS and not _mcp_disabled:
        return claude.beta.messages.stream(betas=[MCP_BETA], mcp_servers=MCP_SERVERS, **kw)
    return claude.messages.stream(**kw)


async def _create(**kw):
    if MCP_SERVERS and not _mcp_disabled:
        return await claude.beta.messages.create(betas=[MCP_BETA], mcp_servers=MCP_SERVERS, **kw)
    return await claude.messages.create(**kw)


def _maybe_disable_mcp() -> bool:
    """MCP 有効時に呼ぶと MCP を無効化。無効化したら True を返す（フォールバック用）。"""
    global _mcp_disabled
    if MCP_SERVERS and not _mcp_disabled:
        _mcp_disabled = True
        log.warning("MCP 接続に失敗したため、以降は MCP 無しで動作します。")
        return True
    return False


async def _trigger_n8n(name: str, payload, chat_id: int) -> str:
    url = n8n_webhooks.get(name)
    if not url:
        return f"ワークフロー『{name}』は未登録です。登録済み: {', '.join(n8n_webhooks) or '(なし)'}"
    body = {"text": payload if isinstance(payload, str) else json.dumps(payload),
            "chat_id": chat_id, "source": "telegram-bot"}
    try:
        async with httpx.AsyncClient(timeout=30) as cli:
            r = await cli.post(url, json=body)
        snippet = (r.text or "")[:500]
        return f"n8n『{name}』を実行しました (HTTP {r.status_code})。応答: {snippet or '(空)'}"
    except Exception as e:
        log.exception("n8n起動失敗")
        return f"n8n『{name}』の起動に失敗: {e}"


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


def _tts_bytes(text: str) -> bytes:
    """テキストを音声(mp3)に変換して bytes を返す（gTTS・ブロッキング）。"""
    buf = io.BytesIO()
    gTTS(text=text[:TTS_MAXLEN], lang=TTS_LANG).write_to_fp(buf)
    return buf.getvalue()


async def _send_voice(context, chat_id: int, text: str) -> None:
    """テキストを音声化して Telegram に送る。失敗しても無視（本文は別途送付済み）。"""
    if not _TTS or not text.strip():
        return
    try:
        data = await asyncio.to_thread(_tts_bytes, text)
        bio = io.BytesIO(data)
        bio.name = "reply.mp3"
        await context.bot.send_voice(chat_id=chat_id, voice=bio)
    except Exception:
        try:
            bio = io.BytesIO(data)
            bio.name = "reply.mp3"
            await context.bot.send_audio(chat_id=chat_id, audio=bio, title="返信")
        except Exception:
            log.exception("音声返信に失敗")


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


def _parse_hhmm(s: str):
    try:
        hh, mm = map(int, (s or "").strip().split(":"))
        if 0 <= hh < 24 and 0 <= mm < 60:
            return hh, mm
    except Exception:
        pass
    return None


def _nl_schedule_task(chat_id: int, t: str, instruction: str) -> str:
    hm = _parse_hhmm(t)
    if not hm or not (instruction or "").strip():
        return "時刻(HH:MM)と内容が必要です。"
    if _app is None or _app.job_queue is None:
        return "スケジューラが利用できません。"
    sch = {"id": f"sch_{chat_id}_{int(time.time())}", "chat_id": chat_id,
           "hour": hm[0], "minute": hm[1], "instruction": instruction}
    if not _register_job(_app, sch):
        return "登録に失敗しました。"
    schedules.append(sch)
    _save_json(SCHED_PATH, schedules)
    return f"毎日 {hm[0]:02d}:{hm[1]:02d} に「{instruction}」を実行する予約を登録しました。"


def _nl_schedule_call(chat_id: int, t: str, number: str, topic: str) -> str:
    hm = _parse_hhmm(t)
    number = (number or "").strip().replace(" ", "").replace("-", "")
    if not hm or not _valid_e164(number) or not (topic or "").strip():
        return "時刻(HH:MM)・国際形式の番号・用件が必要です。"
    if not _twilio_ready():
        return "電話機能が未設定です。"
    if _app is None or _app.job_queue is None:
        return "スケジューラが利用できません。"
    sch = {"id": f"call_{chat_id}_{int(time.time())}", "chat_id": chat_id,
           "hour": hm[0], "minute": hm[1], "number": number, "topic": topic}
    if not _register_call_job(_app, sch):
        return "登録に失敗しました。"
    call_schedules.append(sch)
    _save_json(CALL_SCHED_PATH, call_schedules)
    return f"毎日 {hm[0]:02d}:{hm[1]:02d} に {number} へ自動発信する予約を登録しました。"


async def _exec_client_tool(chat_id: int, name: str, inp: dict) -> str:
    inp = inp or {}
    if name == "save_memory":
        fact = inp.get("fact", "").strip()
        if fact:
            add_memory(chat_id, fact)
            return f"記憶しました: {fact}"
        return "保存する内容がありません。"
    if name == "save_knowledge":
        content = inp.get("content", "").strip()
        if not content:
            return "保存する内容がありません。"
        title = inp.get("title", "").strip()
        add_knowledge(chat_id, title, content)
        return f"📚 知識ベースに保存しました: {title or 'メモ'}"
    if name == "set_reminder":
        return _set_reminder(chat_id, inp.get("at", ""), inp.get("message", ""), inp.get("number", ""))
    if name == "list_reminders":
        return _list_reminders_text(chat_id)
    if name == "cancel_reminder":
        return _cancel_reminders(chat_id, inp.get("query", ""), bool(inp.get("all", False)))
    if name == "list_customers":
        return _list_customers_text(chat_id)
    if name == "list_scheduled_tasks":
        return _list_scheduled_tasks_text(chat_id)
    if name == "cancel_scheduled_task":
        return _cancel_scheduled_task(chat_id, inp.get("query", ""), bool(inp.get("all", False)))
    if name == "list_scheduled_calls":
        return _list_scheduled_calls_text(chat_id)
    if name == "cancel_scheduled_call":
        return _cancel_scheduled_call(chat_id, inp.get("query", ""), bool(inp.get("all", False)))
    if name == "run_briefing":
        try:
            return await _compose_briefing(chat_id)
        except Exception:
            return "ブリーフィングの作成に失敗しました。"
    if name == "set_morning_briefing":
        return _set_morning_briefing(chat_id, inp.get("time", ""), bool(inp.get("off", False)))
    if name == "export_data":
        return await _export_data_tool(chat_id)
    if name == "daily_report":
        try:
            return await _compose_daily_report(chat_id)
        except Exception:
            return "日報の作成に失敗しました。"
    if name == "set_daily_report":
        return _set_daily_report(
            chat_id, inp.get("time", ""), inp.get("slack_channel", ""),
            bool(inp.get("off", False)),
        )
    if name == "save_link":
        return _save_link(chat_id, inp.get("keyword", ""), inp.get("url", ""))
    if name == "open_link":
        return _open_link(chat_id, inp.get("keyword", ""))
    if name == "list_links":
        return _list_links_text(chat_id)
    if name == "lookup_member":
        return _lookup_member_text(inp.get("query", ""))
    if name == "list_team":
        return _list_team_text()
    if name == "save_member":
        return _save_member(
            inp.get("name", ""), inp.get("role", ""),
            inp.get("email", ""), inp.get("slack_id", ""),
        )
    if name == "save_customer":
        cn = inp.get("name", "").strip()
        note = inp.get("note", "").strip()
        if not cn or not note:
            return "顧客名と記録内容が必要です。"
        add_customer_note(chat_id, cn, note)
        return f"🗂 顧客台帳に保存しました: {cn}"
    if name == "lookup_customer":
        q = inp.get("name", "").strip()
        n, rec = find_customer(chat_id, q)
        if not rec:
            return f"『{q}』の記録はまだありません。"
        return f"【{n}】(最終更新 {rec.get('updated', '?')})\n" + "\n".join(rec.get("log", []))
    if name == "list_followups":
        days = int(inp.get("days", FOLLOWUP_DAYS) or FOLLOWUP_DAYS)
        st = stale_customers(chat_id, days)
        if not st:
            return f"{days}日以上連絡していない顧客はいません（フォロー漏れなし）。"
        return f"{days}日以上連絡していない顧客（フォロー漏れ）:\n" + "\n".join(
            f"・{n}（最終接触 {u}・{d}日前）" for n, u, d in st
        )
    if name == "search_records":
        hits = search_records(chat_id, inp.get("query", ""))
        if not hits:
            return f"『{inp.get('query', '')}』に一致する記録は見つかりませんでした。"
        return f"{len(hits)}件ヒット:\n" + "\n".join(hits[:30])
    if name == "run_claude_code":
        instr = inp.get("instruction", "").strip()
        if not instr:
            return "Claude Code への指示が空です。"
        return await _cc_oneshot(chat_id, instr)
    if name == "run_n8n_workflow":
        return await _trigger_n8n(inp.get("name", ""), inp.get("payload", ""), chat_id)
    if name == "check_email":
        if not _email_ready():
            return "メール機能が未設定です。"
        try:
            count = int(inp.get("count", 5) or 5)
            unread = inp.get("unread_only", True)
            mails = await asyncio.to_thread(_imap_fetch, count, bool(unread))
        except Exception as e:
            return f"メール取得に失敗しました: {e}"
        if not mails:
            return "該当するメールはありません。"
        return "\n\n".join(
            f"差出人: {m['from']}\n件名: {m['subject']}\n日時: {m['date']}\n抜粋: {m['snippet']}"
            for m in mails
        )
    if name == "send_email":
        to = inp.get("to", "").strip()
        subject = inp.get("subject", "(件名なし)").strip() or "(件名なし)"
        body = inp.get("body", "")
        if not _email_ready():
            return "メール機能が未設定です。"
        if "@" not in to:
            return "宛先メールアドレスが不正です。"
        try:
            await asyncio.to_thread(_smtp_send, to, subject, body)
            return f"📧 送信しました → {to}（件名: {subject}）"
        except Exception as e:
            return f"メール送信に失敗しました: {e}"
    if name == "make_call":
        number = inp.get("number", "").strip().replace(" ", "").replace("-", "")
        topic = inp.get("topic", "")
        if not _twilio_ready():
            return "電話機能が未設定です。"
        if not _valid_e164(number):
            return "番号は国際形式(+...)で指定してください。"
        try:
            sid, mode, detail = await _place_call(number, topic)
            return f"📞 発信しました → {number}（{mode}）SID:{sid}"
        except Exception as e:
            return f"発信に失敗しました: {e}"
    if name == "send_slack":
        return await _send_slack(inp.get("to", ""), inp.get("text", ""))
    if name == "slack_read":
        return await _slack_read(inp.get("channel", ""), inp.get("limit", 15))
    if name == "list_slack_channels":
        return await _list_slack_channels_text()
    if name == "schedule_task":
        return _nl_schedule_task(chat_id, inp.get("time", ""), inp.get("instruction", ""))
    if name == "schedule_call":
        return _nl_schedule_call(chat_id, inp.get("time", ""), inp.get("number", ""), inp.get("topic", ""))
    return f"未知のツール: {name}"


# --------------------------------------------------------------------------- #
# Claude 応答（ストリーミング＋ツールループ）
# --------------------------------------------------------------------------- #


async def answer(update, context, chat_id: int, content, history_repr=None, voice_out=False) -> None:
    h = hist[chat_id]
    api_messages = list(h) + [{"role": "user", "content": content}]
    _u = update.effective_user.id if update.effective_user else None
    tools = _tools_for_chat(auth(_u))

    placeholder = await update.message.reply_text("🤔 …")
    acc = ""
    last_edit = 0.0
    final = None
    file_ids: set = set()

    try:
        for _ in range(6):  # ツール/検索の継続ループ
            async with _stream(
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
        if _maybe_disable_mcp():
            await _safe_edit(
                placeholder,
                "⚠️ MCP 接続に失敗したため無効化しました。もう一度送ってください。",
            )
            return
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

    # 🔊 音声返信（音声で聞かれた / 音声モード時）
    if (voice_out or chat_id in voice_mode):
        await _send_voice(context, chat_id, text)


PROMPT_BUILDER_SYSTEM = (
    "あなたは世界最高水準のプロンプトエンジニアです。ユーザーのざっくりした要望を受け取り、"
    "AIにそのまま貼って使える高品質なプロンプトを設計します。\n"
    "【含める要素】①AIに与える役割（ペルソナ）②目的・ゴール ③必要な背景や入力 "
    "④手順・考え方 ⑤守るべき制約・トーン ⑥出力フォーマット（見出し/箇条書き/文字数等）"
    "⑦必要なら簡単な例。過不足なく構造化する。\n"
    "【方針】曖昧な点は妥当な前提で補い『(前提: …)』と短く明記。冗長にせず、そのまま使える"
    "完成形を出す。要望が日本語なら日本語のプロンプトで答える。\n"
    "【出力形式】最初に『# 完成プロンプト』としてプロンプト本文だけを提示し、"
    "最後に『# 使い方ヒント』として調整ポイントを1〜2行添える。前置きや言い訳は書かない。"
)


TASK_SYSTEM = (
    "あなたは有能な自律エージェントです。与えられた目標を、頼まれなくても最後まで"
    "自分で完遂してください。必要に応じて web_search で最新情報を調べ、code_execution で"
    "コードを書いて実行し（グラフ・資料・データ等のファイルを生成）、段取りを自分で考えて"
    "進めます。途中経過を簡潔に報告しつつ、最終的に「成果のまとめ＋生成物」を提示してください。"
    "外向きの行動（電話・メール送信など実世界に影響するもの）は勝手に実行せず、提案にとどめます。"
)


async def run_task(update, context, chat_id: int, goal: str) -> None:
    """目標を自律的に遂行する（高effort・多ターン・全ツール・成果物送付）。"""
    api_messages = [{"role": "user", "content": goal}]
    _u = update.effective_user.id if update.effective_user else None
    tools = _tools_for_chat(auth(_u))
    sysprompt = _system_for(chat_id) + "\n\n" + TASK_SYSTEM

    placeholder = await update.message.reply_text("🎯 タスクに着手します…")
    acc = ""
    last_edit = 0.0
    final = None
    file_ids: set = set()

    try:
        for _ in range(14):  # 自律ループ（多めに）
            async with _stream(
                model=MODEL,
                max_tokens=16000,
                system=sysprompt,
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
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
                            nm = getattr(ev.content_block, "name", "")
                            label = "🛠 コード実行中…" if nm == "code_execution" else "🌐 調査中…"
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
                        results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
                if results:
                    api_messages.append({"role": "user", "content": results})
                    continue
            if sr == "pause_turn":
                continue
            break
    except Exception:
        if _maybe_disable_mcp():
            await _safe_edit(
                placeholder,
                "⚠️ MCP 接続に失敗したため無効化しました。もう一度送ってください。",
            )
            return
        log.exception("タスク実行失敗")
        await _safe_edit(placeholder, "⚠️ タスク実行中にエラーが発生しました。")
        return

    text = acc.strip()
    if not text and final is not None:
        text = "".join(b.text for b in final.content if getattr(b, "type", None) == "text").strip()
    text = text or "(完了しましたが出力がありません)"
    chunks = split("✅ 完了\n\n" + text)
    await _safe_edit(placeholder, chunks[0])
    for c in chunks[1:]:
        await update.message.reply_text(c)
    if file_ids:
        await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.UPLOAD_DOCUMENT)
        await _send_artifacts(context, chat_id, file_ids)


async def cmd_task(update, context):
    """/task 複雑な目標 → 自律的に遂行"""
    cid = update.effective_chat.id
    goal = (update.message.text or "").split(maxsplit=1)
    if len(goal) < 2 or not goal[1].strip():
        await update.message.reply_text(
            "使い方: /task 目標\n"
            "例: /task 〇〇エリアの賃貸相場を調べて、間取り別の比較表(Excel)にまとめて\n"
            "例: /task 来月の集客キャンペーン案を3つ考えて、企画書(PDF)にして"
        )
        return
    await run_task(update, context, cid, goal[1].strip())


async def _claude_oneshot(chat_id: int, instruction: str) -> str:
    """スケジュール実行用の非ストリーミング呼び出し（ウェブ検索可）。"""
    msgs = [{"role": "user", "content": instruction}]
    tools = [{"type": "web_search_20260209", "name": "web_search"}] if WEB_SEARCH else []
    text = ""
    for _ in range(4):
        resp = await _create(
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


# --------------------------------------------------------------------------- #
# ⏰ 単発リマインダー
# --------------------------------------------------------------------------- #


async def _reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    d = context.job.data
    cid, message, number = d["chat_id"], d.get("message", ""), d.get("number", "")
    # 完了したものは台帳から除去
    rid = d.get("id")
    global reminders
    reminders = [x for x in reminders if x.get("id") != rid]
    _save_json(REM_PATH, reminders)
    try:
        if number and _twilio_ready():
            sid, mode, _detail = await _place_call(number, message)
            await context.bot.send_message(cid, f"⏰📞 リマインダー発信 → {number}\n用件: {message}\nSID:{sid}")
        else:
            await context.bot.send_message(cid, f"⏰ リマインダー: {message}")
    except Exception:
        log.exception("リマインダー実行失敗")


def _register_reminder(app: Application, rem: dict) -> bool:
    jq = app.job_queue
    if jq is None:
        return False
    when = dt.datetime.fromtimestamp(rem["ts"], tz=LOCAL_TZ)
    jq.run_once(_reminder_job, when=when, data=rem, name=rem["id"], chat_id=rem["chat_id"])
    return True


def _set_reminder(chat_id: int, at: str, message: str, number: str = "") -> str:
    if not message.strip():
        return "通知内容が必要です。"
    when = None
    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%m-%d %H:%M", "%H:%M"):
        try:
            d = dt.datetime.strptime(at.strip(), fmt)
            now = dt.datetime.now(LOCAL_TZ)
            if fmt == "%H:%M":
                d = d.replace(year=now.year, month=now.month, day=now.day)
            elif fmt == "%m-%d %H:%M":
                d = d.replace(year=now.year)
            when = d.replace(tzinfo=LOCAL_TZ)
            break
        except ValueError:
            continue
    if when is None:
        return "時刻の形式が不正です（例: 2026-06-09 15:00）。"
    if when <= dt.datetime.now(LOCAL_TZ):
        when = when + dt.timedelta(days=1)  # 過去なら翌日扱い
    number = (number or "").strip().replace(" ", "").replace("-", "")
    if number and not _valid_e164(number):
        number = ""
    if _app is None or _app.job_queue is None:
        return "スケジューラが利用できません。"
    rem = {"id": f"rem_{chat_id}_{int(time.time()*1000)}", "chat_id": chat_id,
           "ts": when.timestamp(), "message": message, "number": number}
    if not _register_reminder(_app, rem):
        return "登録に失敗しました。"
    reminders.append(rem)
    _save_json(REM_PATH, reminders)
    label = f"{when.strftime('%m/%d %H:%M')}"
    return (f"⏰ {label} に電話でお知らせします: {message}" if number
            else f"⏰ {label} にお知らせします: {message}")


def _future_reminders(chat_id: int) -> list[dict]:
    """このチャットの未来の単発リマインダーを時刻順に返す。"""
    now_ts = dt.datetime.now(LOCAL_TZ).timestamp()
    return sorted(
        [r for r in reminders if r.get("chat_id") == chat_id and r.get("ts", 0) > now_ts],
        key=lambda r: r["ts"],
    )


def _list_reminders_text(chat_id: int) -> str:
    mine = _future_reminders(chat_id)
    if not mine:
        return "予定中のリマインダーはありません。"
    lines = []
    for i, r in enumerate(mine, 1):
        when = dt.datetime.fromtimestamp(r["ts"], tz=LOCAL_TZ).strftime("%m/%d %H:%M")
        tag = "📞" if r.get("number") else "🔔"
        lines.append(f"{i}. {when} {tag} {r.get('message', '')}")
    return "予定中のリマインダー:\n" + "\n".join(lines)


def _cancel_reminders(chat_id: int, query: str = "", cancel_all: bool = False) -> str:
    global reminders
    mine = _future_reminders(chat_id)
    if not mine:
        return "取り消せるリマインダーがありません。"
    if cancel_all:
        targets = mine
    elif (query or "").strip():
        q = query.strip()
        targets = [r for r in mine if q in r.get("message", "")]
    else:
        return "どのリマインダーを取り消すか内容の一部を指定してください（全件なら all を指定）。"
    if not targets:
        return f"『{query}』に一致するリマインダーは見つかりませんでした。"
    jq = _app.job_queue if _app is not None else None
    if jq is not None:
        for t in targets:
            for j in jq.get_jobs_by_name(t["id"]):
                j.schedule_removal()
    ids = {t["id"] for t in targets}
    reminders = [r for r in reminders if r.get("id") not in ids]
    _save_json(REM_PATH, reminders)
    return "🗑 取り消しました:\n" + "\n".join(
        f"・{dt.datetime.fromtimestamp(t['ts'], tz=LOCAL_TZ).strftime('%m/%d %H:%M')} "
        f"{t.get('message', '')}"
        for t in targets
    )


def _list_customers_text(chat_id: int) -> str:
    recs = customers.get(_dk(chat_id), {})
    if not recs:
        return "顧客台帳は空です。名刺の写真や「〇〇社の商談メモ：…」と送ると登録されます。"
    items = sorted(recs.items(), key=lambda kv: kv[1].get("updated", ""), reverse=True)
    return f"🗂 顧客台帳（{len(recs)}件）:\n" + "\n".join(
        f"・{n}（最終 {r.get('updated', '?')}・{len(r.get('log', []))}件）" for n, r in items[:40]
    )


def _list_scheduled_tasks_text(chat_id: int) -> str:
    mine = [s for s in schedules if s["chat_id"] == chat_id]
    if not mine:
        return "登録済みの定時タスクはありません。"
    return "⏰ 定時タスク:\n" + "\n".join(
        f"{i}. {int(s['hour']):02d}:{int(s['minute']):02d} {s['instruction']}"
        for i, s in enumerate(mine, 1)
    )


def _cancel_scheduled_task(chat_id: int, query: str = "", cancel_all: bool = False) -> str:
    global schedules
    mine = [s for s in schedules if s["chat_id"] == chat_id]
    if not mine:
        return "取り消せる定時タスクがありません。"
    if cancel_all:
        targets = mine
    elif (query or "").strip():
        q = query.strip()
        targets = [s for s in mine if q in s.get("instruction", "")]
    else:
        return "どの定時タスクを取り消すか内容の一部を指定してください（全件なら all を指定）。"
    if not targets:
        return f"『{query}』に一致する定時タスクは見つかりませんでした。"
    jq = _app.job_queue if _app is not None else None
    if jq is not None:
        for t in targets:
            for j in jq.get_jobs_by_name(t["id"]):
                j.schedule_removal()
    ids = {t["id"] for t in targets}
    schedules = [s for s in schedules if s["id"] not in ids]
    _save_json(SCHED_PATH, schedules)
    return "🗑 取り消しました:\n" + "\n".join(
        f"・{int(t['hour']):02d}:{int(t['minute']):02d} {t['instruction']}" for t in targets
    )


def _list_scheduled_calls_text(chat_id: int) -> str:
    mine = [s for s in call_schedules if s["chat_id"] == chat_id]
    if not mine:
        return "自動電話の予約はありません。"
    return "⏰📞 自動電話の予約:\n" + "\n".join(
        f"{i}. {int(s['hour']):02d}:{int(s['minute']):02d} → {s['number']} 「{s['topic']}」"
        for i, s in enumerate(mine, 1)
    )


def _cancel_scheduled_call(chat_id: int, query: str = "", cancel_all: bool = False) -> str:
    global call_schedules
    mine = [s for s in call_schedules if s["chat_id"] == chat_id]
    if not mine:
        return "取り消せる自動電話の予約がありません。"
    if cancel_all:
        targets = mine
    elif (query or "").strip():
        q = query.strip()
        targets = [s for s in mine if q in s.get("topic", "") or q in s.get("number", "")]
    else:
        return "どの自動電話を取り消すか用件・番号の一部を指定してください（全件なら all を指定）。"
    if not targets:
        return f"『{query}』に一致する自動電話の予約は見つかりませんでした。"
    jq = _app.job_queue if _app is not None else None
    if jq is not None:
        for t in targets:
            for j in jq.get_jobs_by_name(t["id"]):
                j.schedule_removal()
    ids = {t["id"] for t in targets}
    call_schedules = [s for s in call_schedules if s["id"] not in ids]
    _save_json(CALL_SCHED_PATH, call_schedules)
    return "🗑 取り消しました:\n" + "\n".join(
        f"・{int(t['hour']):02d}:{int(t['minute']):02d} {t['number']} 「{t['topic']}」" for t in targets
    )


def _set_morning_briefing(chat_id: int, time_str: str = "", off: bool = False) -> str:
    key = str(chat_id)
    jq = _app.job_queue if _app is not None else None
    if jq is None:
        return "スケジューラが利用できません。"
    if off:
        proactive.pop(key, None)
        _save_json(PROACTIVE_PATH, proactive)
        for j in jq.get_jobs_by_name(f"proactive_{key}"):
            j.schedule_removal()
        return "☀️ 朝のブリーフィングを停止しました。"
    hm = _parse_hhmm(time_str)
    if not hm:
        return "時刻を HH:MM で指定してください（例 07:30）。"
    for j in jq.get_jobs_by_name(f"proactive_{key}"):
        j.schedule_removal()
    proactive[key] = {"hour": hm[0], "minute": hm[1]}
    _save_json(PROACTIVE_PATH, proactive)
    _register_proactive(_app, key, proactive[key])
    return f"☀️ 毎朝 {hm[0]:02d}:{hm[1]:02d} にブリーフィングを送ります。"


async def _export_data_tool(chat_id: int) -> str:
    bot = _app.bot if _app is not None else None
    if bot is None:
        return "送信できませんでした。"
    sent: list[str] = []
    recs = customers.get(_dk(chat_id), {})
    if recs:
        cbio = io.BytesIO(_customers_csv(chat_id).encode("utf-8-sig"))
        cbio.name = "customers.csv"
        try:
            await bot.send_document(
                chat_id=chat_id, document=cbio, filename="customers.csv",
                caption=f"📊 顧客台帳（{len(recs)}件）",
            )
            sent.append("顧客CSV")
        except Exception:
            log.exception("CSV送信失敗")
    if await _send_backup(bot, chat_id, caption="🔁 全データバックアップ"):
        sent.append("全データバックアップ")
    return "書き出して送信しました: " + "、".join(sent) if sent else "書き出すデータがまだありません。"


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


async def cmd_reminders(update, context):
    """登録済みの単発リマインダー（「30分後に〜」等）を一覧する。"""
    cid = update.effective_chat.id
    now_ts = dt.datetime.now(LOCAL_TZ).timestamp()
    mine = sorted(
        [r for r in reminders if r.get("chat_id") == cid and r.get("ts", 0) > now_ts],
        key=lambda r: r["ts"],
    )
    if not mine:
        await update.message.reply_text(
            "⏰ 予定中のリマインダーはありません。\n"
            "「30分後に〇〇」「明日15時に△△を電話で」等と話しかけると登録されます。"
        )
        return
    lines = ["⏰ 予定中のリマインダー:"]
    for i, r in enumerate(mine, 1):
        when = dt.datetime.fromtimestamp(r["ts"], tz=LOCAL_TZ).strftime("%m/%d %H:%M")
        tag = "📞" if r.get("number") else "🔔"
        lines.append(f"{i}. {when} {tag} {r.get('message', '')}")
    lines.append("\n削除: /unremind <番号>")
    await update.message.reply_text("\n".join(lines))


async def cmd_unremind(update, context):
    """/unremind <番号> — リマインダーを取り消す（番号は /reminders で確認）。"""
    global reminders
    cid = update.effective_chat.id
    args = (update.message.text or "").split()
    now_ts = dt.datetime.now(LOCAL_TZ).timestamp()
    mine = sorted(
        [r for r in reminders if r.get("chat_id") == cid and r.get("ts", 0) > now_ts],
        key=lambda r: r["ts"],
    )
    if len(args) < 2 or not args[1].isdigit():
        await update.message.reply_text("使い方: /unremind <番号>（番号は /reminders で確認）")
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
    reminders = [r for r in reminders if r.get("id") != target["id"]]
    _save_json(REM_PATH, reminders)
    when = dt.datetime.fromtimestamp(target["ts"], tz=LOCAL_TZ).strftime("%m/%d %H:%M")
    await update.message.reply_text(f"🗑 取り消しました: {when} {target.get('message', '')}")


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

BRIEFING_PROMPT = (
    "あなたは私の優秀な秘書AIです。以下の『今日の予定データ』『受信メール』と、"
    "記憶している私の情報・今日の日付を踏まえて、朝のブリーフィングを作成してください。\n"
    "①今日のハイライト（リマインダー・自動発信・定時タスクの予定を時系列で）\n"
    "②要フォロー顧客がいれば『今日追いかけるべき相手』として挙げ、各社にひと言の打ち手"
    "（再訪・電話・メールのどれが良いか）を添える\n"
    "③未読メールがあれば重要そうなものを要約し、必要なら返信案を提案\n"
    "④今日の一手（先回りの提案）。\n"
    "簡潔・実用的に、箇条書き中心で。最後に『着手することがあれば言ってください』と添えてください。"
    "予定データが空の項目は触れなくて構いません。"
)


def _today_digest(chat_id: int) -> str:
    """今日のブリーフィングに渡す予定・顧客データのダイジェスト（ネットワーク非依存）。"""
    now = dt.datetime.now(LOCAL_TZ)
    lines: list[str] = []
    mine_rem = []
    for r in reminders:
        if r.get("chat_id") != chat_id:
            continue
        when = dt.datetime.fromtimestamp(r["ts"], tz=LOCAL_TZ)
        if when.date() == now.date():
            tag = "📞" if r.get("number") else "⏰"
            mine_rem.append(f"{when.strftime('%H:%M')} {tag}{r.get('message', '')}")
    if mine_rem:
        lines.append("今日のリマインダー: " + " / ".join(sorted(mine_rem)))
    mine_calls = [s for s in call_schedules if s["chat_id"] == chat_id]
    if mine_calls:
        lines.append("自動電話: " + " / ".join(
            f"{int(s['hour']):02d}:{int(s['minute']):02d} {s['number']}「{s['topic']}」"
            for s in mine_calls
        ))
    mine_sched = [s for s in schedules if s["chat_id"] == chat_id]
    if mine_sched:
        lines.append("定時タスク: " + " / ".join(
            f"{int(s['hour']):02d}:{int(s['minute']):02d} {s['instruction']}"
            for s in mine_sched
        ))
    st = stale_customers(chat_id)
    if st:
        lines.append("要フォロー顧客(連絡が空いている): " + "、".join(
            f"{n}({d}日前)" for n, _u, d in st[:10]
        ))
    return "\n".join(lines) if lines else "(今日の予定データは特になし)"


def _today_customer_activity(chat_id: int) -> list[str]:
    """今日(同日)に更新された顧客の、今日分のログ行をまとめる。"""
    today = dt.datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    out: list[str] = []
    for name, r in customers.get(_dk(chat_id), {}).items():
        todays = [line for line in r.get("log", []) if line.startswith(f"[{today}")]
        if todays:
            out.append(f"【{name}】\n" + "\n".join(todays))
    return out


DAILY_REPORT_PROMPT = (
    "あなたは私の営業秘書です。以下の『今日の活動データ』を元に、"
    "そのまま提出できる簡潔で読みやすい営業日報を日本語で作成してください。\n"
    "① 今日のサマリ（対応した顧客数・主な動き）\n"
    "② 顧客別の動き（誰に何をして、相手の反応・次アクション）\n"
    "③ 明日やること・要フォロー（予定/リマインダーと、連絡が空いている顧客）\n"
    "箇条書き中心で簡潔に。データが無い項目は省略する。前置きや言い訳は書かない。"
)


async def _compose_daily_report(chat_id: int) -> str:
    acts = _today_customer_activity(chat_id)
    digest = _today_digest(chat_id)
    parts = []
    parts.append(
        "[今日の顧客対応]\n" + ("\n\n".join(acts) if acts else "(今日の記録なし)")
    )
    parts.append("[予定・フォロー]\n" + digest)
    prompt = DAILY_REPORT_PROMPT + "\n\n" + "\n\n".join(parts)
    return await _claude_oneshot(chat_id, prompt)


async def _autoreport_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    d = context.job.data
    cid = d["chat_id"]
    channel = d.get("slack_channel", "")
    try:
        text = await _compose_daily_report(cid)
    except Exception:
        log.exception("自動日報の作成に失敗")
        return
    try:
        for c in split("📊 本日の営業日報（自動）\n\n" + text):
            await context.bot.send_message(cid, c)
    except Exception:
        pass
    if channel and _slack_ready():
        res = await _send_slack(channel, "📊 本日の営業日報\n\n" + text)
        try:
            await context.bot.send_message(cid, f"（Slack投稿: {res}）")
        except Exception:
            pass


def _register_autoreport(app: Application, cid_str: str, conf: dict) -> bool:
    jq = app.job_queue
    if jq is None:
        return False
    t = dt.time(hour=int(conf["hour"]), minute=int(conf["minute"]), tzinfo=LOCAL_TZ)
    jq.run_daily(
        _autoreport_job,
        time=t,
        data={"chat_id": int(cid_str), "slack_channel": conf.get("slack_channel", "")},
        name=f"autoreport_{cid_str}",
        chat_id=int(cid_str),
    )
    return True


def _set_daily_report(chat_id: int, time_str: str = "", slack_channel: str = "",
                      off: bool = False) -> str:
    key = str(chat_id)
    jq = _app.job_queue if _app is not None else None
    if jq is None:
        return "スケジューラが利用できません。"
    if off:
        autoreport.pop(key, None)
        _save_json(AUTOREPORT_PATH, autoreport)
        for j in jq.get_jobs_by_name(f"autoreport_{key}"):
            j.schedule_removal()
        return "📊 毎日の自動日報を停止しました。"
    hm = _parse_hhmm(time_str)
    if not hm:
        return "時刻を HH:MM で指定してください（例 18:00）。"
    for j in jq.get_jobs_by_name(f"autoreport_{key}"):
        j.schedule_removal()
    conf: dict = {"hour": hm[0], "minute": hm[1]}
    ch = (slack_channel or "").strip().lstrip("#")
    if ch:
        conf["slack_channel"] = "#" + ch
    autoreport[key] = conf
    _save_json(AUTOREPORT_PATH, autoreport)
    _register_autoreport(_app, key, conf)
    extra = f"・Slack {conf['slack_channel']} にも投稿します" if conf.get("slack_channel") else ""
    return f"📊 毎日 {hm[0]:02d}:{hm[1]:02d} に日報を自動作成して送ります。{extra}"


async def _compose_briefing(chat_id: int) -> str:
    """予定・顧客・未読メールを集約し、Claude に朝のブリーフィングを作らせる。"""
    digest = _today_digest(chat_id)
    mail_line = ""
    if _email_ready():
        try:
            mails = await asyncio.to_thread(_imap_fetch, 5, True)
            if mails:
                mail_line = f"未読{len(mails)}件:\n" + "\n".join(
                    f"- {m['from']}: {m['subject']}" for m in mails
                )
            else:
                mail_line = "未読メールなし"
        except Exception:
            log.exception("ブリーフィング用メール取得失敗")
    prompt = BRIEFING_PROMPT + "\n\n[今日の予定データ]\n" + digest
    if mail_line:
        prompt += "\n\n[受信メール]\n" + mail_line
    return await _claude_oneshot(chat_id, prompt)


async def _proactive_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = context.job.data["chat_id"]
    try:
        text = await _compose_briefing(cid)
    except Exception:
        log.exception("朝のブリーフィング 実行失敗")
        return
    try:
        for c in split("☀️ 朝のブリーフィング\n\n" + text):
            await context.bot.send_message(cid, c)
    except Exception:
        pass
    # 🔁 無敵化: 毎朝のブリーフィングに全データのバックアップを自動添付（Macが壊れても安全）
    await _send_backup(context.bot, cid, caption="🔁 本日の自動バックアップ")


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
                f"☀️ 朝のブリーフィング: ON（毎日 {int(c['hour']):02d}:{int(c['minute']):02d}）\n"
                "停止: /proactive off ・ 時刻変更: /proactive HH:MM ・ 今すぐ: /briefing"
            )
        else:
            await update.message.reply_text(
                "☀️ 朝のブリーフィング: OFF\n"
                "有効化: /proactive 07:30 のように時刻を指定"
                "（毎朝その時刻に予定・要フォロー顧客・未読メールを集約して送ります）"
            )
        return

    if args[1].lower() in ("off", "stop", "0"):
        proactive.pop(key, None)
        _save_json(PROACTIVE_PATH, proactive)
        if jq is not None:
            for j in jq.get_jobs_by_name(f"proactive_{key}"):
                j.schedule_removal()
        await update.message.reply_text("☀️ 朝のブリーフィングを停止しました。")
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
        f"☀️ 朝のブリーフィングを ON にしました。毎日 {hh:02d}:{mm:02d} に、"
        "今日の予定・要フォロー顧客・未読メールを集約し、先回りの提案つきで送ります。\n"
        "今すぐ試す: /briefing"
    )


async def cmd_n8n(update, context):
    """/n8n（一覧）/ add 名前 URL / del 名前 / run 名前 データ"""
    cid = update.effective_chat.id
    u = update.effective_user.id if update.effective_user else None
    args = (update.message.text or "").split(maxsplit=3)

    if len(args) == 1:
        if n8n_webhooks:
            lst = "\n".join(f"・{k}" for k in n8n_webhooks)
            await update.message.reply_text(
                f"🔗 登録済み n8n ワークフロー:\n{lst}\n\n"
                "起動: /n8n run 名前 データ\n"
                "追加: /n8n add 名前 WebhookURL ・ 削除: /n8n del 名前\n"
                "※会話や /task からも自動で呼べます"
            )
        else:
            await update.message.reply_text(
                "🔗 n8n ワークフローは未登録です。\n"
                "n8n で Webhook ノードを作り、その URL を登録:\n"
                "/n8n add 名前 https://あなたのn8n/webhook/xxxx\n"
                "例: /n8n add メール送信 https://n8n.example.com/webhook/abc123"
            )
        return

    sub = args[1].lower()

    if sub == "add":
        if not auth(u):
            await update.message.reply_text(f"⛔ 追加は認可ユーザー専用 (ID: {u})。")
            return
        if len(args) < 4:
            await update.message.reply_text("使い方: /n8n add 名前 WebhookURL")
            return
        name, url = args[2], args[3].strip()
        if not url.startswith("http"):
            await update.message.reply_text("URL は http(s):// で始めてください。")
            return
        n8n_webhooks[name] = url
        _save_json(N8N_PATH, n8n_webhooks)
        await update.message.reply_text(f"✅ 登録しました: {name}\n起動: /n8n run {name} データ")
        return

    if sub in ("del", "delete", "rm"):
        if not auth(u):
            await update.message.reply_text("⛔ 削除は認可ユーザー専用。")
            return
        name = args[2] if len(args) > 2 else ""
        if n8n_webhooks.pop(name, None) is not None:
            _save_json(N8N_PATH, n8n_webhooks)
            await update.message.reply_text(f"🗑 削除しました: {name}")
        else:
            await update.message.reply_text("その名前はありません。/n8n で一覧を確認。")
        return

    if sub == "run":
        if len(args) < 3:
            await update.message.reply_text("使い方: /n8n run 名前 データ")
            return
        name = args[2]
        payload = args[3] if len(args) > 3 else ""
        await context.bot.send_chat_action(chat_id=cid, action=constants.ChatAction.TYPING)
        res = await _trigger_n8n(name, payload, cid)
        await update.message.reply_text(f"🔗 {res}")
        return

    await update.message.reply_text("使い方: /n8n（一覧）/ add 名前 URL / del 名前 / run 名前 データ")


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


async def cmd_briefing(update, context):
    """今すぐ朝のブリーフィング（予定・要フォロー顧客・未読メールの集約）を1回送る。"""
    cid = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=cid, action=constants.ChatAction.TYPING)
    try:
        text = await _compose_briefing(cid)
    except Exception:
        log.exception("briefing失敗")
        await update.message.reply_text("⚠️ エラーが発生しました。")
        return
    for c in split("☀️ ブリーフィング\n\n" + text):
        await update.message.reply_text(c)


async def _send_backup(bot, chat_id: int, caption: str = "") -> bool:
    """全データの JSON バックアップを Telegram に送る（履歴に残る＝Macが壊れても安全）。"""
    try:
        stamp = dt.datetime.now(LOCAL_TZ).strftime("%Y%m%d")
        bio = io.BytesIO(_backup_bytes())
        bio.name = f"backup_{stamp}.json"
        await bot.send_document(
            chat_id=chat_id, document=bio, filename=bio.name,
            caption=caption or "🔁 データバックアップ",
        )
        return True
    except Exception:
        log.exception("バックアップ送信失敗")
        return False


async def cmd_export(update, context):
    """顧客台帳CSV＋全データJSONバックアップを書き出して送信。"""
    cid = update.effective_chat.id
    await context.bot.send_chat_action(
        chat_id=cid, action=constants.ChatAction.UPLOAD_DOCUMENT
    )
    recs = customers.get(_dk(cid), {})
    if recs:
        csv_bytes = _customers_csv(cid).encode("utf-8-sig")  # Excel 文字化け対策
        cbio = io.BytesIO(csv_bytes)
        cbio.name = "customers.csv"
        try:
            await context.bot.send_document(
                chat_id=cid, document=cbio, filename="customers.csv",
                caption=f"📊 顧客台帳（{len(recs)}件）",
            )
        except Exception:
            log.exception("CSV送信失敗")
    ok = await _send_backup(
        context.bot, cid, caption="🔁 全データバックアップ（記憶・知識・顧客・予定）"
    )
    if not ok and not recs:
        await update.message.reply_text("書き出すデータがまだありません。")


async def _build_prompt(idea: str) -> str:
    """要望から、そのまま使える高品質プロンプトを生成して返す。"""
    resp = await claude.messages.create(
        model=MODEL,
        max_tokens=2500,
        system=PROMPT_BUILDER_SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=[{
            "role": "user",
            "content": f"次の要望に対する完成プロンプトを作ってください:\n{idea.strip()}",
        }],
    )
    return "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()


async def cmd_prompt(update, context):
    """/prompt 作りたいこと → そのまま使える高品質プロンプトを自動生成。"""
    cid = update.effective_chat.id
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "🧩 自動プロンプト作成\n"
            "使い方: /prompt 作りたいこと（モード中はそのまま要望を送るだけでOK）\n"
            "例: 物件紹介のキャッチコピーを量産するAI\n"
            "例: 商談メモから議事録を作るプロンプト\n"
            "例: お客様の断り文句への切り返しを考える営業コーチ"
        )
        return
    await context.bot.send_chat_action(chat_id=cid, action=constants.ChatAction.TYPING)
    try:
        text = await _build_prompt(parts[1].strip())
    except Exception:
        log.exception("プロンプト生成失敗")
        await update.message.reply_text("⚠️ プロンプト生成中にエラーが発生しました。")
        return
    for c in split(text or "(生成できませんでした)"):
        await update.message.reply_text(c)


async def cmd_promptmode(update, context):
    """プロンプト作成モードに切替（プレーンなメッセージ＝プロンプト生成）。"""
    modes[update.effective_chat.id] = "prompt"
    await update.message.reply_text(
        "🧩 自動プロンプト作成モードに切替えました。\n"
        "作りたいことを送ると、そのまま使える高品質プロンプトを返します。\n"
        "通常のアシスタントに戻す: /chat"
    )


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


async def _cc_oneshot(chat_id: int, prompt: str) -> str:
    """会話/秘書から Claude Code を1回実行し、結果テキストを返す（run_claude_code 用）。"""
    if not _CC:
        return "Claude Code は未導入です（claude-agent-sdk 未インストール）。"
    opt = ClaudeAgentOptions(
        cwd=CWD, permission_mode=PMODE, allowed_tools=TOOLS_CC, max_turns=CCTURNS
    )
    if ccsess.get(chat_id):
        opt.resume = ccsess[chat_id]
    parts: list[str] = []
    sid = fin = None
    try:
        async for m in query(prompt=prompt, options=opt):
            if isinstance(m, AssistantMessage):
                t = "\n".join(
                    b.text for b in m.content if isinstance(b, TextBlock) and b.text
                ).strip()
                if t:
                    parts.append(t)
            elif isinstance(m, ResultMessage):
                sid = m.session_id
                fin = m.result
    except Exception as e:
        log.exception("CC oneshot 失敗")
        return f"Claude Code 実行エラー: {e}"
    if sid:
        ccsess[chat_id] = sid
    return (fin or "\n".join(parts) or "(出力なし)")[:6000]


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
    mode_line = (
        "🧩 いまは自動プロンプト作成モードです。作りたいことを送ると、そのまま使える"
        "高品質プロンプトを返します。\n通常のアシスタント（CRM/メール/電話/検索/ファイル生成）"
        "に切替: /chat\n\n"
        if DEFAULT_MODE == "prompt" else ""
    )
    await update.message.reply_text(
        "🤖 最強 Claude ボット v4\n\n"
        + mode_line
        + "🧩 /prompt 高品質プロンプト自動作成 / 💬 /chat フルアシスタント\n"
        "🌐 自動ウェブ検索 / 🏭 ファイル生成 / 🧠 記憶 / ⏰ 定時タスク\n"
        "🖼 画像 / 📄 PDF / 🎤 音声 / 🛠 /code\n\n"
        "/help で詳細"
    )


async def c_help(update, context):
    await update.message.reply_text(
        "💬 コマンドは覚えなくてOK。やりたいことを普通に話すだけで動きます。\n"
        "（例:「毎朝7時にニュース送って」「リマインダー見せて」「田中さんを深掘りして」）\n\n"
        "できること:\n"
        "・💬 テキスト → ⚡表示で回答（🌐必要なら自動検索）\n"
        "・🏭 ファイル作成 → 「売上の棒グラフ作って」「請求書のExcel作って」等で\n"
        "  実際にファイルを生成して送信\n"
        "・🧠 名前や好みを伝えると自動で記憶（/memory で確認, /forget で消去）\n"
        "・⏰ /schedule HH:MM 指示 → 毎日その時刻に自動実行して送信\n"
        "・📞 /call 番号 用件 → 今すぐ電話してAIが応対（要認可・Twilio）\n"
        "・⏰📞 /callat HH:MM 番号 用件 → 毎日その時刻に自動で電話\n"
        "・🤖 /proactive HH:MM → 毎朝☀️ブリーフィングを自動送信（/briefing で今すぐ）\n"
        "  └ 今日の予定・要フォロー顧客・未読メールを集約して提案\n"
        "・📸 名刺を撮って送るだけ → 会社名・連絡先を読み取り顧客台帳に自動登録\n"
        "・🔔 「フォロー漏れない？」→ しばらく連絡してない顧客を抽出して打ち手を提案\n"
        "・🔍 /dig 顧客名 → 過去ログを深掘りし、次の打ち手（再訪/電話/メール案）を提案\n"
        "・🔎 「〇〇について記録あった？」→ 記憶・知識・顧客台帳を横断検索\n"
        "・🔁 /export → 顧客CSV＋全データを書き出し（毎朝も自動バックアップ）\n"
        "・🎯 /task 目標 → 複雑な目標を丸投げ。自分で調べ・作り・成果物まで出す\n"
        "・🧩 /prompt 作りたいこと → そのまま使える高品質プロンプトを自動作成\n"
        "・🔗 /n8n → n8n ワークフローを起動（会話/taskからも自動で呼べる）\n"
        "・🌐 MCP連携 → MCP_SERVERS 設定で Slack/GitHub/Google 等のツールを自律使用\n"
        "・🖼 写真 / 📄 PDF・文書 / 🎤 音声メッセージ\n"
        "・🛠 /code → Claude Code（要認可）\n\n"
        "/memory 記憶一覧 ・ /forget 記憶消去 ・ /schedules 予定一覧\n"
        "/reminders リマインダー一覧（削除は /unremind 番号）\n"
        "/chat ・ /code ・ /reset ・ /status ・ /update（最新版に自己更新）"
    )


async def c_memory(update, context):
    mems = get_memory(update.effective_chat.id)
    if not mems:
        await update.message.reply_text("🧠 まだ記憶はありません。会話から自動で覚えていきます。")
        return
    await update.message.reply_text("🧠 記憶していること:\n" + "\n".join(f"・{m}" for m in mems))


async def c_forget(update, context):
    memory.pop(_dk(update.effective_chat.id), None)
    _save_json(MEM_PATH, memory)
    await update.message.reply_text("🧠 記憶を消去しました。")


async def c_knowledge(update, context):
    kb = get_knowledge(update.effective_chat.id)
    if not kb:
        await update.message.reply_text(
            "📚 知識ベースは空です。資料を送って「これを覚えて」と頼むと登録されます。"
        )
        return
    lines = ["📚 登録済みの知識ベース:"]
    for i, item in enumerate(kb, 1):
        lines.append(f"{i}. {item.get('title', 'メモ')}（{len(item.get('content', ''))}字）")
    lines.append("\n全消去: /forget_kb")
    await update.message.reply_text("\n".join(lines))


async def c_forget_kb(update, context):
    knowledge.pop(_dk(update.effective_chat.id), None)
    _save_json(KB_PATH, knowledge)
    await update.message.reply_text("📚 知識ベースを消去しました。")


async def c_customers(update, context):
    recs = customers.get(_dk(update.effective_chat.id), {})
    if not recs:
        await update.message.reply_text(
            "🗂 顧客台帳は空です。名刺の写真や「〇〇社の商談メモ：…」と送ると登録されます。\n"
            "状況確認は「〇〇社の状況教えて」と聞くだけ。"
        )
        return
    items = sorted(recs.items(), key=lambda kv: kv[1].get("updated", ""), reverse=True)
    lines = ["🗂 顧客台帳:"]
    for n, r in items[:40]:
        lines.append(f"・{n}（最終 {r.get('updated', '?')}・{len(r.get('log', []))}件）")
    await update.message.reply_text("\n".join(lines))


DIG_INSTRUCTION = (
    "次は訪問営業の顧客『{name}』(最終更新 {updated}・記録{count}件)の商談履歴です。"
    "これを深掘り分析し、次を簡潔に日本語で提案してください。\n"
    "① 状況サマリ（3行以内・今どの段階か）\n"
    "② 相手の関心・懸念・温度感（読み取れる範囲で）\n"
    "③ 次の打ち手を3案（それぞれ 再訪 / 電話 / メール のどれが最適かと、一言の狙い）\n"
    "④ すぐ使えるひと言（電話の切り出しトーク、またはメール下書き2〜3行）\n"
    "履歴に無いことは断定せず『(推測)』と明記する。前置きは不要。\n\n"
    "[商談履歴]\n{log}"
)


async def cmd_dig(update, context):
    """/dig 顧客名 → 過去ログを深掘りし、次の打ち手を提案。"""
    cid = update.effective_chat.id
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        recs = customers.get(_dk(cid), {})
        hint = ""
        if recs:
            names = "、".join(list(recs.keys())[:8])
            hint = f"\n登録済み: {names}"
        await update.message.reply_text(
            "🔍 顧客の深掘り\n使い方: /dig 顧客名\n"
            "過去の商談ログを読み、次の打ち手（再訪/電話/メール案）まで提案します。" + hint
        )
        return
    name, rec = find_customer(cid, parts[1].strip())
    if not rec:
        await update.message.reply_text(
            f"『{parts[1].strip()}』の記録はまだありません。/customers で一覧を確認してください。"
        )
        return
    await context.bot.send_chat_action(chat_id=cid, action=constants.ChatAction.TYPING)
    history = "\n".join(rec.get("log", [])) or "(記録なし)"
    prompt = DIG_INSTRUCTION.format(
        name=name, updated=rec.get("updated", "?"), count=len(rec.get("log", [])), log=history
    )
    try:
        text = await _claude_oneshot(cid, prompt)
    except Exception:
        log.exception("深掘り失敗")
        await update.message.reply_text("⚠️ 分析中にエラーが発生しました。")
        return
    for c in split(f"🔍 {name} の深掘り\n\n" + text):
        await update.message.reply_text(c)


async def cmd_links(update, context):
    """/links（一覧）/ links キーワード（呼び出し）— よく使うURL。"""
    cid = update.effective_chat.id
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) >= 2 and parts[1].strip():
        await update.message.reply_text(_open_link(cid, parts[1].strip()))
    else:
        await update.message.reply_text(_list_links_text(cid))


async def cmd_report(update, context):
    """/report — 今日の営業日報を作成して送る。"""
    cid = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=cid, action=constants.ChatAction.TYPING)
    try:
        text = await _compose_daily_report(cid)
    except Exception:
        log.exception("日報作成失敗")
        await update.message.reply_text("⚠️ 日報の作成に失敗しました。")
        return
    for c in split("📊 本日の営業日報\n\n" + text):
        await update.message.reply_text(c)


async def cmd_team(update, context):
    """/team（一覧）/ team 名前（検索） — 社内チーム名簿を引く。"""
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) >= 2 and parts[1].strip():
        await update.message.reply_text(_lookup_member_text(parts[1].strip()))
    else:
        await update.message.reply_text(_list_team_text())


async def c_chat(update, context):
    modes[update.effective_chat.id] = "chat"
    await update.message.reply_text(
        "💬 フルアシスタントに切替（CRM・メール・電話・検索・ファイル生成など全機能）。\n"
        "プロンプト作成に戻す: /promptmode"
    )


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


async def cmd_awaken(update, context):
    """⚡ 覚醒診断: 今ONの機能と、未開放機能を解禁する“あなた専用コマンド”を表示。"""
    u = update.effective_user.id if update.effective_user else 0

    def mark(b: bool) -> str:
        return "✅" if b else "⬜"

    status = [
        "⚡ 覚醒ステータス",
        f"{mark(True)} 💬 会話・🌐検索・🏭ファイル生成・🧠記憶・🎯タスク（標準で無双）",
        f"{mark(bool(IDS))} 🔓 本人認証（電話・/code の解禁キー）",
        f"{mark(_email_ready())} 📧 メール送受信",
        f"{mark(_twilio_ready())} 📞 電話発信",
        f"{mark(bool(MCP_SERVERS))} 🌐 MCP連携（Slack/GitHub/Google/n8n）",
        f"{mark(_CC)} 🛠 Claude Code（PC実作業）",
        f"\n👤 あなたのTelegram ID: {u}",
    ]
    await update.message.reply_text("\n".join(status))

    # 未開放のものだけ、貼るだけで解禁できるコマンドを生成（IDは埋め込み済み）
    miss: list[str] = []
    if not IDS:
        miss.append(f"setenv ALLOWED_TELEGRAM_USER_IDS {u}    # ←あなた専用・記入不要")
    if not _email_ready():
        miss.append('setenv GMAIL_ADDRESS あなた@gmail.com')
        miss.append('setenv GMAIL_APP_PASSWORD "16桁のアプリパスワード"')
    if not _twilio_ready():
        miss.append('setenv TWILIO_ACCOUNT_SID ACxxxxxxxx')
        miss.append('setenv TWILIO_AUTH_TOKEN  xxxxxxxx')
        miss.append('setenv TWILIO_FROM_NUMBER "+1xxxxxxxxxx"')
    if not MCP_SERVERS:
        miss.append('# setenv MCP_SERVERS \'[{"type":"url","name":"n8n","url":"https://YOUR/mcp-server/http","authorization_token":"TOKEN"}]\'')

    if not miss:
        await update.message.reply_text("🎉 すべて覚醒済みです。これ以上の鍵は不要。無双状態。")
        return

    plist = "~/Library/LaunchAgents/com.martialarts.telegram-bot.plist"
    script = (
        f'PLIST={plist}\n'
        'setenv(){ /usr/libexec/PlistBuddy -c "Delete :EnvironmentVariables:$1" "$PLIST" 2>/dev/null; '
        '/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:$1 string $2" "$PLIST"; }\n'
        + "\n".join(miss)
        + '\nlaunchctl unload "$PLIST" 2>/dev/null; pkill -9 -f mega_bot.py; sleep 3; launchctl load "$PLIST"'
    )
    await update.message.reply_text(
        "↓ これを Mac のターミナルに貼れば未開放の機能が解禁されます"
        "（値の部分だけ自分のものに）:\n\n```\n" + script + "\n```",
        parse_mode=constants.ParseMode.MARKDOWN,
    )


async def c_status(update, context):
    cid = update.effective_chat.id
    m = modes[cid]
    jq = "ON" if context.application.job_queue is not None else "OFF (未導入)"
    n_tasks = len([s for s in schedules if s["chat_id"] == cid])
    n_calls = len([s for s in call_schedules if s["chat_id"] == cid])
    n_rem = len(_future_reminders(cid))
    mode_label = {"code": "🛠 Code", "prompt": "🧩 Prompt"}.get(m, "💬 Chat")
    await update.message.reply_text(
        f"モード: {mode_label}\n"
        f"モデル: {MODEL} (effort={EFFORT}, max={MAXTOK})\n"
        f"🌐 ウェブ検索: {'ON' if WEB_SEARCH else 'OFF'}\n"
        f"🏭 ファイル生成: {'ON' if CODE_EXEC else 'OFF'}\n"
        f"🌐 MCP接続: {len(MCP_SERVERS)}件\n"
        f"🧠 記憶件数: {len(get_memory(cid))}\n"
        f"⏰ スケジューラ: {jq}（定時タスク{n_tasks}・自動電話{n_calls}・リマインダー{n_rem}）\n"
        f"☀️ 朝ブリーフィング: {'ON' if str(cid) in proactive else 'OFF'}\n"
        f"👥 チーム共有: {'ON' if TEAM_MODE else 'OFF'} / 🗂 顧客 {len(customers.get(_dk(cid), {}))}件\n"
        f"📧 メール送信: {'利用可' if _email_ready() else '未設定'}\n"
        f"💬 Slack送信: {'利用可' if _slack_ready() else '未設定'} / 👥 名簿 {len(team_members)}名\n"
        f"📞 電話発信: {'利用可' if _twilio_ready() else '未設定'}"
        f"（{'🗣双方向AI通話' if VOICE_AGENT_URL else '📢読み上げ'}・声: {TW_VOICE}）\n"
        f"🎤 音声: {'利用可' if _WHISPER else '不可'}\n"
        f"🛠 Claude Code連携: {'会話から利用可（実行前に確認）' if _CC else '不可'}\n"
        "✅ 方針: コマンド不要・話すだけで操作／外向き操作は実行前に確認・嘘の報告はしません"
    )


async def cmd_voice(update, context):
    """音声返信モードのON/OFF切替。"""
    cid = update.effective_chat.id
    if not _TTS:
        await update.message.reply_text("⚠️ 音声返信は未導入です（gTTS）。")
        return
    if cid in voice_mode:
        voice_mode.discard(cid)
        await update.message.reply_text("🔇 音声返信をOFFにしました。")
    else:
        voice_mode.add(cid)
        await update.message.reply_text(
            "🔊 音声返信をONにしました。これ以降テキストでも声で返します。"
            "（音声メッセージを送れば、いつでも声で返ってきます）"
        )


async def cmd_update(update, context):
    """最新コードを取得して自己更新・再起動（認可ユーザー専用）。"""
    u = update.effective_user.id if update.effective_user else None
    if not auth(u):
        await update.message.reply_text(f"⛔ /update は認可ユーザー専用 (ID: {u})。")
        return
    await update.message.reply_text("🔄 最新コードを取得しています…")
    try:
        async with httpx.AsyncClient(timeout=30) as cli:
            r = await cli.get(UPDATE_URL)
        if r.status_code != 200 or "def main" not in r.text:
            await update.message.reply_text(f"⚠️ 取得失敗 (HTTP {r.status_code})。")
            return
        path = os.path.abspath(__file__)
        # 構文チェック（壊れたコードで再起動ループに入らないため）
        try:
            compile(r.text, path, "exec")
        except SyntaxError as e:
            await update.message.reply_text(f"⚠️ 取得コードに構文エラー。更新中止: {e}")
            return
        # バックアップしてから置換
        try:
            with open(path + ".bak", "w", encoding="utf-8") as f:
                f.write(open(path, encoding="utf-8").read())
        except Exception:
            pass
        with open(path, "w", encoding="utf-8") as f:
            f.write(r.text)
        await update.message.reply_text(
            "✅ 更新しました。再起動します（数秒で復帰）。/status で確認してください。"
        )
        # 返信送信後に終了 → launchd(KeepAlive) が新コードで再起動
        loop = asyncio.get_event_loop()
        loop.call_later(1.5, lambda: os._exit(0))
    except Exception as e:
        log.exception("自己更新に失敗")
        await update.message.reply_text(f"⚠️ 更新に失敗しました: {e}")


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
    if modes[cid] == "prompt":
        try:
            text = await _build_prompt(update.message.text)
        except Exception:
            log.exception("プロンプト生成失敗")
            await update.message.reply_text("⚠️ プロンプト生成中にエラーが発生しました。")
            return
        for c in split(text or "(生成できませんでした)"):
            await update.message.reply_text(c)
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
    cap = update.message.caption or (
        "この画像を確認してください。もし名刺なら、会社名・氏名・役職・電話番号・"
        "メールアドレス・住所を正確に読み取り、save_customer で顧客台帳に登録した上で、"
        "読み取った内容を整理して報告してください（会社名を顧客名にする）。"
        "ホワイトボードや書類など他の情報なら、要点をテキスト化して説明してください。"
        "名刺・書類でなければ、画像の内容を説明してください。"
    )
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
    await answer(update, context, cid, text, history_repr=f"[音声] {text}", voice_out=True)


# --------------------------------------------------------------------------- #
# エラー / 起動
# --------------------------------------------------------------------------- #


async def on_err(update, context):
    e = context.error
    if isinstance(e, Conflict):
        log.error("Conflict検出: 別インスタンスが getUpdates 実行中の可能性。")
        return
    log.exception("未処理例外", exc_info=e)


# 📋 Telegram の「/」メニューに出すコマンド一覧（タップで選べる＝覚えなくてよい）
BOT_COMMANDS = [
    ("briefing", "☀️ 今日のまとめ（予定・要フォロー・メール）"),
    ("assist", "🤖 先回りで提案してもらう"),
    ("proactive", "⏰ 毎朝の自動ブリーフィングを設定"),
    ("task", "🎯 目標を丸投げして自動でやってもらう"),
    ("prompt", "🧩 やりたいことから高品質プロンプトを自動作成"),
    ("promptmode", "🧩 プロンプト作成モードにする"),
    ("chat", "💬 フルアシスタントに戻す"),
    ("customers", "🗂 顧客台帳を見る"),
    ("dig", "🔍 顧客を深掘り＆次の打ち手を提案"),
    ("report", "📊 今日の営業日報を作成"),
    ("links", "🔖 よく使うURLを呼び出す（一言で開く）"),
    ("team", "👥 社内チーム名簿を引く（名前・メール・Slack ID）"),
    ("export", "📊 顧客CSV＋全データを書き出す"),
    ("call", "📞 電話をかける（番号 用件）"),
    ("callat", "📞 毎日決まった時刻に自動で電話"),
    ("schedule", "📅 毎日決まった時刻に自動実行"),
    ("schedules", "📅 登録した定時タスク一覧"),
    ("reminders", "⏰ 予定中のリマインダー一覧"),
    ("memory", "🧠 覚えていることを見る"),
    ("forget", "🧠 記憶を消す"),
    ("knowledge", "📚 覚えさせた資料を見る"),
    ("voice", "🔊 音声返信のON/OFF"),
    ("awaken", "⚡ 覚醒診断＆未開放機能を解禁する手順"),
    ("status", "📊 今の設定・状態を見る"),
    ("reset", "🔄 会話の流れをリセット"),
    ("update", "🆙 最新版に更新する"),
    ("help", "❓ できることの一覧"),
]


async def post_init(app: Application):
    await app.bot.delete_webhook(drop_pending_updates=True)
    try:
        await app.bot.set_my_commands(
            [BotCommand(c, d) for c, d in BOT_COMMANDS]
        )
    except Exception:
        log.exception("コマンドメニュー登録に失敗")
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
    for cid_str, conf in autoreport.items():
        if _register_autoreport(app, cid_str, conf):
            restored += 1
    # 未来の単発リマインダーだけ復元（過去のものは破棄）
    global reminders
    now_ts = dt.datetime.now(LOCAL_TZ).timestamp()
    reminders = [r for r in reminders if r.get("ts", 0) > now_ts]
    _save_json(REM_PATH, reminders)
    for rem in reminders:
        if _register_reminder(app, rem):
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

    global _app
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    _app = app
    app.add_handler(CommandHandler("start", c_start))
    app.add_handler(CommandHandler("help", c_help))
    app.add_handler(CommandHandler("memory", c_memory))
    app.add_handler(CommandHandler("forget", c_forget))
    app.add_handler(CommandHandler("knowledge", c_knowledge))
    app.add_handler(CommandHandler("forget_kb", c_forget_kb))
    app.add_handler(CommandHandler("customers", c_customers))
    app.add_handler(CommandHandler("dig", cmd_dig))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("links", cmd_links))
    app.add_handler(CommandHandler("team", cmd_team))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("schedules", cmd_schedules))
    app.add_handler(CommandHandler("unschedule", cmd_unschedule))
    app.add_handler(CommandHandler("call", cmd_call))
    app.add_handler(CommandHandler("callat", cmd_callat))
    app.add_handler(CommandHandler("callats", cmd_callats))
    app.add_handler(CommandHandler("uncallat", cmd_uncallat))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CommandHandler("unremind", cmd_unremind))
    app.add_handler(CommandHandler("proactive", cmd_proactive))
    app.add_handler(CommandHandler("assist", cmd_assist))
    app.add_handler(CommandHandler("briefing", cmd_briefing))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("prompt", cmd_prompt))
    app.add_handler(CommandHandler("promptmode", cmd_promptmode))
    app.add_handler(CommandHandler("task", cmd_task))
    app.add_handler(CommandHandler("n8n", cmd_n8n))
    app.add_handler(CommandHandler("chat", c_chat))
    app.add_handler(CommandHandler("code", c_code))
    app.add_handler(CommandHandler("reset", c_reset))
    app.add_handler(CommandHandler("status", c_status))
    app.add_handler(CommandHandler("awaken", cmd_awaken))
    app.add_handler(CommandHandler("voice", cmd_voice))
    app.add_handler(CommandHandler("update", cmd_update))
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
