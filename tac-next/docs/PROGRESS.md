# PROGRESS — 進捗（リポジトリを長期記憶として使う）

再開するときは、この文書 → `IMPLEMENTATION_PLAN.md` → `ARCHITECTURE.md` → `DECISIONS.md` の順に読む。

| 項目 | 内容 |
|---|---|
| Current Phase | 0（Foundation のギャップ対応：検証つき設定）→ 1（ドメインのギャップ対応） |
| Completed | 監査・設計書一式／Phase 0 の土台（pnpm・TS・Biome・Vitest・CI）／Phase 1 のドメイン（電話番号・状態機械・Safety・発信ガード・結果・時間帯・スコア）／アプリ層の縦切り（インメモリ）／Mock プロバイダ |
| In Progress | Phase 0：`packages/config`／Phase 1：Safety 状態の追加（COMPLAINT・SYSTEM_FAILURE）・会話フェーズ（PERMISSION・FAQ）・本番の安全装置（ADR-0006） |
| Blocked | 本番 `/tac/app` の実画面の確認（開発環境から接続不可。利用者のスクリーンショットか HTML が必要） |
| Next | Phase 2：PostgreSQL＋Drizzle・RLS・DNC が再起動後も残るテスト・1日上限の競合の解消 |
| Known Issues | 下記 |
| Tests Status | 146/146（2026-10-04 時点、更新前） |
| Build Status | `tsc -b` 成功 |
| Last Verified | 2026-10-04 |

## Known Issues
- 異なる冪等キーの要求が同時に来ると、1日上限を超えうる（Phase 2 で組織単位のロック）。
- インメモリの UnitOfWork はロールバックしない（Phase 2 の PostgreSQL で保証）。
- 抑止は電話番号単位（顧客単位の抑止は Phase 4）。
- 「1日」は直近24時間で判定（暦日ではない）。
- 本番 `/tac/app` に依頼文の機能（スマートリスト・フォロー等）があるかは UNKNOWN。

## 現行 TAC（telegram-ai-bot/tac）で見つけて対応したもの
- DNC の表記ゆれで拒否番号に発信できた不具合を修正（PR #129）。本番反映には `fly deploy` が必要。
- そのほかの重大な問題（同時通話での会話の取り違え・保留の放置・認証のないルート等）は `EXISTING_APP_AUDIT.md` の A に記録。現行側では未修正。
