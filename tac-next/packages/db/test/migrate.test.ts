import { sql } from "drizzle-orm";
import { getTableConfig } from "drizzle-orm/pg-core";
import { describe, expect, it } from "vitest";
import {
  createPgliteDatabase,
  loadMigrations,
  MigrationChecksumError,
  migrate,
  pendingMigrations,
  schema,
} from "../src/index.js";
import { newOrgId } from "./support.js";

const appliedVersions = async (db: Awaited<ReturnType<typeof createPgliteDatabase>>["db"]) =>
  (
    await db.execute<{ version: string }>(
      sql`select version from schema_migrations order by version`,
    )
  ).rows.map((r) => r.version);

describe("マイグレーション（DATABASE.md: 空の DB と既存 DB からの更新の両方）", () => {
  it("空の DB に全マイグレーションを順に適用し、記録する", async () => {
    const { db, close } = await createPgliteDatabase();
    const applied = await migrate(db);
    expect(applied).toEqual(loadMigrations().map((m) => m.version));
    expect(await appliedVersions(db)).toEqual(applied);
    await close();
  });

  it("未適用のマイグレーションを返す（本番の起動時に、古いスキーマのまま動かないための確認）", async () => {
    const { db, close } = await createPgliteDatabase();
    const all = loadMigrations().map((m) => m.version);
    expect(await pendingMigrations(db)).toEqual(all);
    await migrate(db, { to: all[0] ?? "" });
    expect(await pendingMigrations(db)).toEqual(all.slice(1));
    await migrate(db);
    expect(await pendingMigrations(db)).toEqual([]);
    await close();
  });

  it("2 回目の実行では何も適用しない（冪等）", async () => {
    const { db, close } = await createPgliteDatabase();
    await migrate(db);
    expect(await migrate(db)).toEqual([]);
    await close();
  });

  it("途中の版の既存 DB を最新へ更新しても、既存のデータは残り、RLS が有効になる", async () => {
    const { db, close } = await createPgliteDatabase();
    const [first] = loadMigrations();
    if (!first) throw new Error("no migrations");
    await migrate(db, { to: first.version });
    expect(await appliedVersions(db)).toEqual([first.version]);

    const org = newOrgId();
    await db.execute(sql`
      insert into organizations (id, company_name, max_concurrent_calls)
      values (${org}, '既存データ株式会社', 3)`);

    await migrate(db);
    const kept = await db.execute<{ company_name: string }>(
      sql`select company_name from organizations where id = ${org}`,
    );
    expect(kept.rows).toEqual([{ company_name: "既存データ株式会社" }]);
    const rls = await db.execute<{ relrowsecurity: boolean }>(
      sql`select relrowsecurity from pg_class where relname = 'organizations'`,
    );
    expect(rls.rows).toEqual([{ relrowsecurity: true }]);
    await close();
  });

  it("適用済みのマイグレーションの中身が変わっていたら、黙って進めず失敗する", async () => {
    const { db, close } = await createPgliteDatabase();
    await migrate(db);
    await db.execute(sql`update schema_migrations set checksum = 'tampered'`);
    await expect(migrate(db)).rejects.toBeInstanceOf(MigrationChecksumError);
    await close();
  });

  it("失敗したマイグレーションはロールバックされ、記録も残らない", async () => {
    const { db, close } = await createPgliteDatabase();
    const broken = [
      ...loadMigrations(),
      {
        version: "9999_broken",
        checksum: "x",
        statements: ["create table t1 (id int)", "select 1/0"],
      },
    ];
    await expect(migrate(db, { migrations: broken })).rejects.toThrow();
    expect(await appliedVersions(db)).not.toContain("9999_broken");
    const t1 = await db.execute(sql`select to_regclass('public.t1') as t`);
    expect(t1.rows).toEqual([{ t: null }]);
    await close();
  });

  it("Drizzle のスキーマ定義と、マイグレーションで作った列が一致する（定義のずれを検出）", async () => {
    const { db, close } = await createPgliteDatabase();
    await migrate(db);
    for (const table of Object.values(schema)) {
      const config = getTableConfig(table);
      const columns = await db.execute<{ column_name: string }>(sql`
        select column_name from information_schema.columns
        where table_schema = 'public' and table_name = ${config.name}`);
      const actual = columns.rows.map((r) => r.column_name).sort();
      const declared = config.columns.map((c) => c.name).sort();
      expect({ table: config.name, columns: actual }).toEqual({
        table: config.name,
        columns: declared,
      });
    }
    await close();
  });
});
