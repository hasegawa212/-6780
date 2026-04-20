# Slack導入手順（対象チャンネル: C0A3H8NKS3E）

このボットを Slack チャンネル `C0A3H8NKS3E` で利用できるようにする手順です。

## 1. Slack App を作成

1. https://api.slack.com/apps → **Create New App** → **From a manifest**
2. ワークスペースを選択
3. YAMLタブに `slack-manifest.yaml` の内容を貼り付け → **Create**

## 2. App-Level Token を生成

1. 作成されたアプリの **Basic Information** を開く
2. **App-Level Tokens** → **Generate Token and Scopes**
3. スコープ `connections:write` を追加 → 生成
4. 表示された `xapp-...` をコピー（これが `SLACK_APP_TOKEN`）

## 3. ワークスペースにインストール

1. **Install App** → **Install to Workspace** → 許可
2. 表示された `xoxb-...` をコピー（`SLACK_BOT_TOKEN`）
3. **Basic Information** の **Signing Secret** をコピー（`SLACK_SIGNING_SECRET`）

## 4. 対象チャンネルにボットを招待

Slack で `C0A3H8NKS3E` のチャンネルを開き、以下を送信:

```
/invite @私募債契約書ボット
```

## 5. `.env` を作成

```bash
cp .env.example .env
```

`.env` を編集して3トークンを記入（`ALLOWED_CHANNELS` と `ADMIN_CHANNEL` は `C0A3H8NKS3E` が既にセット済み）:

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
SLACK_APP_TOKEN=xapp-1-...
OUTPUT_DIR=./output
ALLOWED_CHANNELS=C0A3H8NKS3E
ADMIN_CHANNEL=C0A3H8NKS3E
```

## 6. 起動（Docker 推奨）

```bash
docker compose up -d --build
docker compose logs -f shibosei-bot
```

ログに `私募債契約書ボット 起動完了` が出れば成功。

## 7. 動作確認

チャンネル `C0A3H8NKS3E` で以下を実行:

```
/shibosei
```

モーダルが開き、4ステップ入力後に DM に `.docx` と `.pdf` が届きます。
同時に `C0A3H8NKS3E` チャンネルにも生成サマリー（誰が・何を生成したか）が自動投稿されます。

## トラブルシュート

| 症状 | 対処 |
|-----|-----|
| `/shibosei` が応答しない | ボットがチャンネルに招待されているか確認、Socket Mode 有効化確認 |
| `このチャンネルからは /shibosei を実行できません` | `.env` の `ALLOWED_CHANNELS` を確認 |
| PDF が文字化け | `Dockerfile` 経由なら解決済み。素のサーバーなら `sudo apt install fonts-noto-cjk` |
| 管理通知が来ない | ボットを `C0A3H8NKS3E` に招待していない可能性。`/invite @私募債契約書ボット` |
