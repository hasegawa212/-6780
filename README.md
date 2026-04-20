# 私募債契約書 自動生成 Slackボット

Slackで `/shibosei` コマンドを打つとフォームが開き、入力完了後に **Word (.docx) + PDF** の私募債契約書が自動生成されてDMに届きます。

---

## セットアップ手順

### 1. Slack App を作成する

1. https://api.slack.com/apps にアクセス
2. **Create New App** → **From scratch**
3. App名: `私募債契約書ボット`、ワークスペースを選択

### 2. 権限とスコープを設定する

**OAuth & Permissions** で以下の Bot Token Scopes を追加:

| スコープ | 用途 |
|---------|------|
| `commands` | スラッシュコマンド |
| `chat:write` | メッセージ送信 |
| `files:write` | ファイルアップロード |
| `im:write` | DMチャンネルを開く |

### 3. Socket Mode を有効にする

1. **Socket Mode** → 有効化
2. App-Level Token を生成（スコープ: `connections:write`）
3. トークンをメモ（`xapp-` で始まる文字列）

### 4. スラッシュコマンドを登録する

**Slash Commands** → **Create New Command**:

| 項目 | 値 |
|-----|---|
| Command | `/shibosei` |
| Short Description | `私募債契約書を作成` |
| Usage Hint | （空欄でOK） |

### 5. アプリをインストールする

**Install App** → **Install to Workspace** → 許可

### 6. 環境変数を設定する

```bash
cp .env.example .env
```

`.env` を編集:

```
SLACK_BOT_TOKEN=xoxb-...        # OAuth & Permissions のBot User OAuth Token
SLACK_SIGNING_SECRET=...         # Basic Information のSigning Secret
SLACK_APP_TOKEN=xapp-1-...       # Socket Mode のApp-Level Token
OUTPUT_DIR=./output              # 生成ファイル保存先
```

### 7. 起動する

```bash
npm install
npm start
```

`私募債契約書ボット 起動完了` と表示されれば成功です。

---

## 使い方

1. Slackの任意のチャンネルで `/shibosei` と入力
2. 3ステップのフォームに情報を入力:
   - **ステップ1**: 社債の基本情報（発行総額、利率、償還期限など）
   - **ステップ2**: 発行者・投資家の情報（名称、住所、代表者）
   - **ステップ3**: 振込先口座、払込期日、担保条件
3. 「契約書を生成」ボタンを押すと、DMに `.docx` と `.pdf` が届きます

---

## フォーム入力項目一覧

| # | 項目名 | 入力例 |
|---|-------|-------|
| 1 | 回号 | 1 |
| 2 | 発行総額（円） | 100,000,000 |
| 3 | 各社債の金額（円） | 10,000,000 |
| 4 | 利率（年率 %） | 2.5 |
| 5 | 利払日 | 毎年6月末日及び12月末日 |
| 6 | 償還期限 | 2029年3月31日 |
| 7 | 償還方法 | 満期一括償還 / 分割償還 |
| 8 | 発行者名 | 株式会社○○ |
| 9 | 発行者住所 | 東京都千代田区... |
| 10 | 発行者代表者名 | 代表取締役 山田太郎 |
| 11 | 投資家名 | △△銀行 |
| 12 | 投資家住所 | 東京都中央区... |
| 13 | 投資家代表者名 | 代表取締役 佐藤花子 |
| 14 | 銀行名 | みずほ銀行 |
| 15 | 支店名 | 丸の内支店 |
| 16 | 口座種類 | 普通 / 当座 |
| 17 | 口座番号 | 1234567 |
| 18 | 払込期日 | 2026年5月1日 |
| 19 | 発行日 | 2026年5月1日 |
| 20 | 契約日 | 令和8年5月1日 |
| 21 | 担保条件（任意） | 無担保の場合は空欄 |

---

## ファイル構成

```
slack-bot-私募債/
├── app.js                  # メインアプリ（Slack Bolt）
├── slack-modal.js          # モーダルフォーム定義（3ステップ）
├── generate-contract.js    # 契約書生成エンジン（docx + pdf）
├── package.json
├── .env.example
├── README.md
└── output/                 # 生成されたファイルの保存先
```

---

## 本番運用のヒント

- **PM2** などで常駐化: `pm2 start app.js --name shibosei-bot`
- **PDF変換**: サーバーに `libreoffice-writer` をインストール (`sudo apt install libreoffice-writer`)
- **Google Drive連携**: 生成後にGoogle Driveにもアップロードする場合は、Google Drive APIを追加
- **ログ保存**: 生成履歴をDBに記録したい場合は、ステップ3のハンドラに保存処理を追加
