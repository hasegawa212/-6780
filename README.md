# AI Secretary Bot — Telegram / Slack ×  Claude

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/hasegawa212/-6780/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)

Claude を“脳”にした、本番運用に耐える AI 秘書ボット。**Telegram と Slack が同じ
データ・同じ 52 ツールを共有**し、チャットだけでなく顧客台帳・記憶・知識・日報/週報・
予定・経費・ToDo・メール・リマインダー・電話発信・資料生成（Word/Excel/PDF/グラフ）まで
こなします。

> 本体は [`telegram-ai-bot/`](telegram-ai-bot/) にあります。詳しい使い方は
> [`telegram-ai-bot/README.md`](telegram-ai-bot/README.md) を参照してください。

## 主な機能

- 💬 **Claude チャット**（ストリーミング表示・長期記憶・再起動後も保持）
- 🧠 **共有ブレイン** — Telegram と Slack が同一データ・同一 52 ツールを共有
- 🌐 **ウェブ検索 / ページ取得**（最新情報の裏取り）
- 🏭 **資料生成** — コード実行でグラフ・Word・Excel・PDF・CSV を作って添付
- 🗂 **業務ツール** — 顧客台帳 / 名簿 / 日報・週報・月報 / 予定 / 経費 / ToDo / 知識ベース
- ✉️ **連携** — メール送受信 / Slack 送受信 / リマインダー / 定時タスク / n8n
- 📞 **電話発信**（Twilio・任意） / 🖼 画像理解 / 🎤 音声入力（任意）

## クイックスタート

```bash
cd telegram-ai-bot
python3.13 -m venv venv && source venv/bin/activate   # Python 3.13 必須
pip install -r requirements.txt

export TELEGRAM_BOT_TOKEN="..."        # @BotFather
export ANTHROPIC_API_KEY="sk-ant-..."  # Claude
python mega_bot.py
```

Slack 版（Telegram と同じ脳）:

```bash
pip install -r slack_bot.requirements.txt
export SLACK_BOT_TOKEN="xoxb-..." SLACK_APP_TOKEN="xapp-..."
export SLACK_BRAIN_CHAT_ID="<自分のTelegramチャットID>"  # Telegram と同データにする場合
python slack_bot.py
```

環境変数の一覧は [`telegram-ai-bot/.env.example`](telegram-ai-bot/.env.example) を参照。

## 設定（環境変数の要点）

| 変数 | 用途 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude（必須） |
| `TELEGRAM_BOT_TOKEN` | Telegram ボット |
| `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` | Slack（Socket Mode） |
| `SLACK_BRAIN_CHAT_ID` | Slack を Telegram と同じ台帳に繋ぐ Telegram チャットID |
| `ALLOWED_TELEGRAM_USER_IDS` | `/code` `/call` 等の要認可ユーザー |
| `SLACK_ADMIN_USER_IDS` | Slack 側の要認可ユーザー（未設定なら全員可） |

機密値はすべて環境変数で渡します。コードにトークンを直書きしないでください。

## ⚠️ 公開（OSS化）前の必須チェック

このリポジトリには **業務上の私的データ** が含まれているため、`Public` にする前に
必ず確認してください。**ファイルを消してもコミット履歴には残ります。**

- [ ] `data/`（財務・取引データ）、`slack_findings.md`、`location_map.md` は私的情報。
      公開リポジトリに含めないこと。
- [ ] `.env` や実トークン（`sk-ant-…` / `xoxb-…` / `xapp-…`）が履歴に無いか確認。
- [ ] 個人を特定する ID・氏名・電話番号・メールが残っていないか確認。

**推奨手順:** 履歴ごと安全に公開するには、`telegram-ai-bot/` だけを
**新しい空の公開リポジトリ**にコピーして初コミットする（私的データと過去履歴を持ち込まない）。

```bash
# 例: ボット本体だけをクリーンな新規リポジトリとして公開
mkdir ai-secretary-bot && cp -R telegram-ai-bot/* telegram-ai-bot/.gitignore .github LICENSE ai-secretary-bot/
cd ai-secretary-bot && git init && git add -A && git commit -m "Initial public release"
# その後 GitHub で空の Public リポジトリを作成し push
```

リポジトリの公開（Private → Public 切替）は GitHub の設定画面から手動で行ってください。

## ライセンス

[MIT](LICENSE) © 2026 Hikaru Hasegawa
