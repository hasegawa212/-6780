import { randomUUID } from "node:crypto";
import type { CallRecord, OrganizationId, UserId } from "@tac/application";
import { type E164, toE164 } from "@tac/domain";
import { sql } from "drizzle-orm";
import { createPgliteDatabase, type Database, migrate } from "../src/index.js";

/** 架空のデータだけを使う（実在の人物・番号は使わない）。 */

export const OPERATOR = "user-op" as UserId;
export const jst = (local: string) => new Date(`${local}+09:00`);

export const phone = (raw: string): E164 => {
  const r = toE164(raw);
  if (!r.ok) throw new Error(`bad test phone ${raw}`);
  return r.value;
};

export const newOrgId = () => randomUUID() as OrganizationId;

/** マイグレーション済みの PGlite（dataDir を省略するとメモリ上） */
export async function freshDatabase(dataDir?: string): Promise<Database> {
  const database = await createPgliteDatabase(dataDir);
  await migrate(database.db);
  return database;
}

/**
 * テナントの初期データを入れる。組織の作成はアプリのロールに許していないため、
 * 所有者（マイグレーションと同じ権限）で直接書き込む。
 */
export async function seedTenant(
  database: Database,
  opts: {
    org?: OrganizationId;
    companyName?: string;
    maxConcurrentCalls?: number;
    dailyCap?: number | null;
    perNumberDailyLimit?: number;
    phones?: readonly string[];
  } = {},
) {
  const org = opts.org ?? newOrgId();
  const campaignId = randomUUID();
  const contactIds = (opts.phones ?? ["090-0000-0001", "090-0000-0002"]).map(() => randomUUID());
  const { db } = database;
  await db.execute(sql`
    insert into organizations (id, company_name, ai_voice_outbound_enabled, paused, max_concurrent_calls)
    values (${org}, ${opts.companyName ?? "株式会社サンプル不動産"}, false, false, ${opts.maxConcurrentCalls ?? 5})`);
  const window = {
    timeZone: "Asia/Tokyo",
    startMinute: 9 * 60,
    endMinute: 20 * 60,
    weekdays: [1, 2, 3, 4, 5, 6],
    holidays: [],
  };
  await db.execute(sql`
    insert into campaigns (id, organization_id, product, caller_id_e164, calling_window,
      allowed_country_codes, daily_cap, per_number_daily_limit, max_attempts, paused)
    values (${campaignId}, ${org}, '新築マンション', ${phone("03-0000-0000")}, ${JSON.stringify(window)}::jsonb,
      ${sql.raw("array['81']")}, ${opts.dailyCap === undefined ? 100 : opts.dailyCap},
      ${opts.perNumberDailyLimit ?? 1}, 3, false)`);
  const phones = opts.phones ?? ["090-0000-0001", "090-0000-0002"];
  for (const [i, raw] of phones.entries()) {
    await db.execute(sql`
      insert into contacts (id, organization_id, display_name, phone_e164, time_zone)
      values (${contactIds[i]}, ${org}, ${`架空 ${i + 1}`}, ${phone(raw)}, 'Asia/Tokyo')`);
  }
  return { org, campaignId, contactIds };
}

export function callRecord(
  org: OrganizationId,
  contactId: string,
  campaignId: string,
  over: Partial<CallRecord> = {},
): CallRecord {
  return {
    id: randomUUID(),
    organizationId: org,
    contactId,
    campaignId,
    to: phone("090-0000-0001"),
    from: phone("03-0000-0000"),
    mode: "HUMAN_DIALED",
    status: "REQUESTED",
    idempotencyKey: `key-${randomUUID()}`,
    requestFingerprint: `${contactId}|${campaignId}|HUMAN_DIALED`,
    requestedBy: OPERATOR,
    agentName: "佐藤",
    provider: undefined,
    providerCallId: undefined,
    createdAt: jst("2026-10-05T10:00:00"),
    ...over,
  };
}

/** ドライバの例外（Drizzle が cause で包む）から SQLSTATE を取り出す */
export function sqlState(e: unknown): string | undefined {
  for (let cur: unknown = e; cur && typeof cur === "object"; ) {
    if ("code" in cur && typeof cur.code === "string" && /^[0-9A-Z]{5}$/.test(cur.code)) {
      return cur.code;
    }
    cur = "cause" in cur ? cur.cause : undefined;
  }
  return undefined;
}

/** 権限エラー（insufficient_privilege = 42501）で失敗することを確かめる */
export async function expectPermissionDenied(work: Promise<unknown>): Promise<void> {
  const error = await work.then(
    () => undefined,
    (e: unknown) => e ?? new Error("rejected without a reason"),
  );
  if (error === undefined) throw new Error("expected permission denied, but the query succeeded");
  if (sqlState(error) !== "42501") throw error;
}
