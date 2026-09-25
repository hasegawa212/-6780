#!/bin/bash
# ============================================================================
#  create_public_repo.command  —  MacBook 用ワンクリック公開リポジトリ作成
# ----------------------------------------------------------------------------
#  telegram-ai-bot/ だけを取り出して、私的データ・過去履歴・秘密情報を含まない
#  「クリーンな公開用リポジトリ」を Mac 上に作成します（OSS 公開の準備）。
#
#  使い方:
#    1) Finder でこのファイルをダブルクリック（または Terminal で実行）
#       Terminal:  bash create_public_repo.command [オプション] [出力先]
#    2) 既定の出力先は ~/ai-secretary-bot
#    3) 完成後、表示される手順に従って GitHub の空 Public リポジトリへ push
#
#  オプション:
#    --dry-run     実際には出力先を作らず、検査結果だけを表示する（安全な下見）
#    --strict      PII（電話番号・メール・ローカルパス）の警告も「失敗」扱いにする
#    --no-verify   Python 構文チェックを省略する
#    -h, --help    このヘルプを表示
#
#  このスクリプトは push しません（公開は最後にあなたが手動で行います）。
#  作業は一時ディレクトリで行い、全チェックに合格して初めて出力先へ移動します。
#  途中で失敗した場合は何も残しません（壊れた出力先ができない）。
# ============================================================================
set -euo pipefail

DRY_RUN=0
STRICT=0
VERIFY=1
DEST=""

usage() {
  sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)   DRY_RUN=1 ;;
    --strict)    STRICT=1 ;;
    --no-verify) VERIFY=0 ;;
    -h|--help)   usage; exit 0 ;;
    -*)          echo "❌ 不明なオプション: $1（--help を参照）"; exit 1 ;;
    *)           if [ -n "$DEST" ]; then echo "❌ 出力先は 1 つだけ指定してください。"; exit 1; fi
                 DEST="$1" ;;
  esac
  shift
done

# このスクリプトが置かれている場所を基準にソースを自動判定する。
#  - リポジトリのルートに置いた場合 → telegram-ai-bot/ を使う
#  - telegram-ai-bot の中に置いた場合 → そのフォルダ自身を使う
SRC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$SRC_ROOT/telegram-ai-bot" ]; then
  SRC="$SRC_ROOT/telegram-ai-bot"
elif [ -f "$SRC_ROOT/mega_bot.py" ]; then
  SRC="$SRC_ROOT"
else
  SRC="$SRC_ROOT/telegram-ai-bot"   # 後段のチェックでエラー表示
fi
DEST="${DEST:-$HOME/ai-secretary-bot}"

echo "================================================================"
echo " 公開リポジトリを作成します"
echo "   元:   $SRC"
echo "   先:   $DEST"
[ "$DRY_RUN" = 1 ] && echo "   ※ --dry-run: 検査のみ。出力先は作成しません。"
[ "$STRICT" = 1 ] && echo "   ※ --strict: PII 警告も失敗として扱います。"
echo "================================================================"

# --- 事前チェック -----------------------------------------------------------
if [ ! -f "$SRC/mega_bot.py" ]; then
  echo "❌ ボット本体が見つかりません（$SRC/mega_bot.py なし）。"
  echo "   このスクリプトは『リポジトリのルート』または『telegram-ai-bot フォルダ内』に置いて実行してください。"
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

# --- 作業用の一時ディレクトリ（出力先と同じ場所に作り、失敗時は自動で消す） --
DEST_PARENT="$(dirname "$DEST")"
mkdir -p "$DEST_PARENT"
STAGE="$(mktemp -d "$DEST_PARENT/.public-repo-stage.XXXXXX")"
cleanup() {
  [ -n "${STAGE:-}" ] && [ -d "$STAGE" ] && rm -rf "$STAGE"
}
trap cleanup EXIT

# ============================================================================
#  除外ルール（rsync / フォールバックの双方で同じ結果になるよう、
#  コピー後の掃除 harden_tree() を唯一の正とする）
# ============================================================================
EXCLUDE_DIRS=(
  .git venv .venv env __pycache__ .pytest_cache .ruff_cache .mypy_cache
  node_modules .idea .vscode .claude .DS_Store
  data backups backup tmp
)
EXCLUDE_FILE_GLOBS=(
  '*.pyc' '*.pyo' '*.log' '*.lock' '*.sqlite' '*.sqlite3' '*.db'
  '*.pem' '*.key' '*.p12' '*.pfx' '*.crt' '*.der' '*.keystore'
  'id_rsa*' 'id_ed25519*' '.netrc' '.npmrc' '.pypirc'
  '*credentials*.json' '*service-account*.json' 'client_secret*.json'
  '*.session' '*.bak' '*.orig' '*.swp' '.DS_Store'
  'create_public_repo.command'
  'slack_findings.md' 'location_map.md'
  # 実運用の設定（実在の会社名・電話番号が入る）。*.example.md だけを公開する。
  'business_info.md' 'operators.json' 'customers.json' 'memory.json'
)

# 公開用ツリーから危険物を取り除く（.env.example だけは残す）。
harden_tree() {
  local root="$1" d g
  for d in "${EXCLUDE_DIRS[@]}"; do
    find "$root" -name "$d" -type d -prune -exec rm -rf {} + 2>/dev/null || true
  done
  for g in "${EXCLUDE_FILE_GLOBS[@]}"; do
    find "$root" -name "$g" -type f -delete 2>/dev/null || true
  done
  # .env / .env.local / .env.production … は削除、.env.example は残す
  find "$root" -name '.env*' -type f ! -name '.env.example' -delete 2>/dev/null || true
  # 壊れた／外部を指すシンボリックリンクは公開しない
  find "$root" -type l -delete 2>/dev/null || true
}

# --- ボット本体をコピー（秘密情報・キャッシュ・私的データは除外） -----------
echo "▶ ファイルをコピー中…"
if command -v rsync >/dev/null 2>&1; then
  RSYNC_ARGS=()
  for d in "${EXCLUDE_DIRS[@]}"; do RSYNC_ARGS+=( --exclude "$d/" ); done
  for g in "${EXCLUDE_FILE_GLOBS[@]}"; do RSYNC_ARGS+=( --exclude "$g" ); done
  # .env.example を守った上で、他の .env 系は除外する（include が先に効く）
  rsync -a --include '.env.example' --exclude '.env' --exclude '.env.*' \
    "${RSYNC_ARGS[@]}" "$SRC"/ "$STAGE"/
else
  echo "  ℹ️ rsync が無いため全コピー後に除外します（結果は同じ）。"
  cp -R "$SRC"/. "$STAGE"/
fi
harden_tree "$STAGE"

# ライセンスを同梱（無ければ MIT を生成）
if [ -f "$SRC_ROOT/LICENSE" ]; then
  cp "$SRC_ROOT/LICENSE" "$STAGE/LICENSE"
elif [ ! -f "$STAGE/LICENSE" ]; then
  YEAR="$(date +%Y)"
  cat > "$STAGE/LICENSE" <<LIC
MIT License

Copyright (c) $YEAR Hikaru Hasegawa

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
LIC
fi

# 公開リポではボット同梱の README をトップに使う（既に STAGE/README.md として存在）

# .gitignore が無い公開リポは事故のもとなので必ず用意する
if [ ! -f "$STAGE/.gitignore" ]; then
  cat > "$STAGE/.gitignore" <<'GI'
# secrets / local env
.env
.env.*
!.env.example
*.pem
*.key

# python
__pycache__/
*.pyc
venv/
.venv/
.pytest_cache/
.ruff_cache/
*.log
GI
fi
# .env が無視されていない .gitignore は危険（追記して塞ぐ）
if ! grep -qE '^\.env$' "$STAGE/.gitignore"; then
  printf '\n# secrets\n.env\n.env.*\n!.env.example\n' >> "$STAGE/.gitignore"
fi

# --- ルート用の CI を生成（公開リポはボットがトップ階層になるため） ---------
mkdir -p "$STAGE/.github/workflows"
cat > "$STAGE/.github/workflows/ci.yml" <<'YAML'
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

# --- 構成チェック（公開リポとして最低限そろっているか） ---------------------
echo "▶ 構成を確認中…"
STRUCT_NG=0
for f in mega_bot.py README.md requirements.txt LICENSE .gitignore .env.example; do
  if [ ! -f "$STAGE/$f" ]; then
    echo "  ❌ $f が見つかりません。"
    STRUCT_NG=1
  fi
done
# 私的データ・秘密ファイルが残っていないことを最終確認（掃除漏れの検知）
LEFTOVER="$(find "$STAGE" \( -name '.env' -o -name '*.pem' -o -name '*.key' \
  -o -name 'slack_findings.md' -o -name 'location_map.md' -o -name 'business_info.md' \) \
  -not -path '*/.git/*' 2>/dev/null || true)"
if [ -n "$LEFTOVER" ]; then
  echo "  ❌ 公開してはいけないファイルが残っています:"
  echo "$LEFTOVER" | sed 's/^/     /'
  STRUCT_NG=1
fi
if [ "$STRUCT_NG" = 1 ]; then
  echo ""
  echo "❌ 構成チェックに失敗しました。公開を中止しました（出力先は作成していません）。"
  exit 1
fi
echo "  ✅ 必要なファイルはそろっています。"

# --- 秘密情報の最終スキャン（見つかったら中止） -----------------------------
echo "▶ 秘密情報をスキャン中…"
# テスト用の明らかなダミー値（fake / placeholder / deadbeef 等）は誤検知なので除く。
# 「実在しそうな値」だけを残すためのフィルタ。
FAKE_WORDS='fake|dummy|placeholder|example|sample|deadbeef|redacted|changeme|xxxx|abcdef123456|your[_-]?(key|token|secret|id)|\.\.\.'
drop_fakes() {
  grep -vEi "$FAKE_WORDS" || true
}
FAKE_SKIPPED=0

# 「名前|正規表現」の形式。1 つでも当たれば公開を中止する。
SECRET_PATTERNS=(
  'Anthropic API キー|sk-ant-[A-Za-z0-9_-]{20,}'
  'OpenAI API キー|sk-(proj-)?[A-Za-z0-9]{32,}'
  'Slack トークン|xox[abposr]-[0-9A-Za-z-]{10,}'
  'Slack アプリトークン|xapp-[0-9]-[0-9A-Za-z-]{10,}'
  'Slack Webhook URL|https://hooks\.slack\.com/services/T[0-9A-Za-z]+/B[0-9A-Za-z]+/[0-9A-Za-z]{20,}'
  'GitHub トークン|(gh[pousr]_[0-9A-Za-z]{36,}|github_pat_[0-9A-Za-z_]{22,})'
  'AWS アクセスキー|AKIA[0-9A-Z]{16}'
  'Google API キー|AIza[0-9A-Za-z_-]{35}'
  'Telegram ボットトークン|[0-9]{8,10}:[A-Za-z0-9_-]{35}'
  'Twilio SID/キー|(AC|SK)[0-9a-f]{32}'
  'Stripe 本番キー|sk_live_[0-9A-Za-z]{16,}'
  'SendGrid キー|SG\.[0-9A-Za-z_-]{16,}\.[0-9A-Za-z_-]{16,}'
  'Supabase プロジェクト URL|https://[a-z0-9]{16,}\.supabase\.co'
  'JWT トークン (Supabase キー等)|eyJ[0-9A-Za-z_-]{8,}\.eyJ[0-9A-Za-z_-]{8,}'
  '秘密鍵|-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'ベタ書きの認証情報|(API_KEY|APIKEY|TOKEN|SECRET|PASSWORD|PASSWD)["'"'"']?\s*[:=]\s*["'"'"'][0-9A-Za-z_/+-]{24,}["'"'"']'
)
SECRET_NG=0
for entry in "${SECRET_PATTERNS[@]}"; do
  name="${entry%%|*}"
  regex="${entry#*|}"
  raw="$(grep -rIEn --exclude-dir='.git' "$regex" "$STAGE" 2>/dev/null || true)"
  [ -z "$raw" ] && continue
  hits="$(printf '%s\n' "$raw" | drop_fakes)"
  raw_n="$(printf '%s\n' "$raw" | grep -c . || true)"
  hit_n="$(printf '%s\n' "$hits" | grep -c . || true)"
  FAKE_SKIPPED=$((FAKE_SKIPPED + raw_n - hit_n))
  if [ -n "$hits" ]; then
    echo "  ❌ $name の疑い:"
    printf '%s\n' "$hits" | sed "s|$STAGE/|     |"
    SECRET_NG=1
  fi
done
if [ "$SECRET_NG" = 1 ]; then
  echo ""
  echo "❌ 実在しそうな秘密情報が見つかりました（上記）。公開を中止しました。"
  echo "   該当箇所を環境変数に置き換えてから、もう一度実行してください。"
  echo "   （出力先 $DEST は作成していません）"
  exit 1
fi
if [ "$FAKE_SKIPPED" -gt 0 ]; then
  echo "  ✅ 秘密情報は検出されませんでした（明らかなダミー値 ${FAKE_SKIPPED} 件は除外）。"
else
  echo "  ✅ 秘密情報は検出されませんでした。"
fi

# --- PII / ローカル環境の痕跡スキャン（既定は警告のみ／--strict で中止） ----
echo "▶ 個人情報・ローカル痕跡をスキャン中…"
PII_PATTERNS=(
  '電話番号|(0[0-9]{1,4}-[0-9]{1,4}-[0-9]{4}|0[789]0[0-9]{8})'
  'メールアドレス|[0-9A-Za-z._%+-]+@[0-9A-Za-z.-]+\.[A-Za-z]{2,}'
  'ローカルの絶対パス|/Users/[0-9A-Za-z._-]+/'
  '個人の Telegram/Slack ID|(ALLOWED_TELEGRAM_USER_IDS|SLACK_ADMIN_USER_IDS|SLACK_BRAIN_CHAT_ID)\s*=\s*[0-9]{6,}'
)
PII_FOUND=0
for entry in "${PII_PATTERNS[@]}"; do
  name="${entry%%|*}"
  regex="${entry#*|}"
  # 明らかなプレースホルダ（example.com / noreply / <あなた> 等）は除外する
  if hits="$(grep -rIEn --exclude-dir='.git' "$regex" "$STAGE" 2>/dev/null \
      | grep -vEi 'example\.(com|org|net)|noreply|your-?(name|email)|<|\.\.\.|\.example\.|123456789|1234-5678|0000-0000|〇〇|（例）|you@|/Users/you/|/home/you/' || true)"; then
    if [ -n "$hits" ]; then
      echo "  ⚠️ $name の可能性:"
      echo "$hits" | head -20 | sed "s|$STAGE/|     |"
      total="$(printf '%s\n' "$hits" | wc -l | tr -d ' ')"
      [ "$total" -gt 20 ] && echo "     … 他 $((total - 20)) 件"
      PII_FOUND=1
    fi
  fi
done
if [ "$PII_FOUND" = 1 ]; then
  if [ "$STRICT" = 1 ]; then
    echo ""
    echo "❌ --strict 指定のため、上記の個人情報候補で公開を中止しました。"
    exit 1
  fi
  echo "  ⚠️ 上記は自動判定です。公開前に目視で確認してください（--strict で中止扱いにできます）。"
else
  echo "  ✅ 個人情報らしき記述は見つかりませんでした。"
fi

# --- Python 構文チェック（コピー欠落・破損の検知） --------------------------
if [ "$VERIFY" = 1 ] && command -v python3 >/dev/null 2>&1; then
  echo "▶ Python 構文を確認中…"
  if ! python3 - "$STAGE" <<'PY'
import ast, pathlib, sys
root = pathlib.Path(sys.argv[1])
bad = []
for path in sorted(root.rglob("*.py")):
    try:
        ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except SyntaxError as exc:
        bad.append(f"     {path.relative_to(root)}:{exc.lineno}: {exc.msg}")
if bad:
    print("  ❌ 構文エラー:")
    print("\n".join(bad))
    sys.exit(1)
print(f"  ✅ {len(list(root.rglob('*.py')))} 個の .py は構文 OK。")
PY
  then
    echo ""
    echo "❌ 構文チェックに失敗しました。公開を中止しました。"
    exit 1
  fi
fi

# --- 内容サマリ -------------------------------------------------------------
FILE_COUNT="$(find "$STAGE" -type f -not -path '*/.git/*' | wc -l | tr -d ' ')"
TOTAL_SIZE="$(du -sh "$STAGE" 2>/dev/null | cut -f1 | tr -d ' ')"
echo "▶ 内容: ${FILE_COUNT} ファイル / ${TOTAL_SIZE}"
BIG="$(find "$STAGE" -type f -size +1024k -not -path '*/.git/*' 2>/dev/null || true)"
if [ -n "$BIG" ]; then
  echo "  ⚠️ 1MB を超えるファイル（公開して良いか確認してください）:"
  echo "$BIG" | sed "s|$STAGE/|     |"
fi

# --- dry-run はここで終了（何も残さない） -----------------------------------
if [ "$DRY_RUN" = 1 ]; then
  echo ""
  echo "================================================================"
  echo " ✅ 検査のみ完了（--dry-run）。出力先は作成していません。"
  echo "    本番実行:  bash create_public_repo.command \"$DEST\""
  echo "================================================================"
  exit 0
fi

# --- git 初期化（push はしない） --------------------------------------------
cd "$STAGE"
git init -q
git checkout -q -b main 2>/dev/null || git branch -q -M main 2>/dev/null || true
git add -A
git -c user.name="${GIT_AUTHOR_NAME:-Hikaru Hasegawa}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-noreply@users.noreply.github.com}" \
    commit -q -m "Initial public release" || true
cd "$DEST_PARENT"

# 全チェック合格 → ここで初めて出力先へ移す
mkdir -p "$DEST"
rmdir "$DEST" 2>/dev/null || true
mv "$STAGE" "$DEST"
STAGE=""   # 移動済みなので trap で消さない

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
