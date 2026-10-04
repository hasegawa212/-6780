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

## 有効化に必要な手順

### 1. 環境変数を設定

```bash
# 必須
OPENAI_API_KEY=sk-...          # Realtime API 利用可の有料アカウント

# 任意（カスタマイズ）
TAC_REALTIME_MODEL=gpt-4o-realtime-preview   # 既定: gpt-realtime
TAC_REALTIME_VOICE=marin                      # alloy/echo/shimmer/marin/cedar 等
TAC_BUSINESS_INFO_FILE=tac/business_info.md   # 御社情報ファイル（FAQ等）
```

### 2. 追加依存をインストール

```bash
pip install fastapi 'uvicorn[standard]' websockets
```

### 3. Realtime サーバーを起動（Flask とは別プロセス）

```bash
uvicorn tac.realtime:app --host 0.0.0.0 --port 8091
```

**重要**: Flask (gunicorn) の `tac.server:app` とは別に、uvicorn で常駐させる必要がある。
fly.toml で `[processes]` を分けるか、Procfile で2プロセス起動する。

fly.toml 例:
```toml
[processes]
  web = "gunicorn tac.server:app -b 0.0.0.0:8080 -w 2"
  realtime = "uvicorn tac.realtime:app --host 0.0.0.0 --port 8091"

[[services]]
  internal_port = 8091
  processes = ["realtime"]
  protocol = "tcp"
  [[services.ports]]
    port = 8091
```

### 4. Twilio 番号の Voice Webhook を変更

- Twilio Console → 電話番号 → Voice Configuration
- **A]CALL COMES IN** の Webhook URL を:
  - 現在: `https://your-app.fly.dev/tac/voice`（Gather 方式）
  - 変更: `https://your-app.fly.dev/tac/voice-stream`（Media Streams 方式）
- HTTP POST を選択

### 5. 動作確認

1. 番号に電話をかける
2. 「お電話ありがとうございます、さくらです」と AI が挨拶
3. 自然な会話ができることを確認
4. AI が対応しきれない場合は人間にハンドオフ

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
