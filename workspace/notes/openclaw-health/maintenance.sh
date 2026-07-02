#!/bin/bash
# OpenClaw / mail 系 日次メンテナンス
#
# 毎日 03:00 launchd で起動 (com.openclaw.maintenance.plist)
#   - openclaw.json を日次バックアップ (30 日 rotate)
#   - 古い session file を削除 (30 日以上前 / .deleted / .reset / .checkpoint 拡張子)
#   - gateway.log / gateway.err.log を rotate (100MB 超で gzip)
#   - slack-sync のログも同様
#
# stdout / stderr は launchd の指定パスへ、Slack への通知は health.py に任せる。

set -uo pipefail

OPENCLAW_HOME="$HOME/.openclaw"
BACKUP_DIR="$OPENCLAW_HOME/backups/openclaw-json"
SESSIONS_DIR="$OPENCLAW_HOME/agents/main/sessions"
LOG_DIRS=(
    "$OPENCLAW_HOME/logs"
    "$OPENCLAW_HOME/slack-sync"
    "$OPENCLAW_HOME/morning-digest"
    "$OPENCLAW_HOME/health"
    "$OPENCLAW_HOME/imap-watcher/log"
)
BACKUP_KEEP_DAYS=30
SESSION_KEEP_DAYS=30
LOG_ROTATE_BYTES=$((100 * 1024 * 1024))
LOG_KEEP_DAYS=90

TS="$(date +%Y-%m-%d)"

log() { printf '[maintenance %s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

log "start"

# ------------------------------------------------------------
# 1. openclaw.json バックアップ
# ------------------------------------------------------------
if [ -f "$OPENCLAW_HOME/openclaw.json" ]; then
    mkdir -p "$BACKUP_DIR"
    cp "$OPENCLAW_HOME/openclaw.json" "$BACKUP_DIR/openclaw-$TS.json"
    log "backup created: openclaw-$TS.json ($(wc -c < "$BACKUP_DIR/openclaw-$TS.json") bytes)"
    # 30 日超のバックアップ削除
    find "$BACKUP_DIR" -name 'openclaw-*.json' -mtime "+$BACKUP_KEEP_DAYS" -delete 2>/dev/null || true
    remaining=$(find "$BACKUP_DIR" -name 'openclaw-*.json' | wc -l | tr -d ' ')
    log "backups after rotation: $remaining"
else
    log "warn: openclaw.json not found, skipping backup"
fi

# ------------------------------------------------------------
# 2. session file cleanup
# ------------------------------------------------------------
if [ -d "$SESSIONS_DIR" ]; then
    old_total=$(find "$SESSIONS_DIR" -type f | wc -l | tr -d ' ')
    # まず tombstone (.deleted / .reset / .checkpoint) を先に消す
    find "$SESSIONS_DIR" -type f \( -name '*.deleted.*' -o -name '*.reset.*' -o -name '*.checkpoint.*.jsonl' \) -delete 2>/dev/null || true
    # 30 日以上前の .jsonl を削除
    find "$SESSIONS_DIR" -type f -name '*.jsonl' -mtime "+$SESSION_KEEP_DAYS" -delete 2>/dev/null || true
    new_total=$(find "$SESSIONS_DIR" -type f | wc -l | tr -d ' ')
    log "sessions cleaned: $old_total -> $new_total"
else
    log "sessions dir not found: $SESSIONS_DIR"
fi

# ------------------------------------------------------------
# 3. log rotation
# ------------------------------------------------------------
for dir in "${LOG_DIRS[@]}"; do
    [ -d "$dir" ] || continue
    find "$dir" -maxdepth 1 -type f \( -name '*.log' -o -name '*.stdout.log' -o -name '*.stderr.log' -o -name '*.err.log' \) 2>/dev/null | while read -r logfile; do
        size=$(stat -f %z "$logfile" 2>/dev/null || echo 0)
        if [ "$size" -gt "$LOG_ROTATE_BYTES" ]; then
            gzip -c "$logfile" > "${logfile}.${TS}.gz" && : > "$logfile"
            log "rotated: $logfile ($size bytes) -> ${logfile}.${TS}.gz"
        fi
    done
    # 古い gzip を削除
    find "$dir" -maxdepth 1 -type f -name '*.gz' -mtime "+$LOG_KEEP_DAYS" -delete 2>/dev/null || true
done

log "done"
