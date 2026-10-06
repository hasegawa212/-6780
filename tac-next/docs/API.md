# API — HTTP API 仕様

**状態：PARTIAL**（Phase 3・7・8・9：認証・連絡先とキャンペーンの読み取り・発信・結果・mock の Webhook。検索・取り込み・キュー・フォローアップ・抑止の管理は未実装）。
実装は `apps/api`（Hono 4.13）。設計の全体（予定のエンドポイントと発信のシーケンス）は [`ARCHITECTURE.md` §I](ARCHITECTURE.md)、判断は [ADR-0013](adr/0013-auth-and-webhooks.md)。
OpenAPI 3.1 の自動生成（`@hono/zod-openapi`）は未導入（Phase 4 で導入）。それまではこの文書が仕様。

## 共通
- 認証：Cookie `tac_session`（HttpOnly・SameSite=Lax・Path=/、staging / production では Secure）。12 時間で失効。ログインのたびに新しいセッション
- CSRF：状態を変える要求（POST / PUT / PATCH / DELETE）は `X-CSRF-Token` ヘッダーに、ログインで受け取ったトークンを付ける（セッションに紐づく。別のセッションのトークンは不可）
- 本文は `Content-Type: application/json` だけ（それ以外は 415）。64KB まで（超えたら 413）。未知の項目は 400（`organizationId` などを本文で送っても使わない）
- organization_id・担当者はセッションから決まる。別テナントの ID は 404（存在を漏らさない）
- エラーの形式：`{ "error": { "code": "CONTACT_SUPPRESSED", "message": "…", "requestId": "…", "reasons"?: […], "issues"?: [{ "path", "code" }] } }`。スタックトレース・SQL・入力値は返さない
- すべての応答に `X-Request-Id`・`X-Content-Type-Options: nosniff`・`X-Frame-Options: DENY`・`Content-Security-Policy: default-src 'none'`・`Cache-Control: no-store`（Secure のときは HSTS）
- 電話番号は応答でマスクする（`+8190****0001`）

## エンドポイント

| メソッド・パス | 誰が呼べるか | 冪等性 | 成功 | 主なエラー |
|---|---|---|---|---|
| `POST /v1/auth/login` `{email, password, organizationId?}` | 誰でも | — | 200 `{userId, displayName, organizationId, role, csrfToken, expiresAt}`＋Cookie | 401 `INVALID_CREDENTIALS`（存在しないユーザーと同じ応答）・400 `ORGANIZATION_REQUIRED`（複数所属で未指定）・403 `ORGANIZATION_NOT_ALLOWED`・400 `VALIDATION_FAILED`・415 |
| `POST /v1/auth/logout` | ログイン済み＋CSRF | 何度でも可 | 204（Cookie を消す） | 401・403 `CSRF_TOKEN_INVALID` |
| `GET /v1/me` | ログイン済み | — | 200 `{userId, displayName, organizationId, role}` | 401 |
| `GET /v1/contacts?limit&cursor` | VIEWER 以上 | — | 200 `{items:[{id, displayName, phone（マスク）, timeZone}], nextCursor}`（名前順・キーセット） | 400 `VALIDATION_FAILED`（limit は 1〜500）/ `INVALID_CURSOR` |
| `GET /v1/contacts/{id}` | VIEWER 以上 | — | 200 `{contact, suppression: NONE / SUPPRESSED / UNKNOWN}`（照会失敗は UNKNOWN。表示用で、最終判定は発信時） | 404 `CONTACT_NOT_FOUND` |
| `GET /v1/campaigns` | VIEWER 以上 | — | 200 `{items:[{id, product, paused}]}` | — |
| `POST /v1/calls` `{contactId, campaignId, mode}`＋`Idempotency-Key` | OPERATOR 以上＋CSRF | **必須**：同じキー・同じ内容は 200 で前回の通話（`replayed: true`）、違う内容は 409 | 201 `{call, replayed: false}` | 400 `IDEMPOTENCY_KEY_REQUIRED`・404 `CONTACT_NOT_FOUND` / `CAMPAIGN_NOT_FOUND`・409 `IDEMPOTENCY_KEY_REUSED`・**422 発信の拒否**（`code` は `evaluateCallPolicy` の先頭の理由：`OUTBOUND_DISABLED_BY_CONFIG`・`OUTBOUND_STOPPED`・`CONTACT_SUPPRESSED`・`OUTSIDE_CALLING_WINDOW`・`DAILY_CAP_REACHED`・`CONTACT_ALREADY_IN_CALL` …、`reasons` に全部）・502 `PROVIDER_ERROR`（自動で掛け直さない）・504 `PROVIDER_TIMEOUT`（同じキーで再送） |
| `GET /v1/calls/{id}` | OPERATOR 以上 | — | 200 `{call}` | 404 `CALL_NOT_FOUND` |
| `POST /v1/calls/{id}/outcome` `{outcome, callbackAt?, appointmentAt?}` | OPERATOR 以上＋CSRF | 同じ結果は 200（`replayed: true`）、違う結果は 409 | 201 `{outcome, followUp, suppressed, nextAction}` | 400 `INVALID_OUTCOME`・404・409 `OUTCOME_ALREADY_RECORDED`・422（日時の不備など） |
| `POST /v1/webhooks/mock` | 署名（`X-Tac-Signature: t=<unix 秒>,v1=<HMAC-SHA256(MOCK_WEBHOOK_SECRET, "t.body")>`、前後 5 分） | `(provider, eventId)` で重複排除 | 200 `{result: APPLIED / STALE / DUPLICATE / UNKNOWN_CALL}` | 401 `WEBHOOK_SIGNATURE_INVALID`（秘密鍵が未設定でも 401）・400 `UNKNOWN_STATUS` / `VALIDATION_FAILED`。**local / test で mock のときだけ存在**（staging / production では 404） |

`call` の形：`{ id, status, contactId, campaignId, mode, to（マスク済み）, provider, providerCallId, createdAt }`。

## mock の Webhook（電話シミュレーター）
本文：`{ eventId, providerCallId, callId?, status, answeredBy?, occurredAt }`。`status` はプロバイダの語彙
（`queued` `ringing` `in-progress` `completed` `busy` `failed` `no-answer` `canceled`）で、`packages/telephony` の `normalizeMockStatus` がドメインの状態に正規化する。知らない語彙は 400（推測で状態を作らない）。
状態は compare-and-set で進め、終端状態は後退しない（ADR-0013）。生データは `webhook_events` に保存する。

## 起動
`pnpm --filter @tac/api start`（tsx）。設定は環境変数（`.env.example`）。
local / test は `DATABASE_URL` が無ければメモリ上の PGlite で、マイグレーションを自動で適用する。`DEV_SEED_PASSWORD` を指定するとデモ用の組織・担当者（`operator@example.test`）・リードを作る。mock の Webhook は時刻が来たら自分の受け口へ署名つきで自動配信する（local / test だけ）。staging / production は自動で適用せず、未適用があれば起動しない。
ユーザーの作成（招待・初期管理者）はまだ API が無い（`DATABASE.md`「運用」）。
