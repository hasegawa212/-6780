"""最強の Telegram AI ボット (Claude 搭載).

設計目標:
- 二重起動による `telegram.error.Conflict`（getUpdates 競合）を根絶する
- クラッシュせず、graceful にシャットダウンする
- Claude (claude-opus-4-8) で賢く応答し、会話履歴を保持する

シングルインスタンスロック + 起動時の webhook 削除 + Conflict ハンドラの
三段構えで「only one bot instance is running」エラーを防ぎます。
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from collections import defaultdict, deque
from pathlib import Path

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

# --------------------------------------------------------------------------- #
# 設定 (環境変数から読み込み)
# --------------------------------------------------------------------------- #

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Claude モデル。最新かつ最も賢い Opus を既定にする。
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")
# 思考の深さ: low | medium | high | max
EFFORT = os.environ.get("CLAUDE_EFFORT", "medium")
MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "4096"))

# 1 チャットあたり保持する会話ターン数 (user+assistant のペア)
HISTORY_TURNS = int(os.environ.get("HISTORY_TURNS", "12"))

SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "あなたは Telegram 上で動く親切で有能なAIアシスタントです。"
    "簡潔で分かりやすく、必要に応じて具体例を交えて回答してください。"
    "ユーザーの言語に合わせて応答します。",
)

# シングルインスタンスロックファイル。二重起動を物理的に防ぐ。
LOCK_PATH = Path(os.environ.get("BOT_LOCK_PATH", "/tmp/telegram-ai-bot.lock"))

# Telegram の 1 メッセージ上限
TELEGRAM_MAX_LEN = 4096

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("telegram-ai-bot")


# --------------------------------------------------------------------------- #
# シングルインスタンスロック
# --------------------------------------------------------------------------- #


class SingleInstanceLock:
    """fcntl ベースの排他ロック。

    同じマシンで 2 つ目のボットを起動しようとするとここで弾かれるため、
    2 つのプロセスが同時に getUpdates を叩く事故 (Conflict) を防げます。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        import fcntl  # Unix 専用。Windows では下の except で握りつぶす。

        self._fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._fd)
            self._fd = None
            logger.error(
                "別のボットインスタンスが既に起動しています (lock: %s)。"
                "二重起動は Telegram の getUpdates 競合を引き起こすため中止します。",
                self._path,
            )
            sys.exit(1)
        os.ftruncate(self._fd, 0)
        os.write(self._fd, str(os.getpid()).encode())
        logger.info("シングルインスタンスロックを取得しました (pid=%s)", os.getpid())

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
# 会話履歴 (チャットごと)
# --------------------------------------------------------------------------- #

# chat_id -> 直近のメッセージ列 (Anthropic 形式の dict)
_histories: dict[int, deque[dict]] = defaultdict(
    lambda: deque(maxlen=HISTORY_TURNS * 2)
)


def reset_history(chat_id: int) -> None:
    _histories.pop(chat_id, None)


# --------------------------------------------------------------------------- #
# Claude 呼び出し
# --------------------------------------------------------------------------- #

claude = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


async def generate_reply(chat_id: int, user_text: str) -> str:
    """会話履歴を踏まえて Claude に応答を生成させる。"""
    history = _histories[chat_id]
    history.append({"role": "user", "content": user_text})

    try:
        response = await claude.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            messages=list(history),
        )
    except Exception:
        # 失敗したらユーザーの発言を履歴から取り除いて整合性を保つ
        if history and history[-1]["role"] == "user":
            history.pop()
        raise

    text = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()

    if not text:
        text = "(応答を生成できませんでした。もう一度お試しください。)"

    history.append({"role": "assistant", "content": text})
    return text


# --------------------------------------------------------------------------- #
# Telegram ハンドラ
# --------------------------------------------------------------------------- #


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "こんにちは！ Claude 搭載の AI ボットです 🤖\n"
        "メッセージを送れば何でもお答えします。\n\n"
        "/help — 使い方\n"
        "/reset — 会話履歴をリセット"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "使い方:\n"
        "・普通にメッセージを送ると Claude が応答します。\n"
        "・会話の文脈は保持されます。\n\n"
        "コマンド:\n"
        "/start — はじめる\n"
        "/reset — この会話の履歴を消去\n"
        "/help — このヘルプ\n\n"
        f"モデル: {MODEL} (effort={EFFORT})"
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reset_history(update.effective_chat.id)
    await update.message.reply_text("会話履歴をリセットしました ✨")


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_text = update.message.text

    # 「入力中…」を表示しつつ生成
    await context.bot.send_chat_action(
        chat_id=chat_id, action=constants.ChatAction.TYPING
    )

    try:
        reply = await generate_reply(chat_id, user_text)
    except Exception:
        logger.exception("Claude 応答の生成に失敗しました")
        await update.message.reply_text(
            "⚠️ 応答の生成中にエラーが発生しました。少し待って再度お試しください。"
        )
        return

    # Telegram の文字数上限を超える場合は分割送信
    for chunk in _split_message(reply):
        await update.message.reply_text(chunk)


def _split_message(text: str, limit: int = TELEGRAM_MAX_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # なるべく改行で区切る
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """全ハンドラ共通のエラーハンドラ。

    特に Conflict (getUpdates 競合) はクラッシュさせず、明確に警告する。
    """
    err = context.error
    if isinstance(err, Conflict):
        logger.error(
            "Conflict を検出しました。別のインスタンスが同じトークンで "
            "getUpdates を実行している可能性があります。"
            "古いプロセスを停止してください（このプロセスは継続します）。"
        )
        return
    logger.exception("ハンドラで未処理の例外が発生しました", exc_info=err)


# --------------------------------------------------------------------------- #
# 起動
# --------------------------------------------------------------------------- #


async def _post_init(app: Application) -> None:
    """ポーリング開始前に webhook を消し、保留中の更新を捨てる。

    webhook が残っていると getUpdates と競合するため、ここで確実に削除する。
    """
    await app.bot.delete_webhook(drop_pending_updates=True)
    me = await app.bot.get_me()
    logger.info("ボット起動: @%s (id=%s)", me.username, me.id)


def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.error("環境変数 TELEGRAM_BOT_TOKEN が設定されていません。")
        sys.exit(1)
    if not ANTHROPIC_API_KEY:
        logger.error("環境変数 ANTHROPIC_API_KEY が設定されていません。")
        sys.exit(1)

    lock = SingleInstanceLock(LOCK_PATH)
    try:
        lock.acquire()
    except ImportError:
        # fcntl が無い環境 (Windows) ではロックをスキップ
        logger.warning("fcntl が利用できないためシングルインスタンスロックを省略します。")

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)

    try:
        # drop_pending_updates=True で、起動時に溜まった更新を破棄。
        # PTB が SIGINT/SIGTERM を捕捉して graceful にシャットダウンする。
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
