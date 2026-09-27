# TAC 本番デプロイ手順

Mac + ngrok から脱却し、**固定 HTTPS URL・常時稼働**で運用するための手順です。
どのプラットフォームでも動くよう **Docker 化**してあります（例は Fly.io）。

> 重要: 会話状態はプロセス内メモリに持つため **1インスタンス／1ワーカー固定**で運用します
> （水平スケールしない）。ConversationRelay の WebSocket も gthread ワーカーで動きます。

---

## 0. 事前準備（漏洩した秘密情報のローテート）
本番前に、これまでチャット等に出た秘密情報を**必ず再発行**してください。
- Twilio: Auth Token を Console でローテート
- OpenAI（S2S を使う場合）: 新しい API キーを発行、古いものは失効
- 発信トークン `TAC_OUTBOUND_TOKEN`: 新しいランダム値を生成

## 1. 必要な環境変数（本番）
| 変数 | 用途 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude（頭脳） |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | 発信 REST・署名検証 |
| `TAC_CALLER_ID` | 発信元 Twilio 番号（例 +16592103801） |
| `TAC_AGENT_NUMBER` | 担当者の電話番号 |
| `TAC_OUTBOUND_TOKEN` | 発信 API の操作者トークン |
| `TAC_VERIFY_TWILIO_SIGNATURE` | `true`（着信なりすまし対策） |
| `TAC_PUBLIC_BASE_URL` | 公開 URL（署名検証の URL 再構成に必須） |
| `TAC_RELAY_TTS_PROVIDER` / `TAC_RELAY_VOICE` | `Google` / `ja-JP-Neural2-B` |
| `TAC_RECORD_CALLS` / `TAC_RECORDING_CONSENT_TEXT` | 録音する場合のみ |
| `TAC_BUSINESS_INFO_FILE` | 業務知識ファイル（任意） |

## 2. Fly.io でのデプロイ
```bash
# telegram-ai-bot ディレクトリで
cd telegram-ai-bot
brew install flyctl            # 未導入なら
fly auth login

# アプリ作成（名前は任意。fly.toml の app 名と一致させる）
fly apps create tac-martial-arts

# 永続ボリューム（DNC 等の保存用）
fly volumes create tac_data --size 1 -r nrt

# 秘密情報を投入（値は本物に置き換え。履歴に残さないよう注意）
fly secrets set \
  ANTHROPIC_API_KEY=sk-ant-... \
  TWILIO_ACCOUNT_SID=AC... \
  TWILIO_AUTH_TOKEN=... \
  TAC_CALLER_ID=+16592103801 \
  TAC_AGENT_NUMBER=+81... \
  TAC_OUTBOUND_TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")

# デプロイ（fly.toml の [env] と [build] を使う。context は tac/）
fly deploy -c tac/fly.toml -a tac-martial-arts

# URL 確認（例 https://tac-martial-arts.fly.dev）
fly status
```
> `TAC_PUBLIC_BASE_URL` は fly.toml に入れてあります。app 名を変えたら fly.toml の
> `app` と `TAC_PUBLIC_BASE_URL` も合わせて変更してください。

## 3. Twilio の Webhook を本番 URL に向ける
番号 `+16592103801` の Voice Webhook を、ngrok から本番 URL に変更:
- 着信AI（ConversationRelay・日本語音声）: `https://<app>.fly.dev/tac/voice-relay`
- （Gather 方式を使う場合）: `https://<app>.fly.dev/tac/voice`

Console または API（Auth Token 必要）で更新。

## 4. 動作確認
```bash
curl -s https://<app>.fly.dev/            # "tac-server OK"
# 着信: 番号に電話 → 日本語で応答
# 発信: 
curl -s -X POST "https://<app>.fly.dev/tac/call" \
  -H "X-TAC-Token: $TAC_OUTBOUND_TOKEN" \
  --data-urlencode "to=+81xxxxxxxxxx"
```

## 5. 運用メモ
- **ログ**: `fly logs`
- **DNC**: `/data/dnc.txt`（ボリューム永続）。断られたら `/tac/dnc` で登録
- **S2S（OpenAI Realtime）**を使う場合は `tac/realtime.py` を別サービスとして
  `tac/realtime.requirements.txt` でデプロイ（本 Dockerfile は着信AI＋発信ブリッジ用）

## 代替プラットフォーム
同じ Docker イメージで動きます:
- **Render / Railway**: Dockerfile を指定。常時稼働プラン推奨（無料枠は spin-down で電話取りこぼしの恐れ）。永続ディスクを `/data` にマウントし `TAC_DNC_FILE=/data/dnc.txt`
- **VPS（Ubuntu 等）**: `docker build -f tac/Dockerfile -t tac tac && docker run -d --restart=always -p 443:8090 --env-file tac/.env -v tac_data:/data tac`＋リバースプロキシ(Caddy/Nginx)で HTTPS（context は tac/）

## トラブルシュート
- **ConversationRelay の WS がつながらない**: gthread で動くはずですが、環境によっては
  gevent が必要。`tac/requirements.txt` に `gevent>=23.0` を足し、Dockerfile CMD の
  `--worker-class gthread` を `--worker-class gevent` に変更して再デプロイ。
- **着信で 403**: `TAC_VERIFY_TWILIO_SIGNATURE=true` かつ `TAC_PUBLIC_BASE_URL` が
  実 URL と一致しているか確認（不一致だと署名が合わず 403）。
