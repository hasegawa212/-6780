# DATABASE — データベース

**状態：PARTIAL**（Phase 2：最初の縦切りに必要なテーブル・RLS・リポジトリまで。API への接続は Phase 3）。
実装は `packages/db`（PostgreSQL 16+＋Drizzle 0.45、結合テストは PGlite 0.5、並行性は実 PostgreSQL）。設計判断は [ADR-0012](adr/0012-database-tenant-context.md)。
ERD の全体像は [`ARCHITECTURE.md` §H](ARCHITECTURE.md)（Phase 2 で作ったのはその一部）。

## Phase 2 の受け入れ条件（決定済み）と結果
| 条件 | 状態 | 証拠 |
|---|---|---|
| テナント分離（アプリのスコープ＋RLS の二重） | PASS（PGlite）／プール上の漏れなしは CI（実 PG） | `db/test/tenant-isolation.test.ts`・`db/test-postgres/concurrency.test.ts` |
| 抑止は再起動・再接続の後も残る | PASS | `db/test/use-cases.test.ts`「DB を閉じ、開き直しても発信は拒否」 |
| 冪等キーの一意制約・回線上は番号ごとに1件 | PASS（PGlite）／同時実行は CI（実 PG） | `db/test/repositories.test.ts`・`test-postgres` |
| 結果は通話ごとに1件 | PASS | `repositories.test.ts`（`DuplicateOutcomeError`） |
| 上限の判定と保存を組織ロックで直列化 | PASS（ロックの保持は PGlite）／**同時実行での直列化は CI（実 PG）でのみ確認** | `use-cases.test.ts`（`pg_locks`）・`test-postgres` |
| マイグレーション：空の DB／既存 DB からの更新 | PASS | `db/test/migrate.test.ts` |
| 監査ログは追記専用 | PASS | `tenant-isolation.test.ts`（UPDATE / DELETE は 42501） |

元の条件：
- テナント分離：全テーブルに `organization_id`、アプリのスコープ＋ **RLS** の二重。別テナントのデータを取れない結合テスト
- 抑止は再起動・再接続の後も残る（INV-1 の DB 層）
- 冪等性：`calls(organization_id, idempotency_key)` の一意制約、**回線上の通話は番号ごとに1件**（`status IN (REQUESTED, DIALING, RINGING, IN_PROGRESS)` の部分一意インデックス）→ `ActiveCallExistsError`
- 結果は通話ごとに1件の一意制約 → `DuplicateOutcomeError`
- 1日上限・同時通話数の判定と保存を組織単位のロックで直列化（現在の Known Issue の解消）
- マイグレーションはスキーマ変更と同じ変更に入れる。空の DB と既存 DB からの更新の両方をテストする
- 監査ログは追記専用（アプリのロールに UPDATE / DELETE を与えない）

## テーブル（`packages/db/migrations/0001_core_schema.sql`）
全テーブルに `organization_id`（`organizations` は `id` 自身、`system_controls` は全体の設定で例外）。
子テーブルは `(organization_id, 親 id)` の**複合外部キー**で、別テナントの行を参照できない。

| テーブル | 主な制約・インデックス | アプリ（`tac_app`）の権限 |
|---|---|---|
| `organizations` | `max_concurrent_calls > 0` | SELECT のみ（作成・変更は管理者、Phase 3） |
| `contacts` | `phone_e164` は E.164 の CHECK／`(organization_id, phone_e164)` | SELECT・INSERT・UPDATE |
| `campaigns` | `daily_cap`（NULL = 上限なし、ADR-0009 で再検討）・上限系は 0 以上 | SELECT・INSERT・UPDATE |
| `calls` | `calls_org_idempotency_key_uq (organization_id, idempotency_key)`／**`calls_one_active_per_number_uq (organization_id, to_e164) WHERE status IN (REQUESTED, DIALING, RINGING, IN_PROGRESS)`**／`(provider, provider_call_id)` 一意／status・mode の CHECK／`(organization_id, created_at desc)` | SELECT・INSERT・UPDATE（DELETE なし） |
| `outcomes` | `call_id` が主キー（通話ごとに1件） | SELECT・INSERT（書き直さない） |
| `follow_ups` | kind・status の CHECK／`(organization_id, due_at) WHERE status = 'OPEN'` | SELECT・INSERT・UPDATE |
| `suppression_entries` | `(organization_id, phone_e164) WHERE lifted_at IS NULL` で一意／E.164 の CHECK | **SELECT・INSERT のみ**（解除・削除は不可。Phase 5 で理由つきの専用経路） |
| `consents` | scope の CHECK／付与〜撤回の間だけ有効 | SELECT・INSERT・UPDATE |
| `audit_logs` | identity の主キー／`(organization_id, at desc)` | **SELECT・INSERT のみ（追記専用）** |
| `system_controls` | 1行だけ（`id = true`）。全発信停止のフラグ | SELECT のみ（行が無ければ「停止中」と扱う = fail closed） |
| `schema_migrations` | 適用した版とファイルの SHA-256 | — |

## RLS（`0002_tenant_isolation.sql`）
- すべてのテナントのテーブルで `ENABLE` ＋ `FORCE ROW LEVEL SECURITY`、ポリシー `tenant_isolation`：
  `USING / WITH CHECK (organization_id = app_current_org())`。`app_current_org()` は `nullif(current_setting('app.org_id', true), '')::uuid`
- アプリはトランザクションごとに `SET LOCAL ROLE tac_app` と `set_config('app.org_id', 組織, true)` を行う（`TenantScope`、ADR-0012）。
  未設定なら NULL になり、1行も見えず、挿入もできない（fail closed）
- 外部キーの検査は PostgreSQL の仕様で RLS を通らないが、複合外部キーで組織の一致を強制している

## 組織ロック
`UnitOfWork.runExclusive(organizationId, …)` = `pg_advisory_xact_lock(hashtextextended('tac:org:' || 組織, 0))`。
`CreateCallUseCase` の「件数の集計 → 判定 → REQUESTED で保存」を直列化する。電話プロバイダの呼び出しはロックの外。

## マイグレーション
- `migrate(db)`（`packages/db/src/migrate.ts`）。1ファイル = 1トランザクション、`--> statement-breakpoint` の行で文を分ける
- 適用済みファイルの改変（SHA-256 の不一致）・このビルドが知らない版がある DB では失敗する。複数プロセスの同時実行は advisory lock で直列
- 失敗したマイグレーションはロールバックされ、記録も残らない
- **テーブルの所有者で実行する**（`tac_app` では実行しない）。スキーマを変える変更には、同じ変更でマイグレーションを追加する。適用済みのファイルは書き換えない

## 運用（本番前に必要。現在は UNKNOWN）
- `tac_app` は NOLOGIN。本番の接続ユーザー（例 `tac_api`）を作り `GRANT tac_app TO tac_api` する。マイグレーション用のユーザーとは分ける
- マネージド PostgreSQL で `CREATE ROLE` の権限が無い場合は、`tac_app` を事前に作っておく（0002 は存在すれば作らない）
- `FORCE ROW LEVEL SECURITY` のため、スーパーユーザーでない所有者がデータ移行するときは `app.org_id` を設定する
- 全発信停止の切り替えは現在 SQL（`update system_controls set outbound_stopped = true`）。管理画面・API は Phase 3 以降
- バックアップ・保存期間・PITR は `DEPLOYMENT.md`（Phase 18）で決める（UNKNOWN）

## テスト
| 種類 | 実行 | 内容 |
|---|---|---|
| 結合（PGlite） | `pnpm test`・`pnpm test:critical` | `packages/db/test/*`：マイグレーション・RLS・権限・一意制約・ユースケース・再起動後の抑止・ロールバック |
| 並行性（実 PostgreSQL） | `pnpm test:postgres`（`TEST_DATABASE_URL` 必須、CI の postgres:16） | `packages/db/test-postgres/*`：組織ロックで上限を守る・同時の二重発信なし・プールでの文脈の漏れなし |
