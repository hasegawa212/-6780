# 最強の Telegram AI ボット (Claude 搭載)

`telegram.error.Conflict: terminated by other getUpdates request` を**根絶**した、
本番運用に耐える Telegram × Claude ボットです。

## なぜ Conflict が起きていたか

```
telegram.error.Conflict: Conflict: terminated by other getUpdates request;
make sure that only one bot instance is running
```

Telegram は 1 つの bot トークンにつき **同時に 1 つの `getUpdates` ロングポーリング**しか
許可しません。古いプロセスが残ったまま新しいプロセスを起動したり、webhook と
ポーリングを併用すると、このエラーが出てボットが落ちます。

## このボットの対策（三段構え）

1. **シングルインスタンスロック** (`fcntl`) — 同じマシンで 2 つ目を起動しようとすると
   即座に弾く。二重起動を物理的に防止。
2. **起動時の webhook 削除 + 保留更新の破棄** (`delete_webhook(drop_pending_updates=True)`)
   — webhook 残骸とポーリングの競合を解消。
3. **Conflict エラーハンドラ** — 万一競合してもクラッシュさせず、原因を明示して継続。

さらに:
- 会話履歴をチャットごとに保持（文脈を理解した応答）
- 「入力中…」表示、長文の自動分割
- `SIGINT` / `SIGTERM` での graceful シャットダウン
- 既定モデルは最新の **`claude-opus-4-8`**（adaptive thinking）

## セットアップ

```bash
cd telegram-ai-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # 値を埋める
# もしくは直接環境変数をエクスポート:
export TELEGRAM_BOT_TOKEN="..."
export ANTHROPIC_API_KEY="sk-ant-..."

python bot.py
```

`.env` を使う場合は読み込んでから起動してください（例: `set -a; . ./.env; set +a; python bot.py`）。

## 既に「Conflict」で困っている場合の即時対処

別ターミナルや過去の `nohup` / `screen` セッションに古いボットが残っていないか確認します。

```bash
# 残っているボットプロセスを探す
ps aux | grep "[b]ot.py"

# 見つかったら停止
pkill -f "bot.py"

# webhook が残っていないか確認・削除 (TOKEN を置き換え)
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook?drop_pending_updates=true"
```

その後 `python bot.py` を起動すれば、シングルインスタンスロックにより
今後の二重起動は自動的に防がれます。

## コマンド

| コマンド | 説明 |
| --- | --- |
| `/start` | はじめる |
| `/help` | 使い方とモデル情報 |
| `/reset` | この会話の履歴を消去 |

## 設定（環境変数）

`.env.example` を参照。`CLAUDE_MODEL` / `CLAUDE_EFFORT` / `CLAUDE_MAX_TOKENS` /
`HISTORY_TURNS` / `SYSTEM_PROMPT` などで挙動を調整できます。
