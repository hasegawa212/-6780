# API — HTTP API 仕様

**状態：NOT IMPLEMENTED**（Phase 3 で Hono の HTTP 骨格、Phase 4・7・10 でエンドポイント）。
設計（エンドポイントの一覧と発信のシーケンス）は [`ARCHITECTURE.md` §I](ARCHITECTURE.md) にある。実装したら、この文書を OpenAPI 3.1 の要約と各エンドポイントの「誰が呼べるか・冪等性・エラー」の表に置き換える。

## 実装時に必ず守ること（決定済み）
- すべての入口を Zod で検証する（HTTP・Webhook・ツール引数・CSV）
- 認可は API で強制する（UI で隠すだけにしない）。organization_id はセッションから取り、リクエストの値を信用しない
- 発信 `POST /v1/calls` は `Idempotency-Key` 必須。同じキー・同じ内容は同じ結果を返し、同じキー・違う内容は 409
- エラー応答にスタックトレース・SQL・内部パス・シークレットを含めない。形式は `{ code, message, request_id }`
- 発信を拒否した理由はコード（`CONTACT_SUPPRESSED`・`OUTBOUND_STOPPED` など、`evaluateCallPolicy` の理由）で返す
