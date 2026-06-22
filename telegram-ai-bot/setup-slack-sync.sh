#!/bin/bash
# Slack -> Supabase sync worker installer (Mac mini).
#
# Usage:
#   cd telegram-ai-bot
#   bash setup-slack-sync.sh
#
# Reads secrets interactively, generates the launchd plist from the
# template in deploy/, drops it into ~/Library/LaunchAgents/, and
# loads the job (runs every 10 min).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$HOME/.openclaw/slack-sync"
PLIST_LABEL="com.openclaw.slack-sync"
PLIST_DEST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
TEMPLATE="$REPO_ROOT/telegram-ai-bot/deploy/${PLIST_LABEL}.plist"

echo "==> python3 検出"
PYTHON_BIN="$(command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: python3 が見つかりません。Xcode CLT か Homebrew Python を入れてください。"
    exit 1
fi
echo "    $PYTHON_BIN"

echo "==> 設定値を入力"
read -rp "    SUPABASE_URL [https://xpzrsljqjhqewquaziul.supabase.co]: " SUPABASE_URL
SUPABASE_URL="${SUPABASE_URL:-https://xpzrsljqjhqewquaziul.supabase.co}"
read -rsp "    SUPABASE_SERVICE_ROLE_KEY: " SUPABASE_SERVICE_ROLE_KEY; echo
read -rsp "    SLACK_BOT_TOKEN (xoxb-): " SLACK_BOT_TOKEN; echo
read -rsp "    OPENAI_API_KEY: " OPENAI_API_KEY; echo

for V in SUPABASE_SERVICE_ROLE_KEY SLACK_BOT_TOKEN OPENAI_API_KEY; do
    if [ -z "${!V}" ]; then
        echo "ERROR: $V が空です。"
        exit 1
    fi
done

echo "==> ログディレクトリ作成 ($LOG_DIR)"
mkdir -p "$LOG_DIR"

echo "==> plist 生成 ($PLIST_DEST)"
sed \
    -e "s|__PYTHON_BIN__|${PYTHON_BIN}|g" \
    -e "s|__REPO_ROOT__|${REPO_ROOT}|g" \
    -e "s|__LOG_DIR__|${LOG_DIR}|g" \
    -e "s|__SUPABASE_URL__|${SUPABASE_URL}|g" \
    -e "s|__SUPABASE_SERVICE_ROLE_KEY__|${SUPABASE_SERVICE_ROLE_KEY}|g" \
    -e "s|__SLACK_BOT_TOKEN__|${SLACK_BOT_TOKEN}|g" \
    -e "s|__OPENAI_API_KEY__|${OPENAI_API_KEY}|g" \
    "$TEMPLATE" > "$PLIST_DEST"
chmod 600 "$PLIST_DEST"

echo "==> 既存ジョブを停止 (もしあれば)"
launchctl unload "$PLIST_DEST" 2>/dev/null || true

echo "==> launchd ジョブを起動 (10 分間隔)"
launchctl load -w "$PLIST_DEST"

echo ""
echo "セットアップ完了"
echo ""
echo "確認コマンド:"
echo "  ジョブ状態: launchctl list | grep slack-sync"
echo "  ログ:      tail -f $LOG_DIR/slack-sync.stdout.log"
echo "  エラー:    tail -f $LOG_DIR/slack-sync.stderr.log"
echo "  停止:      launchctl unload $PLIST_DEST"
echo ""
echo "Telegram から: /semsearch <キーワード>"
