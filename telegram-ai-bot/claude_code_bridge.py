"""Telegram → Claude Code ブリッジ.

Telegram にメッセージを送ると、Claude Code（コーディングエージェント）が
作業ディレクトリ上で動作し、結果を返します。`claude-agent-sdk` 経由で
Claude Code を プログラム的に駆動します。

⚠️ セキュリティ: このボットはホスト上でコード/コマンドを実行できる
エージェントを動かします。必ず `ALLOWED_TELEGRAM_USER_IDS` に
あなた自身の Telegram ユーザーID を設定してください。
未設定の場合は **誰も** 操作できません（フェイルクローズ）。

通常の AI チャットだけが欲しい場合は bot.py を使ってください。
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)
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

# --------------------------------------------------------------------------- #
# 設定
# --------------------------------------------------------------------------- #

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# ANTHROPIC_API_KEY は claude-agent-sdk が環境変数から自動で読み込む

# Claude Code を操作できる Telegram ユーザーID（カンマ区切り）。
# 取得方法: @userinfobot に話しかけると自分の数値IDが分かります。
_raw_ids = os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "")
ALLOWED_USER_IDS: set[int] = {
    int(x) for x in _raw_ids.replace(" ", "").split(",") if x.strip().isdigit()
}

# Claude Code が作業するディレクトリ（リポジトリのルートなど）
CWD = os.environ.get("CLAUDE_CODE_CWD", os.getcwd())

# 権限モード: default | acceptEdits | plan | bypassPermissions
# 非対話（ヘッドレス）で動かすため、ファイル編集を自動承認する acceptEdits を既定に。
# Bash 等も自動実行したい場合は bypassPermissions を指定（リスク高・要理解）。
PERMISSION_MODE = os.environ.get("CLAUDE_CODE_PERMISSION_MODE", "acceptEdits")

# 使用を許可するツール（カンマ区切り）
ALLOWED_TOOLS = [
    t.strip()
    for t in os.environ.get(
        "CLAUDE_CODE_ALLOWED_TOOLS", "Read,Edit,Write,Bash,Glob,Grep"
    ).split(",")
    if t.strip()
]

# 1 リクエストあたりの最大エージェントターン数
MAX_TURNS = int(os.environ.get("CLAUDE_CODE_MAX_TURNS", "30"))

# 任意: Claude Code が使うモデルを上書き（未指定なら Claude Code の既定）
MODEL = os.environ.get("CLAUDE_CODE_MODEL") or None

SYSTEM_PROMPT = os.environ.get("CLAUDE_CODE_SYSTEM_PROMPT") or None

LOCK_PATH = Path(os.environ.get("BOT_LOCK_PATH", "/tmp/telegram-claude-code.lock"))
TELEGRAM_MAX_LEN = 4096

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("telegram-claude-code")


# --------------------------------------------------------------------------- #
# シングルインスタンスロック（getUpdates 競合 = Conflict を防ぐ）
# --------------------------------------------------------------------------- #


class SingleInstanceLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        import fcntl

        self._fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._fd)
            self._fd = None
            logger.error(
                "別インスタンスが起動中です (lock: %s)。二重起動は Conflict の原因に"
                "なるため中止します。",
                self._path,
            )
            sys.exit(1)
        os.ftruncate(self._fd, 0)
        os.write(self._fd, str(os.getpid()).encode())
        logger.info("ロック取得 (pid=%s)", os.getpid())

    def release(self) -> None:
        if self._fd is not None:
            try:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(self._fd)
            self._fd = None
            try:
                self._path.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# セッション管理（チャットごとに Claude Code セッションを継続）
# --------------------------------------------------------------------------- #

_sessions: dict[int, str] = {}  # chat_id -> session_id


def _split_message(text: str, limit: int = TELEGRAM_MAX_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def _authorized(user_id: int | None) -> bool:
    return user_id is not None and user_id in ALLOWED_USER_IDS


# --------------------------------------------------------------------------- #
# Telegram ハンドラ
# --------------------------------------------------------------------------- #


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not _authorized(uid):
        await update.message.reply_text(
            f"⛔ 未認可のユーザーです (あなたのID: {uid})。\n"
            "管理者に ALLOWED_TELEGRAM_USER_IDS への追加を依頼してください。"
        )
        return
    await update.message.reply_text(
        "🛠 Claude Code ブリッジへようこそ。\n"
        "メッセージを送ると Claude Code が下記ディレクトリで作業します:\n"
        f"`{CWD}`\n\n"
        "/help — 使い方\n"
        "/reset — セッションを新規化\n"
        "/status — 現在の設定",
        parse_mode=constants.ParseMode.MARKDOWN,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "使い方:\n"
        "・「auth.py のバグを直して」「テストを実行して結果を教えて」など、\n"
        "  通常 Claude Code に頼むのと同じ指示を送ってください。\n"
        "・会話はチャットごとに継続されます（/reset で新規セッション）。\n\n"
        "コマンド:\n"
        "/reset — セッションを新規化\n"
        "/status — 作業ディレクトリ・権限モード・許可ツールを表示\n"
        "/help — このヘルプ"
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not _authorized(uid):
        return
    _sessions.pop(update.effective_chat.id, None)
    await update.message.reply_text("🔄 セッションをリセットしました。")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not _authorized(uid):
        return
    sid = _sessions.get(update.effective_chat.id)
    await update.message.reply_text(
        "現在の設定:\n"
        f"・作業ディレクトリ: {CWD}\n"
        f"・権限モード: {PERMISSION_MODE}\n"
        f"・許可ツール: {', '.join(ALLOWED_TOOLS)}\n"
        f"・最大ターン: {MAX_TURNS}\n"
        f"・モデル: {MODEL or 'Claude Code 既定'}\n"
        f"・セッション: {sid or '(新規)'}"
    )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    uid = update.effective_user.id if update.effective_user else None
    if not _authorized(uid):
        await update.message.reply_text(
            f"⛔ 未認可のユーザーです (あなたのID: {uid})。"
        )
        logger.warning("未認可ユーザーからのアクセス: %s", uid)
        return

    chat_id = update.effective_chat.id
    prompt = update.message.text

    await context.bot.send_chat_action(
        chat_id=chat_id, action=constants.ChatAction.TYPING
    )

    options = ClaudeAgentOptions(
        cwd=CWD,
        permission_mode=PERMISSION_MODE,
        allowed_tools=ALLOWED_TOOLS,
        max_turns=MAX_TURNS,
    )
    if MODEL:
        options.model = MODEL
    if SYSTEM_PROMPT:
        options.system_prompt = SYSTEM_PROMPT
    # 既存セッションがあれば継続
    prev = _sessions.get(chat_id)
    if prev:
        options.resume = prev

    new_session_id: str | None = None
    final_result: str | None = None
    sent_any = False

    try:
        async for message in query(prompt=prompt, options=options):
            # 途中経過（Claude のテキスト）を逐次返す
            if isinstance(message, AssistantMessage):
                parts = [
                    b.text for b in message.content if isinstance(b, TextBlock) and b.text
                ]
                text = "\n".join(parts).strip()
                if text:
                    for chunk in _split_message(text):
                        await update.message.reply_text(chunk)
                        sent_any = True
                # 長い作業中も「入力中…」を維持
                await context.bot.send_chat_action(
                    chat_id=chat_id, action=constants.ChatAction.TYPING
                )
            elif isinstance(message, ResultMessage):
                new_session_id = message.session_id
                final_result = message.result
    except Exception:
        logger.exception("Claude Code 実行に失敗しました")
        await update.message.reply_text(
            "⚠️ Claude Code の実行中にエラーが発生しました。ログを確認してください。"
        )
        return

    if new_session_id:
        _sessions[chat_id] = new_session_id

    # ResultMessage.result があり、まだ送っていなければ最終結果を送る
    if final_result and not sent_any:
        for chunk in _split_message(final_result):
            await update.message.reply_text(chunk)
    elif not sent_any and not final_result:
        await update.message.reply_text("✅ 完了しました（出力なし）。")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, Conflict):
        logger.error(
            "Conflict 検出: 別インスタンスが同じトークンで getUpdates 実行中の"
            "可能性があります。古いプロセスを停止してください。"
        )
        return
    logger.exception("未処理の例外", exc_info=err)


async def _post_init(app: Application) -> None:
    await app.bot.delete_webhook(drop_pending_updates=True)
    me = await app.bot.get_me()
    logger.info("起動: @%s (id=%s) cwd=%s", me.username, me.id, CWD)
    if not ALLOWED_USER_IDS:
        logger.warning(
            "ALLOWED_TELEGRAM_USER_IDS が未設定です。安全のため全ユーザーを"
            "拒否します。自分の Telegram ユーザーID を設定してください。"
        )


def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN が未設定です。")
        sys.exit(1)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY が未設定です。")
        sys.exit(1)

    lock = SingleInstanceLock(LOCK_PATH)
    try:
        lock.acquire()
    except ImportError:
        logger.warning("fcntl 不可のためロックを省略します。")

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(_post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)

    try:
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            stop_signals=(signal.SIGINT, signal.SIGTERM),
        )
    finally:
        lock.release()
        logger.info("シャットダウン完了。")


if __name__ == "__main__":
    main()
