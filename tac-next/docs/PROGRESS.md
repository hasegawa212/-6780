# PROGRESS — 現在の状態（唯一の基準）

再開するときは [`../CLAUDE.md`](../CLAUDE.md)（Codex 等は [`../AGENTS.md`](../AGENTS.md)）→ この文書 → `IMPLEMENTATION_PLAN.md` → 関係する ADR の順に読む。
テストしていないものは UNKNOWN と書き、推測で PASS にしない。

# Current Status

- **Current Phase**：Phase 0・1・2・7 完了、Phase 3・8・9・11 は一部。Twilio アダプタ（#138、ADR-0015）はマージ済み。確定しない発信の照合をブランチ `claude/reconcile-uncertain-calls` で実装（ADR-0016）
- **Current Vertical Slice**：Contact → 電話番号 → 抑止 → 発信要求 → Fake Telephony → 通話のライフサイクル → 結果 → フォローアップ（画面まで通った：ログイン → リード → 発信 → シミュレーター → 状態 → 結果 → 発信禁止。フォローアップの画面が残り）
- **Overall Status**：PARTIAL（**実通話は未実施**。Twilio アダプタは偽の Twilio に対するテストだけ。招待の API・Dashboard / Follow-ups の画面なし）／判定 **NO-GO**（独立 QA の HIGH 2 件は修正済み。残る理由は Production Blockers）

# Completed
- 既存 TAC の監査（`EXISTING_APP_AUDIT.md`）と設計書一式、ADR-0001〜0014
- Phase 0（Foundation）・Phase 1（Domain）
- アプリ層の縦切り（発信・結果・キュー、インメモリ）＋ Mock プロバイダ
- 並行実装の統合（ADR-0008）、QA 1 回目（開発者自身による。独立ではない、`QA_REPORT.md`）
- UI/UX：既存 UX 監査・原則・ジャーニー・情報設計（`UX.md`）、画面仕様（`SCREEN_SPEC.md`）、デザインシステム（`DESIGN_SYSTEM.md`）、画面に依存しない表示ロジック `packages/workspace`（69 テスト）
- 危険な機能のゲート（`OUTBOUND_CALLS_ENABLED` ほか、既定 OFF、ADR-0010）
- Phase 2（Database）：`packages/db`（Drizzle＋PGlite＋node-postgres）、SQL マイグレーション 0001・0002、RLS、全ポートの PostgreSQL 実装、組織ロック `UnitOfWork.runExclusive`（ADR-0012）
- Phase 3・7・8（一部）：`apps/api`（Hono）—ログイン・ログアウト・`/v1/me`・`POST /v1/calls`・`GET /v1/calls/{id}`・結果・mock の Webhook、Cookie セッション・CSRF・ロール、Webhook の受信箱と状態の compare-and-set、電話シミュレーター（ADR-0013）
- Phase 9（一部）：`apps/web`（Next.js、webpack）—ログイン・リード・Call Workspace・結果、読み取り API（連絡先・キャンペーン）、デモ用シードとシミュレーターの自動配信（local / test）、E2E（Playwright＋axe）（ADR-0014）
- AI 運用の土台：`CLAUDE.md`・`AGENTS.md`・`AI_WORKFLOW.md`・`agents/QA_AUDIT.md`・`agents/PRODUCTION_READINESS_AUDIT.md`・`CRITICAL_INVARIANTS.md`・`RISK_REGISTER.md`・`pnpm test:critical`（CI 必須）（ADR-0011）

# In Progress
- Phase 11：確定しない発信の照合（`claude/reconcile-uncertain-calls`）。実通話は日本の番号の審査待ち

# Blocked
- 本番 `/tac/app` の実画面は確認できない（開発環境から接続不可）。ソースで監査済み、本番のブランチは UNKNOWN
- ADR-0009（null = 上限なし をやめるか）：オーナー判断待ち
- 現行 TAC の修正（`hasegawa212/-6780` PR #132、`feature/sakura-max` にマージ済み）の本番反映：オーナーの `fly deploy` 判断待ち
- Twilio の日本の番号：Regulatory Bundle「Japan: Local - Business」が Twilio の審査中（2026-10-03 提出）。承認後の手順は `RUNBOOK.md`「Twilio の番号が届いたら」
- tac-next の staging（Fly のアプリ・PostgreSQL）がまだ無い（Phase 18、オーナーの承認が必要）

# Next
1. 番号が届いたら：staging を用意して実通話 1 件（`RUNBOOK.md`、オーナーの承認が必要）
2. 独立 QA の修正を、別系統の AI（Codex 等）に再検証させる（`INDEPENDENT_QA_REPORT_20261006.md`）
3. `real PostgreSQL concurrency` と `e2e` をブランチ保護の必須に（オーナー）
3. Phase 3 の続き：招待・パスワード再設定の API（ログインの制限と運用 CLI `create-user` は済）
4. Phase 9 の続き：Follow-ups・Dashboard の画面（`bucketFollowUps`・キュー）
5. mutation smoke の並列化（CI のメインジョブが 23 分。変異が増えるほど伸びる）

# Critical Invariants（層ごとの詳細は `CRITICAL_INVARIANTS.md`）
| 不変条件 | 状態 |
|---|---|
| DNC | ドメイン・アプリ層・DB 層・API PASS／worker・AI ツールは UNKNOWN |
| Tenant Isolation | アプリ層・DB 層（RLS・複合 FK・実 PG のプール）・API（セッション・ロール・別テナントは 404）PASS／ユーザー管理は未実装 |
| Call Idempotency | アプリ層・DB・API（`Idempotency-Key`）・Webhook の重複排除 PASS／実プロバイダは UNKNOWN |
| Human Handoff | ドメイン・表示 PASS／音声・Tool Gateway・E2E は UNKNOWN |
| Kill Switch | アプリ層・設定のゲート・DB・API PASS／worker は UNKNOWN |

# Verification（2026-10-09、ローカル。ブランチ `claude/reconcile-uncertain-calls`）
| 種類 | 結果 |
|---|---|
| Unit＋Integration（`pnpm check`） | 703/703（照合で 23 件追加） |
| Critical Suite | 600/600（照合のテスト 2 ファイルを追加） |
| Mutation smoke | 88 件。追加した照合の変異 6 件はすべて KILLED（全件は CI） |
| 実 PostgreSQL の並行性 | 17/17（ローカルの PostgreSQL 16、空の DB、0006 を含む） |
| E2E（`pnpm test:e2e`） | 未実行（画面は変えていない。CI で実行） |
| Typecheck・Lint・Build | OK・OK・OK |
| 実際の Twilio | **未実施**（偽の Twilio に対するテストのみ） |

# Known Issues
- インメモリの UnitOfWork はロールバックしない（PostgreSQL 実装はロールバックする：`db/test/use-cases.test.ts`）
- ユーザーの作成は運用 CLI `create-user` だけ（招待の API は未実装）
- ログインのロックは他人のアドレスで悪用できる（15 分で自動解除。CAPTCHA・通知は未実装）
- voicemail で伝言を残さない動作・会話の記録・不在の再試行は未実装（Phase 12・6）
- OpenAPI の自動生成は未導入（`API.md` が仕様）
- 画面：新しいタブでは CSRF トークンが無く、状態を変える操作が 403（もう一度ログインで回復。ADR-0014）。画面の CSP は未設定（Phase 16）
- 画面の通話状態は 1 秒ごとのポーリング（SSE は Phase 12）
- CI のメインジョブが 23 分（mutation smoke が直列）
- 抑止の解除・全発信停止の切り替え・組織の作成はアプリから行えない（DB の権限で意図的に塞いでいる。Phase 3・5 で監査つきの経路）
- `contacts.phone_e164` は1件だけ（ERD の `phone_numbers` は Phase 4）
- 抑止は電話番号単位（顧客単位の抑止は Phase 4）
- 「1日」は直近24時間で判定（暦日ではない）

# Production Blockers
- 音声・AI Tool Gateway・worker・実プロバイダの Webhook が未実装（QA_REPORT §15）
- 本番 DB の運用（接続ユーザーを `tac_app` のメンバーにする・バックアップ・PITR）が未設計（`DATABASE.md`「運用」）
- 独立 QA（2026-10-06）の修正は、別の監査者による再検証が未実施
- 実際の Twilio との通信・実通話が未実施（ADR-0015 の UNKNOWN）

# Last Verified
- Commit：このファイルを更新したコミット（`git log -1 -- tac-next/docs/PROGRESS.md`）
- Date：2026-10-09
- Agent：Claude Code（BUILD）

---

# 履歴

## PHASE 11: 確定しない発信の照合（reconcile）— STATUS: DONE（偽の Twilio まで。実際の Twilio は未確認）
- IMPLEMENTED: `ReconcileUncertainCallsUseCase`（1 件なら ID を付けて進める・15 分見つからなければ FAILED で番号を解放・複数／一覧が引けない／一覧の無いプロバイダは変えない・別の通話の ID は除く）／
  `TelephonyProvider.findCalls`（任意）と `TwilioTelephonyProvider.findCalls`（続きのページがあれば失敗）／マイグレーション 0006 `list_uncertain_calls`・`calls_uncertain_idx`／API のプロセスで 1 分ごとに実行（Twilio のときだけ、件数だけログ）
- TESTS ADDED: application 11・telephony 4・db 3・api 5（RED を確認してから実装）
- 既存テストの変更なし
- KNOWN LIMITATIONS: Twilio の一覧への反映の遅れ・並び順は UNKNOWN（実通話で確かめる）／照合は API のプロセスで動く（worker は Phase 6）
- DOCUMENTATION: ADR-0016・`DATABASE.md`・`CRITICAL_INVARIANTS.md`・`RISK_REGISTER.md`・`RUNBOOK.md`・`IMPLEMENTATION_PLAN.md`


## PHASE 11: Production Telephony（Twilio）— STATUS: PARTIAL（実通話・照合は未）
- 一次情報：Twilio 公式 OpenAPI（twilio-oai）と公式 SDK（twilio-node 6.1.2）。twilio.com のドキュメントは開発環境から接続できない
- IMPLEMENTED: `TwilioTelephonyProvider`（担当者が先の会議ブリッジ・名乗り・録音なし・`TimeLimit`／再送しない・4xx は拒否・接続断/5xx/タイムアウトは確定しない失敗／同じ冪等キーは 1 回だけ）／
  `POST /v1/webhooks/twilio`（署名を常に検証・URL は `PUBLIC_BASE_URL` から・`{CallSid}:{CallStatus}` で重複排除・電話番号を保存しない）／
  設定 `TWILIO_AGENT_NUMBER`・`PUBLIC_BASE_URL`・`TWILIO_RING_TIMEOUT_SECONDS`・`TWILIO_CALL_TIME_LIMIT_SECONDS`／`createTelephonyProvider` が staging / production で Twilio を作る（local / test は従来どおり拒否）
- 設計の変更：現行 TAC の「お客様が先」を「担当者が先」にした（担当者のレッグが失敗したらお客様に一度も発信しない）
- TESTS ADDED: telephony 27・api 13（状態通知 12・起動 1）・config 2（RED を確認してから実装）。署名は公式 SDK の計算と一致することを確認
- 既存テストの変更：`telephony.test.ts` の「未実装のアダプタは偽装せず例外」を twilio → openai-sip に（Twilio は実装したため。意図は同じ）。`config.test.ts` の Twilio 設定の例に必須項目を追加
- KNOWN LIMITATIONS: 実通話なし／照合（reconcile）なし＝発信されなかった確定しない発信は番号をふさいだまま／転送なし（Phase 13）／担当者の番号は組織で 1 つ／留守電の判定なし／ADR-0015 の UNKNOWN（`Timestamp` の形式等）
- DOCUMENTATION: ADR-0015・`RUNBOOK.md`（番号が届いたら）・`API.md`・`CRITICAL_INVARIANTS.md`・`RISK_REGISTER.md`・`IMPLEMENTATION_PLAN.md`・`.env.example`


## 独立 QA（2026-10-06）の修正 — STATUS: DONE（再検証待ち）
- 監査：会話の文脈を持たない別エージェント（同じ Claude）による。判定 NO-GO、12 件（HIGH 2・MEDIUM 6・LOW 4）。全文と修正の記録は `INDEPENDENT_QA_REPORT_20261006.md`
- HIGH：IQA-01（先に別の結果を記録すると拒否を抑止にできず、翌日また発信）・IQA-02（組織をまたいで同じ冪等キーの発信がまとめられる）
- 監査者の再現テスト 17 件を取り込み、RED を確認してから修正。Critical Suite に追加し、修正ごとに mutation smoke の変異を追加
- マイグレーション 0005：`locate_provider_call` の通話特定を厳しくした・ログイン試行の予約 `auth_throttle_reserve`（行ロック）
- KNOWN LIMITATIONS：確定しない発信の照合なし（IQA-08）／電話番号の正規化は取り込み経路で未使用（IQA-09、Phase 4）／INFO 4 件は未対応

## 認証の強化（ログイン試行の制限・ユーザー作成）— STATUS: DONE（招待の API は範囲外）
- IMPLEMENTED: `LoginThrottle`（`AuthDeps` の必須入力）・メール 5 回 / IP 50 回で 15 分ロック・429＋Retry-After／マイグレーション 0004（`auth_throttle`・SECURITY DEFINER 関数）／`TRUSTED_CLIENT_IP_HEADER`／`validateNewPassword`／`createUser` と運用 CLI `create-user`
- TESTS ADDED: application 7・db 3・api 17・config 1・実 PG 1（CI のみ）
- VERIFICATION: Unit＋Integration ✅ 615／Critical ✅ 517／Mutation ✅ 63/63／Lint・Typecheck・Build・Audit ✅
- KNOWN LIMITATIONS: ロックの悪用（妨害）への追加対策なし／招待・パスワード再設定の API なし／CLI の引数解析はテストと実装を同時に書いた（RED を先に確認していない。代わりに CLI を起動して拒否を確認）

## PHASE 9: Call Workspace（最初の画面）— STATUS: PARTIAL
- IMPLEMENTED: `apps/web`（Next.js 16.3・React 19.3・Tailwind 4.3、webpack＋extensionAlias）／ログイン・リード（名前順・ページング）・Call Workspace（発信禁止のバナー・発信ボタン・通話の状態・結果）／`GET /v1/contacts`・`GET /v1/contacts/{id}`（抑止の状態）・`GET /v1/campaigns`／`workspace/call-view.ts`（API の応答 → 表示）／`DEV_SEED_PASSWORD`（local / test のデモ用シード）・シミュレーターの自動配信／E2E と CI の e2e ジョブ
- 見つけて直した問題：Turbopack が `./x.js` → `./x.ts` を解決できない（webpack に切り替え、ADR-0014）／並列実行の負荷で PGlite のテストが 5 秒の上限を超える（上限だけを 30 秒に）
- TESTS ADDED: API 7・表示ロジック 12・config 1・telephony 1・server 2・E2E 6
- VERIFICATION: Unit＋Integration ✅ 587／Critical ✅ 495／Mutation ✅ 58/58／E2E ✅ 6/6／Typecheck・Lint・Build・Audit ✅
- KNOWN LIMITATIONS: Dashboard・Follow-ups・検索・文字起こし・AI・引き継ぎの画面なし／新しいタブの CSRF／ポーリング／画面の CSP なし
- DOCUMENTATION: ADR-0014・`SCREEN_SPEC`・`API`・`DESIGN_SYSTEM`・`TESTING`・`CRITICAL_INVARIANTS`・`IMPLEMENTATION_PLAN`（Phase 9 仕様）・CLAUDE.md / AGENTS.md

## PHASE 3・7・8: API・認証・Webhook・シミュレーター — STATUS: PARTIAL（Phase 7 は DONE）
- IMPLEMENTED: `apps/api`（Hono 4.13、`createApp`・`startServer`）／ログイン・ログアウト・`/v1/me`・`POST /v1/calls`・`GET /v1/calls/{id}`・`POST /v1/calls/{id}/outcome`・`POST /v1/webhooks/mock`／Cookie セッション（scrypt・HMAC で保存）・CSRF・ロール／マイグレーション 0003（users・memberships・sessions・webhook_events・call_events・SECURITY DEFINER 関数・`tac_definer`）／`ApplyProviderEventUseCase`・`advanceCallStatus`（compare-and-set）／電話シミュレーター（7 シナリオ・署名）／`MOCK_WEBHOOK_SECRET`・`mockWebhooksEnabled`／`pendingMigrations`
- 見つけて直した穴（RED で確認してから修正）:
  - 発信 API の応答より先に Webhook が届くと、状態を DIALING に戻していた（`provider-events.test.ts`）
  - 同時に届いた Webhook が古い読み取りで状態を上書きしうる作りだった（CAS に変更、実 PG のテストは CI 待ち）
  - mock の通話 ID が再起動で重複していた（`MOCK-1`）→ 一意制約で発信が失敗する（`telephony.test.ts`）
- TESTS ADDED: 約 120 件（application 30・db 14・telephony 21・config 2・api 56）＋実 PG 6 件（CI のみ）
- VERIFICATION: Unit＋Integration ✅ 564／Critical ✅ 474／Mutation ✅ 55/55／Typecheck・Lint・Build・Audit ✅／実 PG（今回分）—（CI 待ち）／E2E —
- SECURITY: 認証の表・受信箱はアプリのロールから読めない／存在しないユーザーとパスワード違いは同じ応答／CSRF はセッションに紐づく／mock の Webhook は local / test だけで、秘密鍵なしは 401
- KNOWN LIMITATIONS: ユーザー管理・ログインの制限・OpenAPI なし／voicemail・会話の記録・再試行は後続
- DOCUMENTATION: `API.md`（実装に合わせて全面改訂）・ADR-0013・`DATABASE.md`・`CRITICAL_INVARIANTS.md`・`TESTING.md`・`SECURITY.md`・`VOICE.md`・`IMPLEMENTATION_PLAN.md`（Phase 3・7・8 仕様）・CLAUDE.md

## PHASE 2: Database — STATUS: DONE（CI で確認済み、PR #133）
- IMPLEMENTED: `packages/db`（Drizzle 0.45.3・PGlite 0.5.8・pg 8.23.1）／マイグレーション `0001_core_schema`（organizations・contacts・campaigns・calls・outcomes・follow_ups・suppression_entries・consents・audit_logs・system_controls）と `0002_tenant_isolation`（`tac_app` ロール・権限・RLS）／`migrate()`（SHA-256 で改変検出・未知の版で停止・advisory lock）／`TenantScope`（`SET LOCAL ROLE`＋`app.org_id`、AsyncLocalStorage でトランザクション共有）／全ポートの Pg 実装と `createPgDeps`／ポート `UnitOfWork.runExclusive` とインメモリ実装／CreateCall の判定〜保存を組織ロックの中へ
- 見つけて直した穴（RED で確認してから修正）:
  - 別々の冪等キー・別々の相手への同時要求で、1日上限・同時通話数を超えていた（Known Issue の解消。`adversarial.test.ts`）
  - 発信が成功した後の保存の失敗を `PROVIDER_ERROR` と取り違え、発信済みの通話を FAILED にしていた（DB の結合テストで発見。`create-call.test.ts`）
- TESTS ADDED: 54 件（application 4・db 50）＋実 PG の並行性 9 件（CI のみ）
- VERIFICATION: Unit＋Integration ✅ 440／Critical ✅ 355／Mutation ✅ 42/42／Typecheck ✅／Lint ✅／Build ✅／Audit ✅／実 PostgreSQL ✅ 9/9（CI）／CI ✅／E2E —
- SECURITY: アプリのロールは監査ログ・抑止の UPDATE / DELETE、全発信停止・組織の書き込みができない。組織未設定なら RLS で0行。複合外部キーで別テナントの行を参照できない
- KNOWN LIMITATIONS: API から未接続／`webhook_events`・`call_events`・`idempotency_keys` 等の ERD の残りは後続 Phase／本番 DB の運用は未設計
- DOCUMENTATION: `DATABASE.md`（テーブル・RLS・運用・テスト）・ADR-0012・`CRITICAL_INVARIANTS.md`・`TESTING.md`・`RISK_REGISTER.md`・CLAUDE.md / AGENTS.md（`test:postgres`）・CI（postgres ジョブ）

## Known Issues（2026-10-05 時点）
- 異なる冪等キーの要求が同時に来ると、1日上限・同時通話数の上限を超えうる（判定と保存の間にロックがない。Phase 2 で組織単位のロック）。
- インメモリの UnitOfWork はロールバックしない（Phase 2 の PostgreSQL で保証）。
- 抑止は電話番号単位（顧客単位の抑止は Phase 4）。
- 「1日」は直近24時間で判定（暦日ではない）。
- 本番 `/tac/app` に依頼文の機能（スマートリスト・フォロー等）があるかは UNKNOWN。

## 現行 TAC（telegram-ai-bot/tac）で見つけて対応したもの
- DNC の表記ゆれで拒否番号に発信できた不具合を修正（PR #129）。本番反映には `fly deploy` が必要。
- そのほかの重大な問題（同時通話での会話の取り違え・保留の放置・認証のないルート等）は `EXISTING_APP_AUDIT.md` の A に記録。現行側では未修正。

## QA 監査（2026-10-05）— STATUS: DONE（判定は NO-GO、`QA_REPORT.md`）
- tac-next の欠陥 5 件を再現テスト付きで修正。内訳: CRITICAL 2（別キーの同時発信で二重発信、判定後の DNC を無視して発信）、HIGH 1（判定後の全発信停止を無視）、MEDIUM 2（結果の同時送信で例外、不正な TZ で例外）。
- 曖昧な断り（今はいい・また今度・忙しい・考えておきます）を SOFT_DECLINE として検知する。抑止はせず WRAP_UP へ（〔要法務確認〕）。
- 新しいリポジトリ契約: `CallRepository.insert` は、同じ番号に回線上の通話があれば `ActiveCallExistsError`（Phase 2 で部分一意インデックスにする）。`OutcomeRepository.insert` は重複なら `DuplicateOutcomeError`。
- 現行 TAC の DNC 迂回 6 系統は `hasegawa212/-6780` PR #132 で修正（未デプロイ）。
- 残る Production Blocker は QA_REPORT §15。

## 統合（2026-10-05, ADR-0008）— STATUS: DONE
- IMPLEMENTED: `isContactable`（抑止の照会を fail closed にし、発信とキューで共通化）／`detectSafetySignals`・`applyCustomerUtterance`（発話 → Safety）／`followCategoryFromLabel`（現行のフォロー 5 分類）／`pnpm test:mutation`（CI）／`evals/`／EXISTING_APP_AUDIT F（sakura-max の監査）
- 見つけて直した穴（RED で確認してから修正）:
  - 抑止の照会が `true` 以外の truthy 値（例 `"yes"`）を返すと発信されていた（発信・キューの両方）
  - 照会の例外でキュー一覧全体が失敗していた
  - 非終端どうしの Webhook 後戻りがテストされていなかった
  - Safety → 営業フェーズの遷移を決定的に検証するテストがなかった
- TESTS ADDED: 81 件（application 5・domain 76。evals を含む）
- VERIFICATION: Unit ✅ 261／Mutation ✅ 16/16（2 回連続）／Typecheck ✅／Lint ✅／Build ✅／Integration —／E2E —
- KNOWN LIMITATIONS: PII redaction と HTTP 骨格は未移植（Phase 15 / Phase 3 で tac-next の流儀で作る）。ADR-0009 は Proposed

## Phase 報告

### PHASE 0: Foundation — STATUS: DONE
- IMPLEMENTED: pnpm workspace・TS strict・Biome・Vitest・CI（lint / typecheck / test / build / audit）・`.nvmrc`・`packages/config`（Zod で起動時に検証、安全装置は既定 ON、production では外せない、Secret は秘匿、`.env.example` 自体をテストで検証）
- TESTS ADDED: `packages/config/test/config.test.ts` 10 件
- VERIFICATION: Unit ✅／Integration —（DB なし）／E2E —（UI なし）／Typecheck ✅／Lint ✅／Build ✅
- SECURITY: シークレットは String / JSON / util.inspect とエラーメッセージに出ない。local / test で本番の電話プロバイダを指定すると起動しない
- KNOWN LIMITATIONS: まだアプリ本体（api / worker）が config を読み込んでいない（Phase 3 で接続）
- DOCUMENTATION: ADR-0007・IMPLEMENTATION_PLAN の Phase 0 仕様

### PHASE 1: Domain — STATUS: DONE（ギャップ対応を含む）
- IMPLEMENTED: Safety に COMPLAINT・SYSTEM_FAILURE、会話に PERMISSION・FAQ、発信ガードに全発信停止・組織/キャンペーンの一時停止・同時通話数・予算（ADR-0006）。CreateCall がこれらを強制
- TESTS ADDED: ドメイン 29 件・アプリ層 5 件（今回分）
- VERIFICATION: Unit ✅ 180/180／Integration —／E2E —／Typecheck ✅／Lint ✅／Build ✅
- SECURITY: 停止系は抑止より先に評価。停止中でも抑止は理由一覧に残す（隠さない）
- KNOWN LIMITATIONS: 上限系の競合（Phase 2）。Webhook 受信・シミュレーターは Phase 7・8
- DOCUMENTATION: DOMAIN.md（状態機械を更新）・ADR-0005・ADR-0006

NEXT: Phase 2（Database）— Drizzle スキーマ・マイグレーション・RLS・PGlite での結合テスト（テナント分離・DNC が再起動後も残る・上限の競合の解消）。
