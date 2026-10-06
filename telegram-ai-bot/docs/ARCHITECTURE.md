# ARCHITECTURE.md

## 全体像
```
台帳(followup.json) → autofollow.select_next → ゲート(decide) → outbound._create_call(Twilio)
                                                                   │
                        着信応答 → TwiML <Connect><Stream> ───────┘
                                   │
                   Twilio Media Streams ⇄ tac/realtime.py ⇄ OpenAI Realtime(さくら)
                                   │
                   さくらが transfer_to_agent → redirect_call → <Dial>担当携帯
```

## コンポーネント
- **Flask サーバ** `tac/server.py`: Webhook とオート操作 API のエントリ。
  - `/tac/autofollow/status|toggle|run|run-batch|dtmf|call-status|dashboard`
  - `/tac/amd-status`（AMD結果→機械なら切断）, `/tac/media-stream`（WS）, `/tac/handoff-result`
- **エンジン** `tac/autofollow.py`: 純粋関数 `decide/select_next/run_once/run_batch` + 状態I/O。
- **発信** `tac/outbound.py`: Twilio Calls API（インラインTwiML）、`redirect_call`。
- **会話** `tac/realtime.py`: セッション設定・挨拶・barge-in・転送ツール（純粋関数に分離してテスト可能）。
- **安全系**: `consent.py` / `dnc.py` / `calling_hours.py` / `rate_limit.py`。
- **設定** `tac/config.py`: 全ノブは env 由来。既定は安全側（OFF）。

## 設計原則
- 副作用（実発信・ネットワーク）は薄い外縁に閉じ込め、**判断は純粋関数**でテスト。
- 状態は JSON ファイル（本番は永続ボリューム推奨）。PII を含むため gitignore。
- Webhook は Twilio から叩かれる（トークン不要）。操作 API はトークン必須。

## 既知のギャップ（IMPLEMENTATION_PLAN 参照）
- Webhook の **idempotency（重複/遅延/順序逆転）** 対策が未実装。
