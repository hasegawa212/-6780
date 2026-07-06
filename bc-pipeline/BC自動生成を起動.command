#!/bin/bash
# ダブルクリックで BC自動生成アプリをセットアップ＆起動する（Mac用）。
# 初回だけ自動でセットアップ、2回目以降はそのまま起動→ブラウザが自動で開く。
# 止める時: この黒いウィンドウを閉じる（またはウィンドウ内で Control+C）。

cd "$(dirname "$0")" || exit 1
clear
echo "======================================"
echo "  BC自動生成アプリ  セットアップ＆起動"
echo "======================================"
echo

# 1) Python の確認
if ! command -v python3 >/dev/null 2>&1; then
  echo "⚠️  Python が見つかりません。"
  echo "    https://www.python.org/downloads/macos/ から Python をインストールして、"
  echo "    もう一度このファイルをダブルクリックしてください。"
  open "https://www.python.org/downloads/macos/" 2>/dev/null
  echo; read -n 1 -s -r -p "Enterで閉じます…"; exit 1
fi

# 2) 仮想環境＋依存パッケージ（初回のみ時間がかかります）
if [ ! -d .venv ]; then
  echo "▶ 初回セットアップ中…（数分かかります。お待ちください）"
  python3 -m venv .venv || { echo "venv作成に失敗"; read -n 1 -s -r; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt || { echo "依存パッケージの導入に失敗"; read -n 1 -s -r; exit 1; }

# 3) APIキー（自動読取に使用。無くても手入力で作成できます）
if [ ! -f .env ] || ! grep -q "ANTHROPIC_API_KEY=sk" .env 2>/dev/null; then
  echo
  echo "▶ AnthropicのAPIキー（sk-ant-… で始まる）があれば貼り付けてEnter。"
  echo "  無ければ空のままEnter（自動読取は使えませんが、手入力で作成できます）。"
  read -r KEY
  echo "ANTHROPIC_API_KEY=$KEY" > .env
fi
set -a; . ./.env; set +a

# 4) ログインユーザー（初回のみ）
if [ ! -f users.json ]; then
  echo
  echo "▶ ログイン用ユーザーを1人つくります。"
  read -r -p "  ユーザーID（例: hikaru）: " NEWUSER
  [ -z "$NEWUSER" ] && NEWUSER="hikaru"
  python3 manage_users.py add "$NEWUSER" || echo "  （ユーザー作成をスキップ）"
fi

# 5) 起動 → 3秒後にブラウザを自動で開く
PORT="${BC_PORT:-8800}"
echo
echo "▶ 起動します。数秒でブラウザ（Safari）が自動で開きます。"
echo "  開かない場合は、Safariのアドレス欄に  localhost:${PORT}  と入力してください。"
echo "  ※ このウィンドウは開いたままにしてください（閉じるとアプリも止まります）。"
( sleep 3; open "http://localhost:${PORT}/" 2>/dev/null ) &
exec .venv/bin/uvicorn bc_service:app --host 0.0.0.0 --port "${PORT}"
