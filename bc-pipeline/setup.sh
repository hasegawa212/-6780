#!/bin/bash
set -e

mkdir -p ~/telegram-ai-bot
cd ~/telegram-ai-bot

python3 -m venv venv
source venv/bin/activate

python -m pip install --upgrade pip
pip install python-telegram-bot openai anthropic python-dotenv

cat > .env <<'EOF'
OPENAI_API_KEY=YOUR_OPENAI_API_KEY_HERE
ANTHROPIC_API_KEY=YOUR_ANTHROPIC_API_KEY_HERE
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN_HERE
DEFAULT_MODEL=openai
OPENAI_MODEL=gpt-4o-mini
ANTHROPIC_MODEL=claude-3-5-sonnet-latest
EOF

chmod 600 .env

cat > bot.py <<'PY'
import os
import logging
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from openai import OpenAI
import anthropic

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "openai").strip().lower()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest").strip()

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")

openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)

user_modes = {}

HELP_TEXT = """
使い方:
/start - 起動
/help - ヘルプ
/gpt - OpenAIに切替
/claude - Claudeに切替
/model - 今の利用モデル確認
/reset - 会話モードを初期化

そのままメッセージを送れば返答します。
""".strip()

def get_mode(user_id: int) -> str:
    return user_modes.get(user_id, DEFAULT_MODEL)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Botを起動しました。\n\n" + HELP_TEXT)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def model_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = get_mode(update.effective_user.id)
    await update.message.reply_text(f"現在のモデル: {mode}")

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_modes.pop(update.effective_user.id, None)
    await update.message.reply_text(f"会話モードを初期化しました。現在: {DEFAULT_MODEL}")

async def gpt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not openai_client:
        await update.message.reply_text("OPENAI_API_KEY が未設定です。")
        return
    user_modes[update.effective_user.id] = "openai"
    await update.message.reply_text(f"OpenAIに切り替えました: {OPENAI_MODEL}")

async def claude_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not anthropic_client:
        await update.message.reply_text("ANTHROPIC_API_KEY が未設定です。")
        return
    user_modes[update.effective_user.id] = "claude"
    await update.message.reply_text(f"Claudeに切り替えました: {ANTHROPIC_MODEL}")

def ask_openai(user_text: str) -> str:
    if not openai_client:
        return "OPENAI_API_KEY が未設定です。"
    response = openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Respond in Japanese unless the user asks otherwise."},
            {"role": "user", "content": user_text},
        ],
    )
    return response.choices[0].message.content or "応答が空でした。"

def ask_claude(user_text: str) -> str:
    if not anthropic_client:
        return "ANTHROPIC_API_KEY が未設定です。"
    response = anthropic_client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1000,
        system="You are a helpful assistant. Respond in Japanese unless the user asks otherwise.",
        messages=[
            {"role": "user", "content": user_text}
        ],
    )
    parts = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip() or "応答が空でした。"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    mode = get_mode(user_id)
    user_text = update.message.text

    try:
        await update.message.chat.send_action("typing")
    except Exception:
        pass

    try:
        if mode == "claude":
            reply = ask_claude(user_text)
        else:
            reply = ask_openai(user_text)

        if len(reply) <= 4000:
            await update.message.reply_text(reply)
        else:
            for i in range(0, len(reply), 4000):
                await update.message.reply_text(reply[i:i+4000])

    except Exception as e:
        await update.message.reply_text(f"エラー: {e}")

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("model", model_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("gpt", gpt_cmd))
    app.add_handler(CommandHandler("claude", claude_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
PY

cat > run.sh <<'SH2'
#!/bin/bash
set -e
cd ~/telegram-ai-bot
source venv/bin/activate
python bot.py
SH2

chmod +x setup.sh run.sh

echo
echo "[OK] Files created:"
echo "  ~/telegram-ai-bot/.env"
echo "  ~/telegram-ai-bot/bot.py"
echo "  ~/telegram-ai-bot/run.sh"
echo
echo "Next:"
echo "  1) edit ~/telegram-ai-bot/.env"
echo "  2) replace placeholders with your real keys"
echo "  3) run: cd ~/telegram-ai-bot && bash setup.sh"
