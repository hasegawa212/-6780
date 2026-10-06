import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { sql } from "drizzle-orm";
import type { Db } from "./client.js";

/**
 * マイグレーションの実行。migrations/NNNN_name.sql を版の順に、1ファイル = 1トランザクションで適用する。
 * - 適用済みのファイルが書き換えられていたら失敗する（本番と違うスキーマを黙って作らない）
 * - DB にあってこのビルドにない版があっても失敗する（古いコードで新しい DB を触らない）
 * - 複数のプロセスが同時に実行しても、advisory lock で1つずつ適用する
 * 所有者（テーブルを作れるロール）で実行する。アプリのロール（tac_app）では実行しない。
 */

export interface Migration {
  readonly version: string;
  readonly checksum: string;
  readonly statements: readonly string[];
}

export class MigrationChecksumError extends Error {
  constructor(version: string) {
    super(`applied migration ${version} has been modified`);
    this.name = "MigrationChecksumError";
  }
}

export class UnknownMigrationError extends Error {
  constructor(version: string) {
    super(`database has migration ${version} that this build does not know`);
    this.name = "UnknownMigrationError";
  }
}

const BREAKPOINT = "--> statement-breakpoint";
const MIGRATION_LOCK_KEY = 7_420_001; // 任意の固定値（マイグレーション専用）
const MIGRATIONS_DIR = new URL("../migrations/", import.meta.url);

const isOnlyComments = (chunk: string) =>
  chunk
    .split("\n")
    .map((l) => l.trim())
    .every((l) => l === "" || l.startsWith("--"));

export function loadMigrations(dir: URL = MIGRATIONS_DIR): Migration[] {
  return readdirSync(dir)
    .filter((f) => /^\d{4}_[a-z0-9_]+\.sql$/.test(f))
    .sort()
    .map((file) => {
      const text = readFileSync(new URL(file, dir), "utf8");
      return {
        version: file.replace(/\.sql$/, ""),
        checksum: createHash("sha256").update(text).digest("hex"),
        statements: text
          .split(BREAKPOINT)
          .map((s) => s.trim())
          .filter((s) => !isOnlyComments(s)),
      };
    });
}

/** 未適用のマイグレーションを適用し、適用した版の一覧を返す */
export async function migrate(
  db: Db,
  opts: { to?: string; migrations?: readonly Migration[] } = {},
): Promise<string[]> {
  const migrations = opts.migrations ?? loadMigrations();
  await db.execute(sql`
    create table if not exists schema_migrations (
      version text primary key,
      checksum text not null,
      applied_at timestamptz not null default now()
    )`);

  const known = new Set(migrations.map((m) => m.version));
  const inDb = await db.execute<{ version: string }>(sql`select version from schema_migrations`);
  const unknown = inDb.rows.find((r) => !known.has(r.version));
  if (unknown) throw new UnknownMigrationError(unknown.version);

  const appliedNow: string[] = [];
  for (const m of migrations) {
    if (opts.to !== undefined && m.version > opts.to) break;
    const applied = await db.transaction(async (tx) => {
      await tx.execute(sql`select pg_advisory_xact_lock(${MIGRATION_LOCK_KEY})`);
      const existing = await tx.execute<{ checksum: string }>(
        sql`select checksum from schema_migrations where version = ${m.version}`,
      );
      const row = existing.rows[0];
      if (row) {
        if (row.checksum !== m.checksum) throw new MigrationChecksumError(m.version);
        return false;
      }
      for (const statement of m.statements) await tx.execute(sql.raw(statement));
      await tx.execute(
        sql`insert into schema_migrations (version, checksum) values (${m.version}, ${m.checksum})`,
      );
      return true;
    });
    if (applied) appliedNow.push(m.version);
  }
  return appliedNow;
}

/** 未適用のマイグレーションの版（schema_migrations が無ければすべて） */
export async function pendingMigrations(
  db: Db,
  migrations: readonly Migration[] = loadMigrations(),
): Promise<string[]> {
  const exists = await db.execute<{ t: string | null }>(
    sql`select to_regclass('public.schema_migrations')::text as t`,
  );
  if (!exists.rows[0]?.t) return migrations.map((m) => m.version);
  const applied = await db.execute<{ version: string }>(sql`select version from schema_migrations`);
  const done = new Set(applied.rows.map((r) => r.version));
  return migrations.map((m) => m.version).filter((v) => !done.has(v));
}
