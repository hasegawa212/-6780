# OpenClaw / Telegram bot 運用 Runbook

Mac mini (`hasegawahikari@hasegawahikarinoMac-mini`) で稼働中の OpenClaw gateway と、それに紐づく launchd ジョブ一式の運用手引き。**次に同じ地雷を踏まないため**に、実際に踏んだ地雷と、その復旧手順を残す。

---

## 1. 現行構成 (2026-07-02 時点)

### プロセス / launchd ジョブ

| Label | 役割 | Interval / Trigger |
|---|---|---|
| `ai.openclaw.gateway` | 本体。Telegram bot `@hasegawa6780_bot` を制御 | KeepAlive |
| `com.openclaw.slack-sync` | Slack → Supabase 同期 | 10 分 |
| `com.openclaw.imap-watcher` | Lolipop IMAP → Slack 通知 | 5 分 |
| `com.openclaw.morning-digest` | 前日 Slack を Claude で要約 → Slack 投稿 | 毎日 09:00 |
| `com.openclaw.health` | ヘルスチェック + Slack アラート | 5 分 |
| `com.openclaw.maintenance` | バックアップ / セッション掃除 / ログ rotate | 毎日 03:00 |

### 主要ファイル / ディレクトリ

```
~/.openclaw/
├── openclaw.json               # OpenClaw 本体設定 (JSON)
├── openclaw.json.bak           # backup (旧手動、以降は maintenance.sh が管理)
├── backups/openclaw-json/       # 日次バックアップ (最大 30 日)
├── credentials/                # Telegram allow list 等
├── agents/main/sessions/        # チャットセッション (jsonl)
├── logs/gateway.log             # gateway stdout
├── logs/gateway.err.log         # gateway stderr
├── slack-sync/                  # slack_sync.py のログ
├── morning-digest/              # 朝ダイジェストのログ
├── health/                      # ヘルスチェックのログ + アラート状態ファイル
└── workspace/
    └── daemon/                  # customer_pickup など既存 daemon
```

### 環境変数 (`ai.openclaw.gateway.plist` の `EnvironmentVariables`)

| Key | 説明 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram bot トークン。@BotFather で発行。**revoke すると即通信不能**なので rotate 時は注意 |
| `ANTHROPIC_API_KEY` | `sk-ant-...`。Claude 呼び出しに必須 |
| `LITELLM_MASTER_KEY` | **使わないが、config schema に無いと startup 拒否**。`unused-dummy` で OK |
| `HOME`, `PATH`, `NODE_EXTRA_CA_CERTS` 等 | OpenClaw 標準セット |

### 使用モデル (openclaw.json の `agents.defaults.model`)

- 現在: `anthropic/claude-opus-4-6`
- **注意**: OpenClaw v2026.4.8 は `claude-opus-4-8` / `claude-sonnet-4-8` を認識しない。存在するのは `claude-opus-4-6` / `claude-sonnet-4-6` / `claude-haiku-4-5` まで。

---

## 2. 過去に踏んだ地雷と復旧手順

### 2.1 「Something went wrong / no reply」

**症状**: Telegram で bot に話しかけても応答が来ない。

#### 原因 A: Telegram bot トークンが `401 Unauthorized`

診断:
```bash
TOKEN=$(plutil -extract EnvironmentVariables.TELEGRAM_BOT_TOKEN raw ~/Library/LaunchAgents/ai.openclaw.gateway.plist)
curl -s "https://api.telegram.org/bot${TOKEN}/getMe"
```
→ `{"ok":false,"error_code":401,...}` なら復旧手順:

1. Telegram で **@BotFather** → `/mybots` → `@hasegawa6780_bot` → **API Token** → **Revoke current token**
2. 新トークンをコピー
3. ```bash
   NEW_TOKEN="<新トークン>"
   plutil -replace EnvironmentVariables.TELEGRAM_BOT_TOKEN -string "$NEW_TOKEN" ~/Library/LaunchAgents/ai.openclaw.gateway.plist
   launchctl bootout gui/$(id -u)/ai.openclaw.gateway 2>/dev/null; sleep 3
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.gateway.plist
   ```

#### 原因 B: `Unknown model` エラー

`~/.openclaw/logs/gateway.err.log` に `Unknown model: anthropic/claude-opus-4-8` 等が出ている場合。

原因: OpenClaw のバージョンが認識しないモデル名を指定している。

復旧:
```bash
node -e "
const f='/Users/hasegawahikari/.openclaw/openclaw.json';
const fs=require('fs');
const j=JSON.parse(fs.readFileSync(f,'utf8'));
const swap=m=>(m||'').replace(/opus-4-8/g,'opus-4-6').replace(/sonnet-4-8/g,'sonnet-4-6').replace(/haiku-4-7/g,'haiku-4-5');
if(j.agents?.defaults) j.agents.defaults.model=swap(j.agents.defaults.model);
if(Array.isArray(j.agents?.list)) j.agents.list.forEach(a=>{a.model=swap(a.model);});
fs.writeFileSync(f,JSON.stringify(j,null,2));
"
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
```

#### 原因 C: 「orphaned user message」でセッションが詰まっている

`gateway.err.log` に `Removed orphaned user message` が出ている場合。過去のエラーで user turn だけ残った壊れたセッションが新メッセージを吸収する。

復旧:
```bash
# 特定 sessionId が分かる場合
rm -v ~/.openclaw/agents/main/sessions/<sessionId>*.jsonl

# 全部消して仕切り直す (推奨: 日次 maintenance.sh でも 30 日超は自動削除)
rm -v ~/.openclaw/agents/main/sessions/*.jsonl
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
```

### 2.2 `Gateway failed to start: SecretRefResolutionError`

**症状**: launchd で起動しても即 exit code 1、gateway.err.log に `Environment variable "LITELLM_MASTER_KEY" is missing or empty` が出る。

原因: `openclaw.json` の `models.providers.litellm.apiKey` が env var 参照になっているが、対応する env var が plist に無い。litellm は使わないが schema にはある。

復旧:
```bash
plutil -replace EnvironmentVariables.LITELLM_MASTER_KEY -string "unused-dummy" ~/Library/LaunchAgents/ai.openclaw.gateway.plist
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
```

### 2.3 `Bootstrap failed: 5: Input/output error`

**症状**: `launchctl bootstrap` / `launchctl load` が失敗する。

原因: 同じ label のジョブが既に in-memory 登録されているのに plist を再 load しようとしている。

復旧:
```bash
launchctl bootout gui/$(id -u)/ai.openclaw.gateway 2>/dev/null; sleep 3
pkill -9 -f openclaw-gateway 2>/dev/null; sleep 2
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.gateway.plist
```

### 2.4 mega_bot が復活してきて Telegram を奪う

**症状**: OpenClaw を起動しても bot の応答が mega_bot の `🤖 最強 Claude ボット v4` になる。同じ token を 2 プロセスが取り合って polling が不安定。

診断:
```bash
ps aux | grep -E "mega_bot|openclaw-gateway" | grep -v grep
launchctl list | grep -iE "telegram-bot|mega"
```

復旧 (mega_bot を launchd から完全 unregister):
```bash
launchctl bootout gui/$(id -u)/com.martialarts.telegram-bot 2>/dev/null
launchctl remove com.martialarts.telegram-bot 2>/dev/null
pkill -9 -f mega_bot.py 2>/dev/null

# plist を .disabled にしておくと再起動しても復活しない
[ -f ~/Library/LaunchAgents/com.martialarts.telegram-bot.plist ] && \
  mv ~/Library/LaunchAgents/com.martialarts.telegram-bot.plist \
     ~/Library/LaunchAgents/com.martialarts.telegram-bot.plist.disabled
```

### 2.5 OpenClaw CLI が壊れる (`Cannot read properties of undefined (reading 't')`)

**症状**: `openclaw --help` / `openclaw channels` 等の CLI が起動時に TypeError で落ちる。gateway は動いていることもある。

原因: `openclaw.json` の `models.providers.litellm` や `plugins.entries.litellm` を完全削除すると、slack extension の contract-api が schema 参照で落ちる。「不要だから消す」は不可、「使わないが定義は残す」が正解。

復旧: バックアップから戻す。
```bash
ls -la ~/.openclaw/backups/openclaw-json/ | tail -5
cp ~/.openclaw/backups/openclaw-json/openclaw-<最新日付>.json ~/.openclaw/openclaw.json
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
```

### 2.6 slack-sync の Supabase 500 (statement timeout)

**症状**: `slack-sync.stderr.log` に `Supabase POST messages 500: {"code":"57014","message":"canceling statement due to statement timeout"}` が出続ける。

原因: HNSW インデックスの更新が遅く、バッチ upsert が 8 秒 (Supabase Free の statement timeout) を超える。

対処 (今後実装予定):
- `slack_sync.py` のバッチサイズを 20 → 5 に落とす
- または HNSW → IVFFlat に変更
- または Supabase Pro プランに移行

### 2.7 OpenAI 側の 403 (embedding)

**症状**: `slack-sync` に `HTTP Error 403: Forbidden` が続く。

原因: OpenAI プロジェクトが `text-embedding-3-small` にアクセス権を持っていない (Persona 本人確認未完了)。

対処:
1. https://platform.openai.com/settings/organization/projects → 該当 project → **Limits** → モデル有効化 (本人確認が要求される場合は Persona で完了)
2. または他の Anthropic API を使わない OpenAI アカウントの sk-キーに差し替え
3. plist の `OPENAI_API_KEY` を新値で書き換え → `launchctl kickstart -k gui/$(id -u)/com.openclaw.slack-sync`

---

## 3. 定期メンテナンス (自動化済)

`com.openclaw.maintenance` が毎日 03:00 に:

- `openclaw.json` を `~/.openclaw/backups/openclaw-json/openclaw-YYYY-MM-DD.json` に保存 (30 日で自動削除)
- `~/.openclaw/agents/main/sessions/` の tombstone (`*.deleted.*` / `*.reset.*` / `*.checkpoint.*.jsonl`) を削除
- session ファイルで 30 日以上古い `*.jsonl` を削除
- 各種ログを 100MB 超で gzip、90 日で削除

手動で回したい時:
```bash
bash ~/-6780/workspace/notes/openclaw-health/maintenance.sh
```

---

## 4. 監視 (自動化済)

`com.openclaw.health` が 5 分ごとに:

1. `openclaw-gateway` プロセス生存
2. Telegram `getMe` 200
3. Anthropic API 疎通 (Haiku ping)
4. session ファイル数 (500 超で警告)
5. `gateway.err.log` の直近 30 分に `Unknown model` / `Unauthorized` / `SecretRefResolutionError` / `Gateway failed to start` / `surface_error reason=timeout` が無いか

異常検知したら Slack `SLACK_WEBHOOK` に投稿。同じ症状は 1 時間に 1 回だけ (state ファイル `~/.openclaw/health/alert-*.ts` で制御)。

手動で回したい時:
```bash
SLACK_WEBHOOK="..." TELEGRAM_BOT_TOKEN="..." ANTHROPIC_API_KEY="..." \
  python3 ~/-6780/workspace/notes/openclaw-health/health.py
```

---

## 5. 完全リセット手順 (最終手段)

すべて壊れた時に一から立て直す:

```bash
# 1. 全 daemon 停止
for label in ai.openclaw.gateway com.openclaw.slack-sync com.openclaw.imap-watcher \
             com.openclaw.morning-digest com.openclaw.health com.openclaw.maintenance; do
    launchctl bootout gui/$(id -u)/$label 2>/dev/null
done
pkill -9 -f openclaw-gateway 2>/dev/null

# 2. openclaw.json をバックアップから復元
cp ~/.openclaw/backups/openclaw-json/openclaw-<最新>.json ~/.openclaw/openclaw.json

# 3. session 全消去
rm -rf ~/.openclaw/agents/main/sessions/*.jsonl* 2>/dev/null

# 4. gateway を立ち上げる
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.gateway.plist

# 5. 他 daemon
for label in com.openclaw.slack-sync com.openclaw.imap-watcher \
             com.openclaw.morning-digest com.openclaw.health com.openclaw.maintenance; do
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$label.plist
done

# 6. Telegram で /start してみる
```
