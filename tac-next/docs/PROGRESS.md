# PROGRESS — 現在の状態（唯一の基準）

再開するときは [`../CLAUDE.md`](../CLAUDE.md)（Codex 等は [`../AGENTS.md`](../AGENTS.md)）→ この文書 → `IMPLEMENTATION_PLAN.md` → 関係する ADR の順に読む。
テストしていないものは UNKNOWN と書き、推測で PASS にしない。

# Current Status

- **Current Phase**：Phase 0・1・2・7 完了、Phase 3・8 は一部（ブランチ `claude/phase3-api-webhooks`、PR #133 の上に積んでいる）。Phase 9 は仕様と表示ロジックのみ
- **Current Vertical Slice**：Contact → 電話番号 → 抑止 → 発信要求 → Fake Telephony → 通話のライフサイクル → 結果 → フォローアップ（ドメイン・アプリ層・DB・API・シミュレーターまで通った。UI が残り）
- **Overall Status**：PARTIAL（**MOCK ONLY**。UI・実プロバイダ・ユーザー管理・ログインの制限なし）／判定 **NO-GO**

# Completed
- 既存 TAC の監査（`EXISTING_APP_AUDIT.md`）と設計書一式、ADR-0001〜0013
- Phase 0（Foundation）・Phase 1（Domain）
- アプリ層の縦切り（発信・結果・キュー、インメモリ）＋ Mock プロバイダ
- 並行実装の統合（ADR-0008）、QA 1 回目（開発者自身による。独立ではない、`QA_REPORT.md`）
- UI/UX：既存 UX 監査・原則・ジャーニー・情報設計（`UX.md`）、画面仕様（`SCREEN_SPEC.md`）、デザインシステム（`DESIGN_SYSTEM.md`）、画面に依存しない表示ロジック `packages/workspace`（69 テスト）
- 危険な機能のゲート（`OUTBOUND_CALLS_ENABLED` ほか、既定 OFF、ADR-0010）
- Phase 2（Database）：`packages/db`（Drizzle＋PGlite＋node-postgres）、SQL マイグレーション 0001・0002、RLS、全ポートの PostgreSQL 実装、組織ロック `UnitOfWork.runExclusive`（ADR-0012）
- Phase 3・7・8（一部）：`apps/api`（Hono）—ログイン・ログアウト・`/v1/me`・`POST /v1/calls`・`GET /v1/calls/{id}`・結果・mock の Webhook、Cookie セッション・CSRF・ロール、Webhook の受信箱と状態の compare-and-set、電話シミュレーター（ADR-0013）
- AI 運用の土台：`CLAUDE.md`・`AGENTS.md`・`AI_WORKFLOW.md`・`agents/QA_AUDIT.md`・`agents/PRODUCTION_READINESS_AUDIT.md`・`CRITICAL_INVARIANTS.md`・`RISK_REGISTER.md`・`pnpm test:critical`（CI 必須）（ADR-0011）

# In Progress
- なし

# Blocked
- 本番 `/tac/app` の実画面は確認できない（開発環境から接続不可）。ソースで監査済み、本番のブランチは UNKNOWN
- ADR-0009（null = 上限なし をやめるか）：オーナー判断待ち
- 現行 TAC の修正（`hasegawa212/-6780` PR #132）の本番反映：オーナーの `fly deploy` 判断待ち

# Next
1. PR #133 のマージと、`tac-next (real PostgreSQL concurrency)` のブランチ保護への追加（オーナー）
2. 縦切りが API まで通ったので、**Codex で初回の独立監査**（`AI_WORKFLOW.md` STEP 7）。対象：認証・CSRF・RLS・SECURITY DEFINER 関数・Webhook・ロック
3. Phase 3 の続き：ユーザーの作成・招待（初期管理者の手順）、ログイン試行のレート制限・ロック（本番前に必須）
4. Phase 9：Call Workspace の画面（API と `packages/workspace` を使う）

# Critical Invariants（層ごとの詳細は `CRITICAL_INVARIANTS.md`）
| 不変条件 | 状態 |
|---|---|
| DNC | ドメイン・アプリ層・DB 層・API PASS／worker・AI ツールは UNKNOWN |
| Tenant Isolation | アプリ層・DB 層（RLS・複合 FK・実 PG のプール）・API（セッション・ロール・別テナントは 404）PASS／ユーザー管理は未実装 |
| Call Idempotency | アプリ層・DB・API（`Idempotency-Key`）・Webhook の重複排除 PASS／実プロバイダは UNKNOWN |
| Human Handoff | ドメイン・表示 PASS／音声・Tool Gateway・E2E は UNKNOWN |
| Kill Switch | アプリ層・設定のゲート・DB・API PASS／worker は UNKNOWN |

# Verification（2026-10-06、ローカル。ブランチ `claude/phase3-api-webhooks`）
| 種類 | 結果 |
|---|---|
| Unit＋Integration（`pnpm check`） | 564/564（36 ファイル。API の結合テストは PGlite＋シミュレーター） |
| Critical Suite | 474/474（27 ファイル） |
| Mutation smoke | 55/55 KILLED（Phase 3・7・8 の 13 変異を含む） |
| 実 PostgreSQL の並行性（`pnpm test:postgres`） | Phase 2 分は 9/9（CI、PR #133）。**今回追加した Webhook の同時到着 6 件はローカル未実行（Docker 不可）→ CI 待ち** |
| 実サーバーの起動（`pnpm --filter @tac/api start`） | 起動して `/v1/me` が 401 を返すことを確認（local・mock） |
| E2E（画面） | — （UI 未実装） |
| Typecheck・Lint・Build・Audit | OK・OK・OK・脆弱性なし |
| CI | このブランチは未 push（オーナーの承認待ち） |

# Known Issues
- インメモリの UnitOfWork はロールバックしない（PostgreSQL 実装はロールバックする：`db/test/use-cases.test.ts`）
- ユーザー・所属の作成は SQL だけ（招待・初期管理者の API・手順は未実装）
- voicemail で伝言を残さない動作・会話の記録・不在の再試行は未実装（Phase 12・6）
- OpenAPI の自動生成は未導入（`API.md` が仕様）
- 抑止の解除・全発信停止の切り替え・組織の作成はアプリから行えない（DB の権限で意図的に塞いでいる。Phase 3・5 で監査つきの経路）
- `contacts.phone_e164` は1件だけ（ERD の `phone_numbers` は Phase 4）
- 抑止は電話番号単位（顧客単位の抑止は Phase 4）
- 「1日」は直近24時間で判定（暦日ではない）

# Production Blockers
- ログイン試行のレート制限・アカウントロックが未実装（総当たりに弱い）
- 音声・AI Tool Gateway・worker・実プロバイダの Webhook が未実装（QA_REPORT §15）
- 本番 DB の運用（接続ユーザーを `tac_app` のメンバーにする・バックアップ・PITR）が未設計（`DATABASE.md`「運用」）
- 独立した監査（Codex 等）が未実施

# Last Verified
- Commit：このファイルを更新したコミット（`git log -1 -- tac-next/docs/PROGRESS.md`）
- Date：2026-10-06
- Agent：Claude Code（BUILD）

---

# 履歴

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
