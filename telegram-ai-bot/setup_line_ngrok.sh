#!/bin/bash
# LINE 受付AI を ngrok 運用へ切り替える 1 コマンドセットアップ。
# 使い方:
#   NGROK_AUTHTOKEN='＜あなたのauthtoken＞' bash <(curl -fsSL ＜このURL＞)
# 任意: NGROK_DOMAIN='xxx.ngrok-free.app' を併せて渡すと固定URL運用になる。
set -u

plist="$HOME/Library/LaunchAgents/com.martialarts.line-bot.plist"
BOT_DIR="$HOME/telegram-ai-bot"
RUN_URL="https://raw.githubusercontent.com/hasegawa212/-6780/refs/heads/claude/loving-pasteur-KqQsk/telegram-ai-bot/run_line.sh"

cd "$BOT_DIR" || { echo "❌ $BOT_DIR がありません"; exit 1; }
if [ -z "${NGROK_AUTHTOKEN:-}" ]; then
    echo "❌ NGROK_AUTHTOKEN を指定してください。"
    echo "   例: NGROK_AUTHTOKEN='2abc...' bash <(curl -fsSL $RUN_URL)"
    exit 1
fi
if [ ! -f "$plist" ]; then
    echo "❌ LINE用の常駐設定($plist)が見つかりません。先に常時起動の設定を済ませてください。"
    exit 1
fi

echo "▶ ngrok を確認/インストール…"
command -v ngrok >/dev/null || brew install ngrok || {
    echo "❌ ngrok のインストールに失敗。'brew install ngrok' を手動で試してください。"; exit 1;
}

echo "▶ authtoken を保存…"
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:NGROK_AUTHTOKEN string $NGROK_AUTHTOKEN" "$plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:NGROK_AUTHTOKEN $NGROK_AUTHTOKEN" "$plist"
if [ -n "${NGROK_DOMAIN:-}" ]; then
    /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:NGROK_DOMAIN string $NGROK_DOMAIN" "$plist" 2>/dev/null \
        || /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:NGROK_DOMAIN $NGROK_DOMAIN" "$plist"
fi

echo "▶ 最新の run_line.sh を取得…"
curl -fsSL "$RUN_URL" -o run_line.sh && chmod +x run_line.sh

echo "▶ 旧プロセスを停止して再起動…"
launchctl unload "$plist" 2>/dev/null
pkill -f cloudflared 2>/dev/null
pkill -f "uvicorn line_agent" 2>/dev/null
pkill -f "ngrok http" 2>/dev/null
sleep 3
launchctl load "$plist"

echo "▶ 起動待ち(25秒)…"
sleep 25
echo "=== webhook ==="; tail -5 webhook_update.log 2>/dev/null
echo "=== server ==="; curl -s http://localhost:8200/ ; echo
echo "✅ 完了。'set webhook -> https://xxxx.ngrok-free.app/callback' と {} が出ていれば成功です。"
