# ADR-0012: DB のテナント文脈・組織ロック・マイグレーションの正

- Status: Accepted
- Date: 2026-10-06

## Context
Phase 2 の受け入れ条件（`DATABASE.md`）は、テナント分離をアプリのスコープと RLS の二重にすること、
1日上限・同時通話数の「数えてから保存する」判定を同時要求で超えないこと、抑止が再起動後も残ること。
OBSERVED（`@electric-sql/pglite@0.5.8` のソース）：PGlite は `query()` も `transaction()` も同じ排他を通るため、
トランザクション中に外のクエリを発行すると、そのトランザクションの終了を待つ（同じ処理の中なら自分を待って止まる）。
また PGlite は接続が1本で、トランザクションを並行に実行しない。

## Decision
1. **テナント文脈はトランザクション単位**：すべてのクエリを `SET LOCAL ROLE tac_app` ＋ `set_config('app.org_id', 組織, true)` の
   トランザクションで流す（`packages/db/src/tenant.ts` の `TenantScope`）。どちらもトランザクション終了で戻るので、接続プールで次の利用者に漏れない。
   所有者・スーパーユーザーで接続していても RLS を外さない。`app.org_id` が未設定なら RLS で1行も見えない（fail closed）。
2. **UnitOfWork のトランザクションは AsyncLocalStorage で共有**する。1トランザクション = 1組織。別の組織を混ぜたら `TenantMismatchError`。
3. **組織ロック**：`UnitOfWork.runExclusive(organizationId, work)` をポートに追加。PostgreSQL では
   `pg_advisory_xact_lock(hashtextextended('tac:org:' || 組織, 0))`、インメモリでは組織ごとの Promise の鎖。
   `CreateCallUseCase` は「件数の集計 → 発信判定 → REQUESTED で保存」をこの中で行い、電話プロバイダの呼び出しは外で行う（ロックを長く握らない）。
4. **権限で塞ぐ**：アプリのロール `tac_app` に、監査ログの UPDATE / DELETE、抑止の UPDATE / DELETE、全発信停止の書き込み、組織の書き込みを与えない。
5. **マイグレーションの正は SQL**（`packages/db/migrations/NNNN_*.sql`）。RLS・部分一意インデックス・権限は SQL にしか書けない／書きにくいため。
   Drizzle のスキーマ（`schema.ts`）は型付きクエリ用で、列のずれは `migrate.test.ts` が検出する。適用済みファイルの改変・未知の版は実行を止める。
6. **冪等キーの優先順位**：`calls` の挿入は `ON CONFLICT (organization_id, idempotency_key) DO NOTHING`。PostgreSQL は対象の制約を先に調べるため、
   冪等キーと回線上の重複が同時に起きても `DuplicateIdempotencyKeyError` が優先される（ports.ts の契約の順序）。
7. **並行性の証拠は実 PostgreSQL で取る**：PGlite ではロックの直列化を証明できない（そもそも直列）。
   `packages/db/test-postgres/`（`pnpm test:postgres`、`TEST_DATABASE_URL` 必須）を CI の postgres:16 サービスで実行する。
   PGlite の Critical Suite では「runExclusive の間 advisory lock を握っている」ことを `pg_locks` で確かめ、変異（ロックを外す）を検出する。

## Alternatives
- `SERIALIZABLE` 分離レベルで上限を守る：直列化エラーの再試行が全ユースケースに必要になり、外部発信との組み合わせが難しい（不採用）
- 組織の行を `SELECT … FOR UPDATE`：組織の行の読み取りと競合し、RLS（組織は読み取り専用）とも相性が悪い（不採用）
- drizzle-kit でマイグレーションを生成：RLS・権限・部分一意インデックスの表現と、生成物のレビューが重い。将来 `drizzle-kit check` を検討（保留）
- テナントごとにスキーマ／DB を分ける：運用コストが高い（不採用）

## Consequences
- 本番の接続ユーザーは `tac_app` のメンバーにする必要がある（`DATABASE.md`「運用」）。マイグレーションはテーブルの所有者で実行する
- `FORCE ROW LEVEL SECURITY` のため、スーパーユーザーでない所有者は RLS の対象になる（データ移行は `app.org_id` を設定して行う）
- 抑止の解除・全発信停止の切り替え・組織の作成は、まだアプリから行えない（Phase 3・5 で、理由と監査ログつきの専用経路を作る）
- 同じ組織の発信要求は判定〜保存の間だけ直列になる（電話プロバイダの呼び出しは並行のまま）
