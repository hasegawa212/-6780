"""最強の Telegram ボット (Claude 統合版).

1 つのボットで以下をすべてこなします:
- 💬 Claude (claude-opus-4-8) とのチャット（文脈を記憶）
- 🖼 画像を送ると内容を理解して回答（Vision）
- 🛠 /code モードで Claude Code を起動し、実際にファイル編集・コマンド実行

そして二重起動による `telegram.error.Conflict` を三段構えで根絶:
  1) シングルインスタンスロック (fcntl)
  2) 起動時の webhook 削除 (drop_pending_updates)
  3) Conflict 対応エラーハンドラ
"""

from __future__ import annotations

import base64
import logging
import os
import signal
import sys
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

# Claude Code (任意機能)。未インストールでもチャット/画像は動く。
try:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    _CLAUDE_CODE_AVAILABLE = True
except Exception:  # pragma: no cover
    _CLAUDE_CODE_AVAILABLE = False

# --------------------------------------------------------------------------- #
# 設定
# --------------------------------------------------------------------------- #

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")
EFFORT = os.environ.get("CLAUDE_EFFORT", "medium")
MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "4096"))
HISTORY_TURNS = int(os.environ.get("HISTORY_TURNS", "12"))

SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "あなたは Telegram 上で動く親切で有能なAIアシスタントです。"
    "簡潔で分かりやすく回答し、ユーザーの言語に合わせて応答します。",
)

# /code モード（Claude Code 操作）を使える Telegram ユーザーID（カンマ区切り）
_raw_ids = os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "")
ALLOWED_USER_IDS: set[int] = {
    int(x) for x in _raw_ids.replace(" ", "").split(",") if x.strip().isdigit()
}
CLAUDE_CODE_CWD = os.environ.get("CLAUDE_CODE_CWD", os.getcwd())
CLAUDE_CODE_PERMISSION_MODE = os.environ.get(
    "CLAUDE_CODE_PERMISSION_MODE", "acceptEdits"
)
CLAUDE_CODE_ALLOWED_TOOLS = [
    t.strip()
    for t in os.environ.get(
        "CLAUDE_CODE_ALLOWED_TOOLS", "Read,Edit,Write,Bash,Glob,Grep"
    ).split(",")
    if t.strip()
]
CLAUDE_CODE_MAX_TURNS = int(os.environ.get("CLAUDE_CODE_MAX_TURNS", "30"))

LOCK_PATH = Path(os.environ.get("BOT_LOCK_PATH", "/tmp/telegram-mega-bot.lock"))
TELEGRAM_MAX_LEN = 4096

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("telegram-mega-bot")

claude = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


# --------------------------------------------------------------------------- #
# シングルインスタンスロック
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
                "別インスタンスが起動中です (lock: %s)。二重起動は Conflict の"
                "原因になるため中止します。",
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
# 状態（チャットごと）
# --------------------------------------------------------------------------- #

# chat_id -> "chat" | "code"
_modes: dict[int, str] = defaultdict(lambda: "chat")
# chat_id -> Claude チャット履歴
_histories: dict[int, Deque[dict]] = defaultdict(
    lambda: deque(maxlen=HISTORY_TURNS * 2)
)
# chat_id -> Claude Code セッションID
_cc_sessions: dict[int, str] = {}


def _authorized(user_id: int | None) -> bool:
    return user_id is not None and user_id in ALLOWED_USER_IDS


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


# --------------------------------------------------------------------------- #
# Claude チャット（テキスト / 画像）
# --------------------------------------------------------------------------- #


async def _claude_chat(chat_id: int, content) -> str:
    """content は str か Anthropic の content ブロックのリスト。"""
    history = _histories[chat_id]
    history.append({"role": "user", "content": content})
    try:
        resp = await claude.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            messages=list(history),
        )
    except Exception:
        if history and history[-1]["role"] == "user":
            history.pop()
        raise
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        text = "(応答を生成できませんでした。)"
    history.append({"role": "assistant", "content": text})
    return text


# --------------------------------------------------------------------------- #
# Claude Code 実行
# --------------------------------------------------------------------------- #


async def _run_claude_code(update: Update, context, chat_id: int, prompt: str) -> None:
    options = ClaudeAgentOptions(
        cwd=CLAUDE_CODE_CWD,
        permission_mode=CLAUDE_CODE_PERMISSION_MODE,
        allowed_tools=CLAUDE_CODE_ALLOWED_TOOLS,
        max_turns=CLAUDE_CODE_MAX_TURNS,
    )
    prev = _cc_sessions.get(chat_id)
    if prev:
        options.resume = prev

    new_sid: str | None = None
    final: str | None = None
    sent = False
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                parts = [
                    b.text
                    for b in message.content
                    if isinstance(b, TextBlock) and b.text
                ]
                text = "\n".join(parts).strip()
                if text:
                    for chunk in _split_message(text):
                        await update.message.reply_text(chunk)
                        sent = True
                await context.bot.send_chat_action(
                    chat_id=chat_id, action=constants.ChatAction.TYPING
                )
            elif isinstance(message, ResultMessage):
                new_sid = message.session_id
                final = message.result
    except Exception:
        logger.exception("Claude Code 実行に失敗")
        await update.message.reply_text("⚠️ Claude Code 実行中にエラーが発生しました。")
        return

    if new_sid:
        _cc_sessions[chat_id] = new_sid
    if final and not sent:
        for chunk in _split_message(final):
            await update.message.reply_text(chunk)
    elif not sent and not final:
        await update.message.reply_text("✅ 完了しました（出力なし）。")


# --------------------------------------------------------------------------- #
# コマンド
# --------------------------------------------------------------------------- #


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 最強の Claude ボットへようこそ！\n\n"
        "💬 メッセージを送ると Claude が返信\n"
        "🖼 画像を送ると内容を理解して回答\n"
        "🛠 /code でコーディングエージェント (Claude Code) を起動\n\n"
        "/help — 詳しい使い方"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "できること:\n"
        "・💬 テキストで質問 → Claude が回答（文脈を記憶）\n"
        "・🖼 写真を送る → 画像を解析して回答\n"
        "・🛠 /code → Claude Code モードに切替（要認可）。以降のメッセージで実際に\n"
        "   ファイル編集やコマンド実行を行います。/chat で通常チャットに戻る。\n\n"
        "コマンド:\n"
        "/chat — 通常チャットモード\n"
        "/code — Claude Code モード\n"
        "/reset — 現在モードの履歴/セッションを消去\n"
        "/status — 現在の状態\n"
        "/help — このヘルプ"
    )


async def cmd_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _modes[update.effective_chat.id] = "chat"
    await update.message.reply_text("💬 通常チャットモードに切り替えました。")


async def cmd_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not _CLAUDE_CODE_AVAILABLE:
        await update.message.reply_text(
            "⚠️ claude-agent-sdk が未インストールです。\n"
            "`pip install claude-agent-sdk` を実行してください。"
        )
        return
    if not _authorized(uid):
        await update.message.reply_text(
            f"⛔ /code は認可ユーザー専用です (あなたのID: {uid})。\n"
            "ALLOWED_TELEGRAM_USER_IDS への追加が必要です。"
        )
        return
    _modes[update.effective_chat.id] = "code"
    await update.message.reply_text(
        "🛠 Claude Code モードに切り替えました。\n"
        f"作業ディレクトリ: {CLAUDE_CODE_CWD}\n"
        "指示を送るとエージェントが作業します。/chat で戻れます。"
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if _modes[chat_id] == "code":
        _cc_sessions.pop(chat_id, None)
        await update.message.reply_text("🔄 Claude Code セッションをリセットしました。")
    else:
        _histories.pop(chat_id, None)
        await update.message.reply_text("🔄 会話履歴をリセットしました。")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    mode = _modes[chat_id]
    lines = [
        f"モード: {'🛠 Claude Code' if mode == 'code' else '💬 チャット'}",
        f"モデル: {MODEL} (effort={EFFORT})",
        f"Claude Code 利用可: {'はい' if _CLAUDE_CODE_AVAILABLE else 'いいえ (未インストール)'}",
    ]
    if mode == "code":
        lines += [
            f"作業ディレクトリ: {CLAUDE_CODE_CWD}",
            f"権限モード: {CLAUDE_CODE_PERMISSION_MODE}",
            f"セッション: {_cc_sessions.get(chat_id) or '(新規)'}",
        ]
    await update.message.reply_text("\n".join(lines))


# --------------------------------------------------------------------------- #
# メッセージ（テキスト・画像）
# --------------------------------------------------------------------------- #


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    chat_id = update.effective_chat.id
    text = update.message.text

    await context.bot.send_chat_action(
        chat_id=chat_id, action=constants.ChatAction.TYPING
    )

    if _modes[chat_id] == "code":
        uid = update.effective_user.id if update.effective_user else None
        if not _authorized(uid):
            await update.message.reply_text("⛔ /code は認可ユーザー専用です。")
            return
        await _run_claude_code(update, context, chat_id, text)
        return

    try:
        reply = await _claude_chat(chat_id, text)
    except Exception:
        logger.exception("Claude 応答生成に失敗")
        await update.message.reply_text("⚠️ 応答生成中にエラーが発生しました。")
        return
    for chunk in _split_message(reply):
        await update.message.reply_text(chunk)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.photo:
        return
    chat_id = update.effective_chat.id
    # コードモード中は画像を無視（チャットモードでのみ Vision）
    if _modes[chat_id] == "code":
        await update.message.reply_text(
            "🛠 Claude Code モード中は画像を扱えません。/chat で戻ってください。"
        )
        return

    await context.bot.send_chat_action(
        chat_id=chat_id, action=constants.ChatAction.TYPING
    )

    # 最大解像度の写真を取得して base64 化
    photo = update.message.photo[-1]
    tg_file = await photo.get_file()
    buf = await tg_file.download_as_bytearray()
    b64 = base64.standard_b64encode(bytes(buf)).decode("utf-8")
    caption = update.message.caption or "この画像について説明してください。"

    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": b64,
            },
        },
        {"type": "text", "text": caption},
    ]
    try:
        reply = await _claude_chat(chat_id, content)
    except Exception:
        logger.exception("画像応答生成に失敗")
        await update.message.reply_text("⚠️ 画像の解析中にエラーが発生しました。")
        return
    for chunk in _split_message(reply):
        await update.message.reply_text(chunk)


# --------------------------------------------------------------------------- #
# エラーハンドラ / 起動
# --------------------------------------------------------------------------- #


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, Conflict):
        logger.error(
            "Conflict 検出: 別インスタンスが同じトークンで getUpdates 実行中の"
            "可能性があります。古いプロセスを停止してください（本プロセスは継続）。"
        )
        return
    logger.exception("未処理の例外", exc_info=err)


async def _post_init(app: Application) -> None:
    await app.bot.delete_webhook(drop_pending_updates=True)
    me = await app.bot.get_me()
    logger.info("起動: @%s (id=%s)", me.username, me.id)


def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN が未設定です。")
        sys.exit(1)
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY が未設定です。")
        sys.exit(1)

    lock = SingleInstanceLock(LOCK_PATH)
    try:
        lock.acquire()
    except ImportError:
        logger.warning("fcntl 不可のためロックを省略します。")

    app = (
        ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(_post_init).build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("chat", cmd_chat))
    app.add_handler(CommandHandler("code", cmd_code))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
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
