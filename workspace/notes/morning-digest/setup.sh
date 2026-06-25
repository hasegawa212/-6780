#!/bin/bash
# 朝のダイジェスト daemon セットアップ (Mac mini で実行)
#
# 既存の Supabase / Slack webhook / Anthropic key を再利用する。
# slack-sync の plist から環境変数を自動コピーする。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
LOG_DIR="$HOME/.openclaw/morning-digest"
PLIST_LABEL="com.openclaw.morning-digest"
PLIST_DEST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
SLACK_SYNC_PLIST="$HOME/Library/LaunchAgents/com.openclaw.slack-sync.plist"
MEGA_BOT_PLIST="$HOME/Library/LaunchAgents/com.martialarts.telegram-bot.plist"
MEGA_BOT_PLIST_DISABLED="${MEGA_BOT_PLIST}.disabled"

echo "==> python3 検出"
PYTHON_BIN="$(command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: python3 が見つかりません。"
    exit 1
fi
echo "    $PYTHON_BIN"

echo "==> anthropic SDK 確認"
"$PYTHON_BIN" -c "import anthropic" 2>/dev/null || {
    echo "    anthropic SDK 未インストール、入れます"
    "$PYTHON_BIN" -m pip install --user anthropic
}

echo "==> 既存 plist から secrets を回収"
SUPABASE_URL=""
SUPABASE_KEY=""
SLACK_WEBHOOK=""
ANTHROPIC_KEY=""

if [ -f "$SLACK_SYNC_PLIST" ]; then
    SUPABASE_URL=$(plutil -extract EnvironmentVariables.SUPABASE_URL raw "$SLACK_SYNC_PLIST" 2>/dev/null || true)
    SUPABASE_KEY=$(plutil -extract EnvironmentVariables.SUPABASE_SERVICE_ROLE_KEY raw "$SLACK_SYNC_PLIST" 2>/dev/null || true)
fi
for SRC in "$MEGA_BOT_PLIST" "$MEGA_BOT_PLIST_DISABLED"; do
    if [ -f "$SRC" ] && [ -z "$ANTHROPIC_KEY" ]; then
        ANTHROPIC_KEY=$(plutil -extract EnvironmentVariables.ANTHROPIC_API_KEY raw "$SRC" 2>/dev/null || true)
    fi
done

if [ -z "$SUPABASE_URL" ]; then read -rp "    SUPABASE_URL: " SUPABASE_URL; fi
if [ -z "$SUPABASE_KEY" ]; then read -rsp "    SUPABASE_SERVICE_ROLE_KEY: " SUPABASE_KEY; echo; fi
if [ -z "$ANTHROPIC_KEY" ]; then read -rsp "    ANTHROPIC_API_KEY: " ANTHROPIC_KEY; echo; fi
read -rp "    SLACK_WEBHOOK (ダイジェスト投稿先): " SLACK_WEBHOOK

mkdir -p "$LOG_DIR"

echo "==> plist 生成"
sed \
    -e "s|__PYTHON_BIN__|${PYTHON_BIN}|g" \
    -e "s|__REPO_ROOT__|${REPO_ROOT}|g" \
    -e "s|__SUPABASE_URL__|${SUPABASE_URL}|g" \
    -e "s|__SUPABASE_KEY__|${SUPABASE_KEY}|g" \
    -e "s|__ANTHROPIC_KEY__|${ANTHROPIC_KEY}|g" \
    -e "s|__SLACK_WEBHOOK__|${SLACK_WEBHOOK}|g" \
    -e "s|__LOG_DIR__|${LOG_DIR}|g" \
    "$(dirname "$0")/${PLIST_LABEL}.plist.template" > "$PLIST_DEST"
chmod 600 "$PLIST_DEST"

echo "==> launchd ジョブ登録 (毎朝 09:00)"
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load -w "$PLIST_DEST"

echo ""
echo "セットアップ完了"
echo ""
echo "確認コマンド:"
echo "  ジョブ確認:   launchctl list | grep morning-digest"
echo "  ログ:        tail -f $LOG_DIR/morning-digest.stdout.log"
echo "  エラー:       tail -f $LOG_DIR/morning-digest.stderr.log"
echo "  手動テスト:   launchctl start ${PLIST_LABEL}"
echo "  停止:        launchctl unload $PLIST_DEST"
echo ""
echo "次の起動: 毎朝 09:00。今すぐテスト走らせるには:"
echo "  launchctl start ${PLIST_LABEL}"
