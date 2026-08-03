@echo off
rem ダブルクリックで BC自動生成アプリをセットアップ＆起動する（Windows用）。
rem 初回だけ自動でセットアップ、2回目以降はそのまま起動→ブラウザが自動で開く。
rem 止める時: この黒いウィンドウを閉じる（またはウィンドウ内で Ctrl+C）。
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
cls
echo ======================================
echo   BC自動生成アプリ  セットアップ＆起動
echo ======================================
echo.

rem 1) Python の確認
where python >nul 2>&1
if errorlevel 1 (
  echo [!] Python が見つかりません。
  echo     https://www.python.org/downloads/windows/ からインストールし、
  echo     インストール画面で「Add Python to PATH」に必ずチェックを入れてください。
  echo     その後、もう一度このファイルをダブルクリックしてください。
  start "" https://www.python.org/downloads/windows/
  pause
  exit /b 1
)

rem 2) 仮想環境＋依存パッケージ（初回のみ時間がかかります）
if not exist .venv (
  echo ▶ 初回セットアップ中…（数分かかります。お待ちください）
  python -m venv .venv || (echo venv作成に失敗しました & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt || (echo 依存パッケージの導入に失敗しました & pause & exit /b 1)

rem 3) APIキー（自動読取に使用。無くても手入力で作成できます）
if not exist .env (
  echo.
  echo ▶ AnthropicのAPIキー（sk-ant-… で始まる）があれば貼り付けてEnter。
  echo   無ければ空のままEnter（自動読取は使えませんが、手入力で作成できます）。
  set /p KEY=キー:
  echo ANTHROPIC_API_KEY=!KEY!> .env
)

rem 4) ログインユーザー（初回のみ。パスワードは画面の指示に従って入力）
if not exist users.json (
  echo.
  echo ▶ ログイン用ユーザーを1人つくります。
  set /p NEWUSER=  ユーザーID（例: hikaru）:
  if "!NEWUSER!"=="" set NEWUSER=hikaru
  python manage_users.py add !NEWUSER!
)

rem 5) 起動 → 数秒後にブラウザを自動で開く
if "%BC_PORT%"=="" (set PORT=8800) else (set PORT=%BC_PORT%)
echo.
echo ▶ 起動します。数秒でブラウザが自動で開きます。
echo   開かない場合は、ブラウザのアドレス欄に  http://localhost:!PORT!/  と入力してください。
echo   ※ このウィンドウは開いたままにしてください（閉じるとアプリも止まります）。
start "" http://localhost:!PORT!/
.venv\Scripts\uvicorn.exe bc_service:app --host 0.0.0.0 --port !PORT!
