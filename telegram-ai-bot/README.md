# 最強の Telegram AI ボット (Claude 搭載)

`telegram.error.Conflict: terminated by other getUpdates request` を**根絶**した、
本番運用に耐える Telegram × Claude ボットです。

## ⚠️ 最重要: Python は 3.13 を使う

**Python 3.14 は `python-telegram-bot` の `run_polling()` と非互換**で、ボットが
起動直後に `RuntimeError: There is no current event loop` でクラッシュします
（"起動" ログは出るのに一切応答しない、という症状になります）。venv は必ず
**`python3.13`** で作成してください:

```bash
python3.13 -m venv venv      # ← python3 ではなく python3.13
```

`python3` が 3.14 を指す環境（Homebrew 更新後など）で `python3 -m venv` すると
この罠にはまります。作り直しのときも必ず `python3.13` を明示。

## 🏆 最強版 `mega_bot.py`（おすすめ・全部入り）

1 つのボットで以下をすべてこなします:

- 💬 **Claude チャット**（claude-opus-4-8・文脈記憶）＋ ⚡ **ストリーミング表示**
- 🌐 **ウェブ検索**（最新情報を自動取得）
- 🏭 **ファイル生成**（コード実行でグラフ/画像/Word/Excel/PPT/PDF/CSV を作って送付）
- 🧠 **長期記憶**（あなたの事実・好みを自分で判断して保存。再起動後も記憶）
- 🤖 **先回り秘書** `/proactive`（毎朝こちらから提案・準備）/ `/assist`（今すぐ）
- 🎯 **丸投げ自動実行** `/task`（複雑な目標を計画→調査→資料作成まで自律遂行）
- 🔗 **n8n 連携** `/n8n`（会話/taskから n8n ワークフローを起動）
- ⏰ **定時タスク** `/schedule` / **自動電話** `/callat`
- 📞 **電話発信** `/call`（Twilio・自然な日本語音声。双方向リアルタイム化も可）
- 🖼 **画像理解** / 📄 **PDF・文書** / 🎤 **音声メッセージ**（要 faster-whisper）
- 🛠 **Claude Code 操作** `/code`（実際にファイル編集・コマンド実行。要認可）
- 🛡 **Conflict 根絶**（ロック＋webhook削除＋ハンドラ）

```bash
cd telegram-ai-bot
python3.13 -m venv venv && source venv/bin/activate   # ← 3.13 必須
pip install -r requirements.txt

export TELEGRAM_BOT_TOKEN="..."
export ANTHROPIC_API_KEY="sk-ant-..."
export ALLOWED_TELEGRAM_USER_IDS="123456789"   # /code /call 等を使う人のID (@userinfobot)
export CLAUDE_CODE_CWD="/path/to/your-project"  # /code の作業ディレクトリ

python mega_bot.py
```

コマンド: `/start` `/help` `/chat` `/code` `/reset` `/status` `/memory` `/forget`
`/schedule` `/schedules` `/unschedule` `/call` `/callat` `/callats` `/uncallat`
`/proactive` `/assist` `/task` `/n8n`

> チャット・画像だけなら `ALLOWED_TELEGRAM_USER_IDS` / `CLAUDE_CODE_CWD` は未設定でOK
> （`/code` `/call` など一部だけが無効になります）。

### 📞 電話まわりの追加ファイル
- `voice_agent.py` … Twilio `<Gather>` ベースの双方向AI通話（Flask）
- `realtime_agent.py` … Twilio Media Streams ↔ OpenAI Realtime API の超低遅延通話（FastAPI）

公開URL（cloudflared 等）で起動し、`VOICE_AGENT_URL` に設定すると `/call` が双方向会話になります。

### 🔁 常駐運用（macOS launchd）
Mac 起動時に自動起動・落ちても自動再起動・常に1個だけ、を実現するには
LaunchAgent plist を `~/Library/LaunchAgents/` に置き、`ProgramArguments` に
**venv の python3.13** と `mega_bot.py` を指定、`EnvironmentVariables` に各種設定、
`RunAtLoad`/`KeepAlive` を `true` にします（`launchctl load` で起動）。

---

以下は、機能を分けたシンプル版 (`bot.py` / `claude_code_bridge.py`) の説明です。

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

---

## 🛠 おまけ: Telegram から Claude Code を操作する (`claude_code_bridge.py`)

通常の AI チャット (`bot.py`) とは別に、**Telegram から Claude Code
（コーディングエージェント）を操作**するブリッジも同梱しています。
「auth.py のバグを直して」「テストを実行して結果を教えて」のように指示すると、
Claude Code が指定ディレクトリで実際にファイルを読み書き・コマンド実行し、
途中経過と結果を Telegram に返します。`claude-agent-sdk` 経由で動作します。

### ⚠️ セキュリティ（必読）

このブリッジは**ホスト上でコード/コマンドを実行できるエージェント**を動かします。
必ず `ALLOWED_TELEGRAM_USER_IDS` に**自分の Telegram ユーザーID**を設定してください。
未設定の場合は誰も操作できません（フェイルクローズ）。自分のIDは Telegram で
`@userinfobot` に話しかけると分かります。

権限モードは既定で `acceptEdits`（ファイル編集を自動承認）。Bash 等もすべて
自動実行させたい場合は `CLAUDE_CODE_PERMISSION_MODE=bypassPermissions` を
指定できますが、リスクを理解した上で信頼できる環境のみで使ってください。

### 起動

```bash
cd telegram-ai-bot
source venv/bin/activate
pip install -r requirements.txt   # claude-agent-sdk を含む (Python 3.10+)

export TELEGRAM_BOT_TOKEN="..."
export ANTHROPIC_API_KEY="sk-ant-..."
export ALLOWED_TELEGRAM_USER_IDS="123456789"     # 自分のID
export CLAUDE_CODE_CWD="/path/to/your-project"   # 作業ディレクトリ

python claude_code_bridge.py
```

> `bot.py` と `claude_code_bridge.py` は**別々の bot トークン**で動かしてください
> （同じトークンで 2 プロセスを起動すると Conflict になります）。

### コマンド

| コマンド | 説明 |
| --- | --- |
| `/start` | はじめる（認可チェック） |
| `/status` | 作業ディレクトリ・権限モード・許可ツール・セッションを表示 |
| `/reset` | Claude Code セッションを新規化 |
| `/help` | ヘルプ |

### 仕組み

- `claude-agent-sdk` の `query()` で Claude Code をヘッドレス実行
- `ResultMessage.session_id` を保存し、次のメッセージで `resume` して**会話を継続**
- `AssistantMessage` のテキストを逐次 Telegram に転送（途中経過が見える）
- `bot.py` と同じく**シングルインスタンスロック + webhook 削除**で Conflict を防止
