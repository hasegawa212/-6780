# PROGRESS — 進捗（リポジトリを長期記憶として使う）

再開するときは、この文書 → `IMPLEMENTATION_PLAN.md` → `ARCHITECTURE.md` → `DECISIONS.md` の順に読む。

| 項目 | 内容 |
|---|---|
| Current Phase | Phase 0・1 完了＋並行実装の統合（ADR-0008）→ 次は Phase 2（Database） |
| Completed | 監査・設計書一式（依頼の構成に再編）／Phase 0（pnpm・TS・Biome・Vitest・CI・build・検証つき設定 `packages/config`）／Phase 1（電話番号・通話/会話の状態機械・Safety 9状態・発信ガード＋本番の安全装置・結果・時間帯・スコア）／アプリ層の縦切り（インメモリ）／Mock プロバイダ |
| In Progress | なし |
| Blocked | 本番 `/tac/app` の実画面は未確認（開発環境から接続不可）。ただし `feature/sakura-max` のソースで機能は監査済み（EXISTING_APP_AUDIT.md F）。本番に出ているブランチは UNKNOWN。ADR-0009（null = 上限なし をやめるか）はオーナー判断待ち |
| Next | Phase 2：PostgreSQL＋Drizzle・RLS・DNC が再起動後も残るテスト・1日上限の競合の解消 |
| Known Issues | 下記 |
| Tests Status | 261/261（evals 37 件を含む）。critical mutant smoke 16/16。lint エラー 0・型エラー 0 |
| Build Status | `pnpm build`（`tsc -b`）成功。CI でも build を実行 |
| Last Verified | 2026-10-05 |

## Known Issues
- 異なる冪等キーの要求が同時に来ると、1日上限・同時通話数の上限を超えうる（判定と保存の間にロックがない。Phase 2 で組織単位のロック）。
- インメモリの UnitOfWork はロールバックしない（Phase 2 の PostgreSQL で保証）。
- 抑止は電話番号単位（顧客単位の抑止は Phase 4）。
- 「1日」は直近24時間で判定（暦日ではない）。
- 本番 `/tac/app` に依頼文の機能（スマートリスト・フォロー等）があるかは UNKNOWN。

## 現行 TAC（telegram-ai-bot/tac）で見つけて対応したもの
- DNC の表記ゆれで拒否番号に発信できた不具合を修正（PR #129）。本番反映には `fly deploy` が必要。
- そのほかの重大な問題（同時通話での会話の取り違え・保留の放置・認証のないルート等）は `EXISTING_APP_AUDIT.md` の A に記録。現行側では未修正。

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
