#!/usr/bin/env bash
# BC自動生成サービス ワンショット セットアップ＆プリフライト。
# 使い方: bash deploy/setup.sh   （bc-pipeline 直下で実行）
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 1) Python venv 作成 =="
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
echo "  OK: $(python --version)"

echo "== 2) テンプレWB配置の確認 =="
TDIR="${BC_TEMPLATE_DIR:-templates}"
mkdir -p "$TDIR"
found=0
for v in 36-1 37-1 38-1; do
  if [ -f "$TDIR/$v.xlsx" ]; then echo "  OK  $TDIR/$v.xlsx"; found=$((found+1));
  else echo "  --  $TDIR/$v.xlsx が未配置（御社のブランク様式WBを置いてください）"; fi
done
[ "$found" -gt 0 ] || echo "  ※ 1つも無いと /generate は自作Excelにフォールバックします。"

echo "== 3) .env の確認 =="
[ -f .env ] || { cp .env.example .env; echo "  .env を作成しました。ANTHROPIC_API_KEY 等を編集してください。"; }

echo "== 4) テスト（環境起因の bundle テストは除外）=="
python -m pytest tests/test_pipeline.py -q -k "not bundle" || true

echo "== 5) 起動方法 =="
cat <<'EOF'
  フォアグラウンド: set -a; . ./.env; set +a; .venv/bin/uvicorn bc_service:app --host 0.0.0.0 --port "${BC_PORT:-8800}"
  常駐(Mac/launchd): deploy/com.martialarts.bcservice.plist を編集→~/Library/LaunchAgents/ に配置→launchctl load
  Docker:           docker compose -f deploy/docker-compose.yml up -d
  プリフライト:      curl -s localhost:${BC_PORT:-8800}/health | python -m json.tool
EOF
echo "完了。"
