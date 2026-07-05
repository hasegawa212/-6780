#!/bin/bash
# Lolipop IMAP watcher セットアップスクリプト (Mac mini で実行)
#
# 使い方:
#   cd workspace/notes/imap-watcher
#   bash setup.sh
#
# やること:
#   - スクリプトを ~/.openclaw/imap-watcher/ にコピー
#   - 4 アドレスの IMAP パスワードを macOS Keychain に登録
#   - launchd plist を ~/Library/LaunchAgents/ に配置
#   - launchctl で 5 分間隔のジョブを起動

set -euo pipefail

INSTALL_ROOT="$HOME/.openclaw/imap-watcher"
PLIST_LABEL="com.openclaw.imap-watcher"
PLIST_DEST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

ACCOUNTS=(
    "info@martialarts.co.jp"
    "sales@martialarts.co.jp"
    "h.hasegawa@martialarts.co.jp"
    "wordpress@martialarts.co.jp"
)

echo "==> python3 を検出"
PYTHON_BIN="$(command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: python3 が見つかりません。Xcode Command Line Tools か Homebrew Python を入れてください。"
    exit 1
fi
echo "    $PYTHON_BIN"

echo "==> Slack Webhook URL を入力 (#30 用)"
read -rp "    SLACK_WEBHOOK: " SLACK_WEBHOOK
if [[ ! "$SLACK_WEBHOOK" =~ ^https://hooks\.slack\.com/services/ ]]; then
    echo "ERROR: URL の形式が想定外です。https://hooks.slack.com/services/ で始まる必要があります。"
    exit 1
fi

echo "==> ディレクトリ作成 ($INSTALL_ROOT)"
mkdir -p "$INSTALL_ROOT/log" "$INSTALL_ROOT/state"

echo "==> スクリプトを配置"
cp "$REPO_DIR/imap-watcher.py" "$INSTALL_ROOT/imap-watcher.py"
chmod 700 "$INSTALL_ROOT/imap-watcher.py"

echo "==> plist を生成して配置"
sed \
    -e "s|__PYTHON_BIN__|${PYTHON_BIN}|g" \
    -e "s|__INSTALL_ROOT__|${INSTALL_ROOT}|g" \
    -e "s|__SLACK_WEBHOOK__|${SLACK_WEBHOOK}|g" \
    "$REPO_DIR/${PLIST_LABEL}.plist.template" > "$PLIST_DEST"
chmod 600 "$PLIST_DEST"

echo "==> Lolipop IMAP パスワードを Keychain に登録"
echo "    (各アドレスの IMAP/メール用パスワードを順番に入力)"
for ACCOUNT in "${ACCOUNTS[@]}"; do
    read -rsp "    $ACCOUNT のパスワード: " PW
    echo
    if [ -z "$PW" ]; then
        echo "    -> 空のためスキップ ($ACCOUNT)"
        continue
    fi
    security delete-generic-password -s lolipop-imap -a "$ACCOUNT" >/dev/null 2>&1 || true
    security add-generic-password -s lolipop-imap -a "$ACCOUNT" -w "$PW"
    echo "    -> Keychain 登録完了"
    unset PW
done

echo "==> 既存 launchd ジョブを停止 (もしあれば)"
launchctl unload "$PLIST_DEST" 2>/dev/null || true

echo "==> launchd ジョブを起動"
launchctl load -w "$PLIST_DEST"

echo ""
echo "セットアップ完了"
echo ""
echo "確認コマンド:"
echo "  ジョブ確認:   launchctl list | grep imap-watcher"
echo "  ログ:        tail -f $INSTALL_ROOT/log/stdout.log"
echo "  エラーログ:   tail -f $INSTALL_ROOT/log/stderr.log"
echo "  状態:        ls -la $INSTALL_ROOT/state/"
echo "  手動実行:    SLACK_WEBHOOK='$SLACK_WEBHOOK' $PYTHON_BIN $INSTALL_ROOT/imap-watcher.py"
echo "  停止:        launchctl unload $PLIST_DEST"
echo ""
echo "動作テスト: 4 アドレスのどれかに外部からメール送信 → 最大 5 分で Slack #30 に届くはず"
echo "(初回起動時は既存メールを「読了」扱いにブックマークするだけで Slack には流しません)"
