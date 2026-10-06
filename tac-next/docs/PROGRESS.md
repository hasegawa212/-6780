# PROGRESS — 現在の状態（唯一の基準）

再開するときは [`../CLAUDE.md`](../CLAUDE.md)（Codex 等は [`../AGENTS.md`](../AGENTS.md)）→ この文書 → `IMPLEMENTATION_PLAN.md` → 関係する ADR の順に読む。
テストしていないものは UNKNOWN と書き、推測で PASS にしない。

# Current Status

- **Current Phase**：Phase 0・1 完了。Phase 9 は仕様と表示ロジックのみ。次は Phase 2（Database）
- **Current Vertical Slice**：Contact → 電話番号 → 抑止 → 発信要求 → Fake Telephony → 通話のライフサイクル → 結果 → フォローアップ（ドメイン・アプリ層はインメモリで済。DB・API・Fake Telephony のシナリオ・UI が残り）
- **Overall Status**：PARTIAL（**MOCK ONLY**。HTTP サーバー・DB・UI・実プロバイダなし）／判定 **NO-GO**

# Completed
- 既存 TAC の監査（`EXISTING_APP_AUDIT.md`）と設計書一式、ADR-0001〜0011
- Phase 0（Foundation）・Phase 1（Domain）
- アプリ層の縦切り（発信・結果・キュー、インメモリ）＋ Mock プロバイダ
- 並行実装の統合（ADR-0008）、QA 1 回目（開発者自身による。独立ではない、`QA_REPORT.md`）
- UI/UX：既存 UX 監査・原則・ジャーニー・情報設計（`UX.md`）、画面仕様（`SCREEN_SPEC.md`）、デザインシステム（`DESIGN_SYSTEM.md`）、画面に依存しない表示ロジック `packages/workspace`（69 テスト）
- 危険な機能のゲート（`OUTBOUND_CALLS_ENABLED` ほか、既定 OFF、ADR-0010）
- AI 運用の土台：`CLAUDE.md`・`AGENTS.md`・`AI_WORKFLOW.md`・`agents/QA_AUDIT.md`・`agents/PRODUCTION_READINESS_AUDIT.md`・`CRITICAL_INVARIANTS.md`・`RISK_REGISTER.md`・`pnpm test:critical`（CI 必須）（ADR-0011）

# In Progress
- なし

# Blocked
- 本番 `/tac/app` の実画面は確認できない（開発環境から接続不可）。ソースで監査済み、本番のブランチは UNKNOWN
- ADR-0009（null = 上限なし をやめるか）：オーナー判断待ち
- 現行 TAC の修正（`hasegawa212/-6780` PR #132）の本番反映：オーナーの `fly deploy` 判断待ち

# Next
1. Phase 2：PostgreSQL＋Drizzle・RLS・PGlite の結合テスト（テナント分離・抑止の永続化・一意制約・上限の競合の解消）— 受け入れ条件は `DATABASE.md`
2. Phase 3・7・8：HTTP 骨格と `POST /v1/calls`（`withDeploymentGate` を組み込む）・Webhook 受信・Fake Telephony のシナリオ
3. 縦切りが API まで通ったら、Codex で初回の独立監査（`AI_WORKFLOW.md` STEP 7）

# Critical Invariants（層ごとの詳細は `CRITICAL_INVARIANTS.md`）
| 不変条件 | 状態 |
|---|---|
| DNC | ドメイン・アプリ層 PASS／DB・API・worker は UNKNOWN |
| Tenant Isolation | アプリ層 PASS（インメモリ）／DB・API・認証は UNKNOWN |
| Call Idempotency | アプリ層 PASS（インメモリ）／DB の一意制約は UNKNOWN |
| Human Handoff | ドメイン・表示 PASS／音声・Tool Gateway・E2E は UNKNOWN |
| Kill Switch | アプリ層・設定のゲート PASS／API・worker は UNKNOWN |

# Verification（2026-10-06、ローカル）
| 種類 | 結果 |
|---|---|
| Unit（全体） | 382/382 |
| Critical Suite | 243/243 |
| Mutation smoke | 28/28 KILLED |
| Integration / Contract / Security / E2E | — （層が未実装） |
| Typecheck・Lint・Build | OK・OK・OK |
| CI | push 後に確認する（自己申告ではなく CI を最終証拠にする） |

# Known Issues
- 異なる冪等キーの要求が同時に来ると、1日上限・同時通話数の上限を超えうる（判定と保存の間にロックがない。Phase 2 で組織単位のロック）
- インメモリの UnitOfWork はロールバックしない（Phase 2 の PostgreSQL で保証）
- 抑止は電話番号単位（顧客単位の抑止は Phase 4）
- 「1日」は直近24時間で判定（暦日ではない）

# Production Blockers
- DB・認証・API・Webhook 受信・音声・AI Tool Gateway が未実装 → テナント分離・抑止の永続化・API / worker の全発信停止が未検証（QA_REPORT §15）
- 独立した監査（Codex 等）が未実施

# Last Verified
- Commit：このファイルを更新したコミット（`git log -1 -- tac-next/docs/PROGRESS.md`）
- Date：2026-10-06
- Agent：Claude Code（BUILD）

---

# 履歴

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
