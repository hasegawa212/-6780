#!/bin/bash
# LINE 受付AI 常時起動ラッパー（macOS launchd から起動する・自己回復型）。
#
# 設計:
#   - 自分自身は終了しない常駐ループ（launchd の再起動ストームを防ぐ）。
#   - uvicorn(line_agent) とトンネル(cloudflared)を監視し、落ちたら自動で再起動。
#   - Cloudflare 無料トンネルは URL が毎回変わり 429 で弾かれることがあるため、
#     失敗時は待って再試行し、URL を取得できたら LINE の Webhook へ自動登録。
#
# 必要な環境変数（launchd plist の EnvironmentVariables で渡す）:
#   LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN / ANTHROPIC_API_KEY
# 任意:
#   LINE_PORT(既定 8200) / BOT_DIR(既定 ~/telegram-ai-bot)
#   NGROK_DOMAIN(指定すると cloudflared でなく ngrok の固定ドメインを使う＝URL不変)
set -u

BOT_DIR="${BOT_DIR:-$HOME/telegram-ai-bot}"
PORT="${LINE_PORT:-8200}"
cd "$BOT_DIR" || exit 1

PY="$BOT_DIR/venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
CF="$(command -v cloudflared)"
NG="$(command -v ngrok)"

# ngrok を使うか判定（authtoken か固定ドメインが指定されていれば ngrok 優先）。
# ngrok は 429 制限が無く安定。固定ドメイン指定時は URL 不変。
USE_NGROK=0
if [ -n "$NG" ] && { [ -n "${NGROK_AUTHTOKEN:-}" ] || [ -n "${NGROK_DOMAIN:-}" ]; }; then
    USE_NGROK=1
    if [ -n "${NGROK_AUTHTOKEN:-}" ]; then
        "$NG" config add-authtoken "$NGROK_AUTHTOKEN" >/dev/null 2>&1 || true
    fi
fi

UPDATE_URL="${BOT_UPDATE_LINE_URL:-https://raw.githubusercontent.com/hasegawa212/-6780/refs/heads/claude/loving-pasteur-KqQsk/telegram-ai-bot/line_agent.py}"
curl -fsSL "$UPDATE_URL" -o line_agent.py 2>/dev/null || true

# 起動時に残骸を掃除（多重起動・ポート競合を防ぐ）
pkill -f "uvicorn line_agent" 2>/dev/null
pkill -f "cloudflared tunnel --url http://localhost:$PORT" 2>/dev/null
pkill -f "ngrok http" 2>/dev/null
sleep 2

SERVER_PID=0
TUNNEL_PID=0
CURURL=""

start_server() {
    "$PY" -m uvicorn line_agent:app --host 0.0.0.0 --port "$PORT" > line.log 2>&1 &
    SERVER_PID=$!
}

start_tunnel() {
    : > tunnel.log
    if [ "$USE_NGROK" = "1" ]; then
        if [ -n "${NGROK_DOMAIN:-}" ]; then
            # 固定ドメイン（URL が変わらない＝LINE 設定は一度きりで済む）
            "$NG" http "--domain=${NGROK_DOMAIN}" "$PORT" --log=stdout > tunnel.log 2>&1 &
        else
            # authtoken のみ（毎回ランダムURLだが 429 制限が無く安定。LINEへ自動再登録）
            "$NG" http "$PORT" --log=stdout > tunnel.log 2>&1 &
        fi
    else
        "$CF" tunnel --url "http://localhost:$PORT" > tunnel.log 2>&1 &
    fi
    TUNNEL_PID=$!
}

get_url() {
    local u=""
    for _ in $(seq 1 30); do
        u=$(grep -oE 'https://[a-z0-9.-]+\.(trycloudflare\.com|ngrok-free\.app|ngrok\.app|ngrok\.io)' tunnel.log | head -1)
        [ -n "$u" ] && { echo "$u"; return 0; }
        sleep 2
    done
    return 1
}

register_webhook() {
    local url="$1"
    [ -n "${LINE_CHANNEL_ACCESS_TOKEN:-}" ] || return
    curl -s -X PUT https://api.line.me/v2/bot/channel/webhook/endpoint \
        -H "Authorization: Bearer $LINE_CHANNEL_ACCESS_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"endpoint\":\"$url/callback\"}" >> webhook_update.log 2>&1
    echo "  $(date) set webhook -> $url/callback" >> webhook_update.log
}

start_server

# 常駐監視ループ（終了しない＝launchd の再起動ストームを起こさない）
while true; do
    # サーバー死活監視
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        start_server
        sleep 3
    fi
    # トンネル死活監視
    if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
        start_tunnel
        if URL="$(get_url)"; then
            if [ "$URL" != "$CURURL" ]; then
                CURURL="$URL"
                register_webhook "$URL"
            fi
        else
            # 429 等で URL を取得できず。トンネルを止め、待ってから再試行
            echo "$(date) 公開URL取得に失敗（429制限の可能性）。60秒後に再試行" >> webhook_update.log
            kill "$TUNNEL_PID" 2>/dev/null
            TUNNEL_PID=0
            sleep 60
            continue
        fi
    fi
    sleep 15
done
