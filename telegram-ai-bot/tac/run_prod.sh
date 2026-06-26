#!/usr/bin/env bash
# TAC Webhook サーバーを本番想定で常駐起動する（gunicorn）。
#
# 重要: TACConnector は会話状態をプロセス内メモリに保持するため、
# ワーカーは 1 プロセスに固定する（-w 1）。同時通話はスレッドで捌く（--threads）。
# 真のマルチワーカー/水平スケールには共有ストア（Redis 等）への状態外出しが必要。
#
# 使い方:
#   set -a; source tac/.env; set +a   # 認証情報を環境に読み込み
#   ./tac/run_prod.sh                 # フォアグラウンド起動
# もしくは常駐（ログをファイルへ）:
#   nohup ./tac/run_prod.sh > /tmp/tac-prod.log 2>&1 &
set -euo pipefail

PORT="${PORT:-8090}"
THREADS="${TAC_GUNICORN_THREADS:-8}"
TIMEOUT="${TAC_GUNICORN_TIMEOUT:-60}"

exec gunicorn \
  --workers 1 \
  --threads "${THREADS}" \
  --timeout "${TIMEOUT}" \
  --bind "0.0.0.0:${PORT}" \
  --access-logfile - \
  tac.server:app
