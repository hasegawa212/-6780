#!/bin/bash
# ============================================================================
#  create_public_repo.command  —  MacBook 用ワンクリック公開リポジトリ作成
# ----------------------------------------------------------------------------
#  telegram-ai-bot/ だけを取り出して、私的データ・過去履歴・秘密情報を含まない
#  「クリーンな公開用リポジトリ」を Mac 上に作成します（OSS 公開の準備）。
#
#  使い方:
#    1) Finder でこのファイルをダブルクリック（または Terminal で実行）
#       Terminal:  bash create_public_repo.command  [出力先]
#    2) 既定の出力先は ~/ai-secretary-bot
#    3) 完成後、表示される手順に従って GitHub の空 Public リポジトリへ push
#
#  このスクリプトは push しません（公開は最後にあなたが手動で行います）。
# ============================================================================
set -euo pipefail

# このスクリプトが置かれている場所（= リポジトリのルート）を基準にする
SRC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SRC_ROOT/telegram-ai-bot"
DEST="${1:-$HOME/ai-secretary-bot}"

echo "================================================================"
echo " 公開リポジトリを作成します"
echo "   元:   $SRC"
echo "   先:   $DEST"
echo "================================================================"

# --- 事前チェック -----------------------------------------------------------
if [ ! -d "$SRC" ]; then
  echo "❌ $SRC が見つかりません。このスクリプトはリポジトリのルートに置いて実行してください。"
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo "❌ git が見つかりません。Xcode Command Line Tools を入れてください: xcode-select --install"
  exit 1
fi
if [ -e "$DEST" ] && [ -n "$(ls -A "$DEST" 2>/dev/null || true)" ]; then
  echo "❌ 出力先 $DEST が既に存在し、空ではありません。別の場所を指定してください:"
  echo "   bash create_public_repo.command ~/別の場所"
  exit 1
fi

mkdir -p "$DEST"

# --- ボット本体をコピー（秘密情報・キャッシュ・私的データは除外） -----------
echo "▶ ファイルをコピー中…"
if command -v rsync >/dev/null 2>&1; then
  rsync -a \
    --exclude '.env' \
    --exclude 'venv/' --exclude '.venv/' \
    --exclude '__pycache__/' --exclude '*.pyc' \
    --exclude '*.log' --exclude '*.lock' \
    --exclude '.pytest_cache/' --exclude '.ruff_cache/' \
    "$SRC"/ "$DEST"/
else
  # rsync が無い環境向けフォールバック: 全コピーしてから不要物を削除
  cp -R "$SRC"/. "$DEST"/
  find "$DEST" \( -name '.env' -o -name '*.pyc' -o -name '*.log' -o -name '*.lock' \) -type f -delete 2>/dev/null || true
  find "$DEST" \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.ruff_cache' \
                  -o -name 'venv' -o -name '.venv' \) -type d -prune -exec rm -rf {} + 2>/dev/null || true
fi

# ライセンスを同梱
[ -f "$SRC_ROOT/LICENSE" ] && cp "$SRC_ROOT/LICENSE" "$DEST/LICENSE"

# 公開リポではボット同梱の README をトップに使う（既に DEST/README.md として存在）

# --- ルート用の CI を生成（公開リポはボットがトップ階層になるため） ---------
mkdir -p "$DEST/.github/workflows"
cat > "$DEST/.github/workflows/ci.yml" <<'YAML'
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  lint:
    name: Lint & compile (ruff + py_compile)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install ruff
      - run: ruff check .
      - run: python -m py_compile *.py

  test:
    name: Unit tests (pytest)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install anthropic "python-telegram-bot[job-queue]" httpx pytest
      - run: pytest
YAML

# --- 秘密情報の最終スキャン（見つかったら中止） -----------------------------
echo "▶ 秘密情報をスキャン中…"
PATTERN='sk-ant-[A-Za-z0-9]{20,}|xoxb-[0-9]|xapp-[0-9]|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|[0-9]{8,10}:[A-Za-z0-9_-]{35}'
if grep -rIEn --exclude-dir='.git' "$PATTERN" "$DEST" 2>/dev/null; then
  echo ""
  echo "❌ 実在しそうな秘密情報が見つかりました（上記）。公開を中止しました。"
  echo "   該当箇所を環境変数に置き換えてから、$DEST を削除して再実行してください。"
  exit 1
fi
echo "  ✅ 秘密情報は検出されませんでした。"

# --- git 初期化（push はしない） --------------------------------------------
cd "$DEST"
git init -q
git add -A
git commit -q -m "Initial public release" || true

echo ""
echo "================================================================"
echo " ✅ 完成: $DEST"
echo "================================================================"
echo ""
echo " 次の手順で GitHub に公開してください:"
echo ""
if command -v gh >/dev/null 2>&1; then
  echo "  # GitHub CLI があるので一発で作成＆push できます:"
  echo "  cd \"$DEST\""
  echo "  gh repo create ai-secretary-bot --public --source=. --remote=origin --push"
else
  echo "  1) GitHub で空の Public リポジトリ（例: ai-secretary-bot）を作る"
  echo "     ※ README/.gitignore/LICENSE は付けない（空で作る）"
  echo "  2) ターミナルで:"
  echo "     cd \"$DEST\""
  echo "     git branch -M main"
  echo "     git remote add origin https://github.com/<あなた>/ai-secretary-bot.git"
  echo "     git push -u origin main"
fi
echo ""
echo " ※ この -6780 リポジトリ（財務データ等を含む）は Private のままにしてください。"
echo ""
