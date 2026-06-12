#!/bin/bash
# LINE 受付AI 常時起動ラッパー（macOS launchd から起動する）。
#
# やること:
#   1) line_agent.py を最新化（任意）
#   2) uvicorn で line_agent を起動
#   3) cloudflared でトンネルを張り、公開URLを取得
#   4) その URL を LINE の Webhook エンドポイントへ自動登録
#      （無料トンネルは再起動でURLが変わるため、毎回自動で追従させる）
#   5) どちらかのプロセスが落ちたら終了 → launchd(KeepAlive) が再起動
#
# 必要な環境変数（launchd plist の EnvironmentVariables で渡す）:
#   LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN / ANTHROPIC_API_KEY
# 任意:
#   LINE_PORT(既定 8200) / BOT_DIR(既定 ~/telegram-ai-bot)
#   BOT_UPDATE_LINE_URL(line_agent.py の取得元・既定はリポジトリ raw)
set -u

BOT_DIR="${BOT_DIR:-$HOME/telegram-ai-bot}"
PORT="${LINE_PORT:-8200}"
cd "$BOT_DIR" || exit 1

PY="$BOT_DIR/venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
CF="$(command -v cloudflared)"
if [ -z "$CF" ]; then
    echo "$(date) cloudflared が見つかりません。'brew install cloudflared' を実行してください。" >&2
    sleep 30
    exit 1
fi

UPDATE_URL="${BOT_UPDATE_LINE_URL:-https://raw.githubusercontent.com/hasegawa212/-6780/refs/heads/claude/loving-pasteur-KqQsk/telegram-ai-bot/line_agent.py}"
curl -fsSL "$UPDATE_URL" -o line_agent.py 2>/dev/null || true

# 1) サーバー
"$PY" -m uvicorn line_agent:app --host 0.0.0.0 --port "$PORT" > line.log 2>&1 &
SERVER_PID=$!

# 2) トンネル
: > tunnel.log
"$CF" tunnel --url "http://localhost:$PORT" > tunnel.log 2>&1 &
TUNNEL_PID=$!

# 3) 公開URLを取得（最大 ~30 秒待つ）
URL=""
for _ in $(seq 1 30); do
    URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' tunnel.log | head -1)
    [ -n "$URL" ] && break
    sleep 1
done

# 4) LINE の Webhook エンドポイントを自動登録
if [ -n "$URL" ] && [ -n "${LINE_CHANNEL_ACCESS_TOKEN:-}" ]; then
    curl -s -X PUT https://api.line.me/v2/bot/channel/webhook/endpoint \
        -H "Authorization: Bearer $LINE_CHANNEL_ACCESS_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"endpoint\":\"$URL/callback\"}" > webhook_update.log 2>&1
    echo "" >> webhook_update.log
    echo "$(date) set webhook -> $URL/callback" >> webhook_update.log
else
    echo "$(date) 公開URLの取得に失敗（tunnel.log を確認）" >> webhook_update.log
fi

# 5) どちらかが落ちるまで常駐
while kill -0 "$SERVER_PID" 2>/dev/null && kill -0 "$TUNNEL_PID" 2>/dev/null; do
    sleep 10
done
kill "$SERVER_PID" "$TUNNEL_PID" 2>/dev/null
exit 1
