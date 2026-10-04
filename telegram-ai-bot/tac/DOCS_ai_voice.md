# AI音声オペレーター（さくら Realtime Voice）— 有効化手順

## 概要

OpenAI Realtime API を使い、Twilio Media Streams 経由で電話の音声を直接やり取りする。
STT→LLM→TTS の変換なし、遅延が極小で相づち・割り込み（barge-in）が人間レベル。

**コンプライアンス注意**: 一斉自動架電（オートダイヤラー）は NG。
使用可能なのは **インバウンド応対**（着信に AI が出る）と **1件ずつの手動発信**
（担当者がタップ→AIが応対）のみ。

## 既存コード

- `tac/realtime.py` — FastAPI アプリ。Twilio Media Streams ↔ OpenAI Realtime の WebSocket ブリッジ
- `tac/handoff.py` — AI が解決しきれない場合の人間への引き継ぎ
- `tac/config.py` — `OPENAI_API_KEY`, `TAC_REALTIME_MODEL`, `TAC_REALTIME_VOICE` 等
- `tac/realtime.requirements.txt` — 追加依存（fastapi, uvicorn[standard], websockets）

## 有効化に必要な手順（専用アプリ方式＝既存アプリにゼロリスク）

**方針**: 既存の `tac-martial-arts`（Flask・クリック発信・コンプラガード）は一切触らず、
AI音声だけを**別の fly アプリ `tac-martial-arts-voice`** として立てる。
fly のルーティングはポート単位なので、WebSocket(/tac/media-stream) を同一ホストの
443 に同居させると Flask 側に吸われる。専用アプリなら1プロセス1ポートで
`/tac/voice-stream`(TwiML) と `/tac/media-stream`(WS) を両方きれいに出せる。

同梱済みファイル（このリポジトリ・feature/sakura-max）:
- `tac/Dockerfile.voice` — uvicorn で `tac.realtime:app` を常駐
- `tac/fly.voice.toml` — app=`tac-martial-arts-voice`・nrt・常時稼働・WS対応
- `tac/business_info.example.md` — さくらが使う御社情報テンプレ（機密は非掲載・既定でこれを読む）
  ※ 実在の連絡先入りで非公開にしたい場合のみ `tac/business_info.md` を作り env を差し替える
- `tac/realtime.requirements.txt` — fastapi / uvicorn[standard] / websockets

### 1. 専用アプリを作成（初回のみ・Macのターミナルで）

```bash
cd ~/-6780
git fetch origin && git checkout feature/sakura-max && git pull
fly apps create tac-martial-arts-voice
```

### 2. OPENAI_API_KEY を投入（Realtime 利用可の有料アカウント）

```bash
fly secrets set OPENAI_API_KEY=sk-... -a tac-martial-arts-voice
```
※ トークンはチャットに貼らない。ターミナルで直接入力する。

### 3. デプロイ（リポジトリ直下で実行）

```bash
fly deploy -c telegram-ai-bot/tac/fly.voice.toml -a tac-martial-arts-voice
```

起動確認:
```bash
curl -s https://tac-martial-arts-voice.fly.dev/      # {"ok":true,"service":"tac-realtime",...}
curl -s https://tac-martial-arts-voice.fly.dev/tac/voice-stream  # <Response><Connect><Stream .../></Connect></Response>
```

### 4. Twilio 番号の Voice Webhook を変更（ここが本番ON の最終スイッチ）

- Twilio Console → Phone Numbers → 対象番号 → Voice Configuration
- **A CALL COMES IN** の Webhook URL を:
  - 変更先: `https://tac-martial-arts-voice.fly.dev/tac/voice-stream`
  - HTTP POST
- 元に戻すとき（AIをOFF）: Webhook を従来の着信 URL に戻すだけ。アプリは消さなくてよい。

### 任意カスタマイズ（env / fly secrets）

```bash
TAC_REALTIME_VOICE=marin                      # alloy/echo/shimmer/marin/cedar 等
TAC_REALTIME_MODEL=gpt-4o-realtime-preview    # 既定: gpt-realtime
TAC_BUSINESS_INFO_FILE=tac/business_info.example.md   # 既定でこのパスを読む
```

### 5. 動作確認

1. 番号に電話をかける
2. 「お電話ありがとうございます、株式会社Martial ArtsのAI受付さくらです」と AI が挨拶
3. 自然な会話（相づち・割り込み可）ができることを確認
4. AI が対応しきれない／人間希望のときは担当者へハンドオフ

## 概算コスト

| 項目 | 単価 | 5分通話 |
|------|------|---------|
| OpenAI Realtime（音声入力） | $0.06/min | $0.30 |
| OpenAI Realtime（音声出力） | $0.24/min | $1.20 |
| Twilio 電話（日本着信） | ~$0.03/min | $0.15 |
| **合計** | | **~$1.65/5分通話** |

※ 2026年10月時点の目安。OpenAI の料金は変動あり。

## 不足しているもの

1. **fly.toml の processes 分離**: 現在は Flask 単体。uvicorn 用の process を追加する必要あり
2. **御社情報ファイル**: `business_info.md` に営業時間・料金・FAQ を記載（AI の回答品質向上）
3. **ConversationRelay との使い分け**: `/tac/voice-relay`（Twilio STT/TTS）も別途ある。
   - Realtime: 超低遅延・自然だがコスト高。VIP/高額案件向き
   - ConversationRelay: 安定・低コスト。一般的な問い合わせ向き
