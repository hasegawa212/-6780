import { PGlite } from "@electric-sql/pglite";
import type { Assume } from "drizzle-orm";
import { drizzle as drizzleNodePg } from "drizzle-orm/node-postgres";
import type { PgDatabase, PgQueryResultHKT } from "drizzle-orm/pg-core";
import { drizzle as drizzlePglite } from "drizzle-orm/pglite";
import pg from "pg";

/**
 * `execute` の戻り値を `{ rows }` にそろえた型。PGlite（Results）と node-postgres（QueryResult）は
 * どちらも `rows: T[]` を持つので、ドライバの違いをこの型の境界で吸収する。
 */
export interface RowsQueryResultHKT extends PgQueryResultHKT {
  type: RowsResult<Assume<this["row"], Record<string, unknown>>>;
}

export interface RowsResult<T> {
  rows: T[];
}

export type Db = PgDatabase<RowsQueryResultHKT>;
export type Tx = Parameters<Parameters<Db["transaction"]>[0]>[0];

export interface Database {
  readonly db: Db;
  close(): Promise<void>;
}

/** テスト・ローカル用の PGlite（本物の PostgreSQL を WASM で動かす）。dataDir を省略するとメモリ上 */
export async function createPgliteDatabase(dataDir?: string): Promise<Database> {
  const client = await PGlite.create(dataDir === undefined ? {} : { dataDir });
  return {
    db: drizzlePglite({ client }) as unknown as Db,
    close: () => client.close(),
  };
}

/** PostgreSQL（node-postgres の接続プール）。接続文字列はログに出さない */
export function createNodePostgresDatabase(
  connectionString: string,
  maxConnections = 10,
): Database {
  const pool = new pg.Pool({ connectionString, max: maxConnections });
  return {
    db: drizzleNodePg({ client: pool }) as unknown as Db,
    close: () => pool.end(),
  };
}
