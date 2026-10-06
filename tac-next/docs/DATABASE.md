# DATABASE — データベース

**状態：NOT IMPLEMENTED**（Phase 2：PostgreSQL 16＋Drizzle、結合テストは PGlite）。
ERD は [`ARCHITECTURE.md` §H](ARCHITECTURE.md)。スキーマを作ったら、この文書にテーブルごとの制約・インデックス・RLS ポリシー・保存期間を書く。

## Phase 2 の受け入れ条件（決定済み）
- テナント分離：全テーブルに `organization_id`、アプリのスコープ＋ **RLS** の二重。別テナントのデータを取れない結合テスト
- 抑止は再起動・再接続の後も残る（INV-1 の DB 層）
- 冪等性：`calls(organization_id, idempotency_key)` の一意制約、**回線上の通話は番号ごとに1件**（`status IN (REQUESTED, DIALING, RINGING, IN_PROGRESS)` の部分一意インデックス）→ `ActiveCallExistsError`
- 結果は通話ごとに1件の一意制約 → `DuplicateOutcomeError`
- 1日上限・同時通話数の判定と保存を組織単位のロックで直列化（現在の Known Issue の解消）
- マイグレーションはスキーマ変更と同じ変更に入れる。空の DB と既存 DB からの更新の両方をテストする
- 監査ログは追記専用（アプリのロールに UPDATE / DELETE を与えない）
