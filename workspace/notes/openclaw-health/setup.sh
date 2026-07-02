#!/bin/bash
# OpenClaw ヘルスチェック + 日次メンテナンス のセットアップ
#
# 使い方 (Mac mini で):
#   bash workspace/notes/openclaw-health/setup.sh
#
# 既存 plist から secrets を再利用する。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
LOG_DIR="$HOME/.openclaw/health"
HEALTH_LABEL="com.openclaw.health"
MAINT_LABEL="com.openclaw.maintenance"
HEALTH_DEST="$HOME/Library/LaunchAgents/${HEALTH_LABEL}.plist"
MAINT_DEST="$HOME/Library/LaunchAgents/${MAINT_LABEL}.plist"

GATEWAY_PLIST="$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist"
SLACK_SYNC_PLIST="$HOME/Library/LaunchAgents/com.openclaw.slack-sync.plist"
MORNING_PLIST="$HOME/Library/LaunchAgents/com.openclaw.morning-digest.plist"

echo "==> python3 検出"
PYTHON_BIN="$(command -v python3 || true)"
[ -n "$PYTHON_BIN" ] || { echo "ERROR: python3 not found"; exit 1; }
echo "    $PYTHON_BIN"

extract() {
    local plist="$1"; local key="$2"
    [ -f "$plist" ] || return 1
    plutil -extract "EnvironmentVariables.$key" raw "$plist" 2>/dev/null || return 1
}

echo "==> 既存 plist から secrets を回収"
SLACK_WEBHOOK="$(extract "$MORNING_PLIST" SLACK_WEBHOOK || extract "$SLACK_SYNC_PLIST" SLACK_WEBHOOK || true)"
TELEGRAM_BOT_TOKEN="$(extract "$GATEWAY_PLIST" TELEGRAM_BOT_TOKEN || true)"
ANTHROPIC_API_KEY="$(extract "$GATEWAY_PLIST" ANTHROPIC_API_KEY || extract "$MORNING_PLIST" ANTHROPIC_API_KEY || true)"

[ -n "$SLACK_WEBHOOK" ] || { read -rp "    SLACK_WEBHOOK: " SLACK_WEBHOOK; }
[ -n "$TELEGRAM_BOT_TOKEN" ] || { read -rp "    TELEGRAM_BOT_TOKEN: " TELEGRAM_BOT_TOKEN; }
[ -n "$ANTHROPIC_API_KEY" ] || { read -rsp "    ANTHROPIC_API_KEY: " ANTHROPIC_API_KEY; echo; }

mkdir -p "$LOG_DIR"

install_plist() {
    local label="$1"; local dest="$2"; local template="$3"
    sed \
        -e "s|__PYTHON_BIN__|${PYTHON_BIN}|g" \
        -e "s|__REPO_ROOT__|${REPO_ROOT}|g" \
        -e "s|__SLACK_WEBHOOK__|${SLACK_WEBHOOK}|g" \
        -e "s|__TELEGRAM_BOT_TOKEN__|${TELEGRAM_BOT_TOKEN}|g" \
        -e "s|__ANTHROPIC_API_KEY__|${ANTHROPIC_API_KEY}|g" \
        -e "s|__LOG_DIR__|${LOG_DIR}|g" \
        "$template" > "$dest"
    chmod 600 "$dest"
    launchctl unload "$dest" 2>/dev/null || true
    launchctl load -w "$dest"
    echo "    installed: $label"
}

echo "==> health check plist 配置 (5 分間隔)"
install_plist "$HEALTH_LABEL" "$HEALTH_DEST" "$(dirname "$0")/${HEALTH_LABEL}.plist.template"

echo "==> maintenance plist 配置 (毎日 03:00)"
install_plist "$MAINT_LABEL" "$MAINT_DEST" "$(dirname "$0")/${MAINT_LABEL}.plist.template"

echo ""
echo "セットアップ完了"
echo ""
echo "手動テスト (health check 即実行):"
echo "  SLACK_WEBHOOK='$SLACK_WEBHOOK' TELEGRAM_BOT_TOKEN='***' ANTHROPIC_API_KEY='***' \\"
echo "    $PYTHON_BIN $REPO_ROOT/workspace/notes/openclaw-health/health.py"
echo ""
echo "手動テスト (maintenance 即実行):"
echo "  bash $REPO_ROOT/workspace/notes/openclaw-health/maintenance.sh"
echo ""
echo "ログ:"
echo "  tail -f $LOG_DIR/health.stdout.log"
echo "  tail -f $LOG_DIR/maintenance.stdout.log"
