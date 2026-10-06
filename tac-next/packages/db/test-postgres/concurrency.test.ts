import { randomUUID } from "node:crypto";
import { CreateCallUseCase, type Deps, type OrganizationId, type UserId } from "@tac/application";
import {
  FixedClock,
  InMemoryBudget,
  InMemoryEvents,
  RecordingTelephony,
} from "@tac/application/testing";
import { sql } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  createNodePostgresDatabase,
  createPgDeps,
  type Database,
  migrate,
  TenantScope,
  UuidIds,
} from "../src/index.js";

/*
 * 本物の PostgreSQL（複数の接続）での並行性のテスト。PGlite は接続が1本でトランザクションを
 * 直列に実行するため、組織ロック・一意制約が「同時に」効くことはここでしか確かめられない。
 * `pnpm test:postgres`（TEST_DATABASE_URL が必須）。CI では postgres:16 のサービスで実行する。
 * 架空のデータだけを使い、電話は RecordingTelephony（外部へは掛けない）。
 */

const url = process.env.TEST_DATABASE_URL;
if (!url) throw new Error("TEST_DATABASE_URL が未設定です（pnpm test:postgres は実 DB が必須）");

const OPERATOR = "user-op" as UserId;
const POOL_SIZE = 12;
let database: Database;

class UniqueIdTelephony extends RecordingTelephony {
  override async createCall(request: Parameters<RecordingTelephony["createCall"]>[0]) {
    const placed = await super.createCall(request);
    return { ...placed, providerCallId: `PC-${request.callId}` };
  }
}

function depsFor(): Deps & { telephony: RecordingTelephony } {
  return {
    ...createPgDeps(new TenantScope(database.db)),
    clock: new FixedClock(new Date("2026-10-05T10:00:00+09:00")), // 月曜 10:00
    ids: new UuidIds(),
    events: new InMemoryEvents(),
    telephony: new UniqueIdTelephony(),
    budget: new InMemoryBudget(),
    features: { outboundCalls: true, aiVoice: true },
  };
}

async function seed(opts: { contacts: number; dailyCap?: number; maxConcurrentCalls?: number }) {
  const org = randomUUID() as OrganizationId;
  const campaignId = randomUUID();
  const { db } = database;
  await db.execute(sql`
    insert into organizations (id, company_name, max_concurrent_calls)
    values (${org}, '株式会社サンプル不動産', ${opts.maxConcurrentCalls ?? 100})`);
  const window = {
    timeZone: "Asia/Tokyo",
    startMinute: 9 * 60,
    endMinute: 20 * 60,
    weekdays: [1, 2, 3, 4, 5, 6],
    holidays: [],
  };
  await db.execute(sql`
    insert into campaigns (id, organization_id, product, caller_id_e164, calling_window,
      allowed_country_codes, daily_cap, per_number_daily_limit, max_attempts)
    values (${campaignId}, ${org}, '新築マンション', '+81300000000', ${JSON.stringify(window)}::jsonb,
      ${sql.raw("array['81']")}, ${opts.dailyCap ?? 1000}, 10, 3)`);
  const contactIds: string[] = [];
  for (let i = 0; i < opts.contacts; i += 1) {
    const id = randomUUID();
    contactIds.push(id);
    await db.execute(sql`
      insert into contacts (id, organization_id, display_name, phone_e164, time_zone)
      values (${id}, ${org}, ${`架空 ${i}`}, ${`+8190000${String(i).padStart(5, "0")}`}, 'Asia/Tokyo')`);
  }
  return { org, campaignId, contactIds };
}

const command = (org: OrganizationId, contactId: string, campaignId: string, key: string) => ({
  organizationId: org,
  actorId: OPERATOR,
  contactId,
  campaignId,
  idempotencyKey: key,
  mode: "HUMAN_DIALED" as const,
  agentName: "佐藤",
});

beforeAll(async () => {
  database = createNodePostgresDatabase(url, POOL_SIZE);
  await migrate(database.db);
}, 60_000);

afterAll(async () => {
  await database?.close();
});

describe("実 PostgreSQL：組織単位の上限は同時要求でも超えない", () => {
  it.each([1, 2, 3, 4, 5])(
    "1日上限 1 件・別々の相手 10 件を同時に（%i 回目）→ 外部発信は 1 件",
    async () => {
      const t = await seed({ contacts: 10, dailyCap: 1 });
      const deps = depsFor();
      const uc = new CreateCallUseCase(deps);
      const results = await Promise.all(
        t.contactIds.map((c, i) => uc.execute(command(t.org, c, t.campaignId, `k-${i}`))),
      );
      expect(deps.telephony.requests).toHaveLength(1);
      expect(results.filter((r) => r.ok)).toHaveLength(1);
    },
  );

  it("同時通話数の上限 2・別々の相手 10 件を同時に → 回線に乗るのは 2 件", async () => {
    const t = await seed({ contacts: 10, maxConcurrentCalls: 2 });
    const deps = depsFor();
    const uc = new CreateCallUseCase(deps);
    await Promise.all(
      t.contactIds.map((c, i) => uc.execute(command(t.org, c, t.campaignId, `k-${i}`))),
    );
    expect(deps.telephony.requests).toHaveLength(2);
    expect(await deps.calls.countActive(t.org)).toBe(2);
  });
});

describe("実 PostgreSQL：二重発信しない（INV-3）", () => {
  it("同じ冪等キーを 30 並列 → 外部発信は 1 件", async () => {
    const t = await seed({ contacts: 1 });
    const deps = depsFor();
    const uc = new CreateCallUseCase(deps);
    const results = await Promise.all(
      Array.from({ length: 30 }, () =>
        uc.execute(command(t.org, t.contactIds[0] ?? "", t.campaignId, "same")),
      ),
    );
    expect(deps.telephony.requests).toHaveLength(1);
    expect(results.every((r) => r.ok)).toBe(true);
  });

  it("同じ相手に別々の冪等キーで 10 並列 → 外部発信は 1 件", async () => {
    const t = await seed({ contacts: 1 });
    const deps = depsFor();
    const uc = new CreateCallUseCase(deps);
    await Promise.all(
      Array.from({ length: 10 }, (_, i) =>
        uc.execute(command(t.org, t.contactIds[0] ?? "", t.campaignId, `k-${i}`)),
      ),
    );
    expect(deps.telephony.requests).toHaveLength(1);
  });
});

describe("実 PostgreSQL：接続プールでテナントの文脈が漏れない（INV-2）", () => {
  it("A と B の問い合わせを 200 件交互に同時実行しても、それぞれ自分の行だけが見える", async () => {
    const a = await seed({ contacts: 3 });
    const b = await seed({ contacts: 5 });
    const scope = new TenantScope(database.db);
    const countAs = (org: OrganizationId) =>
      scope.withTenant(org, async (tx) => {
        const r = await tx.execute<{ n: number }>(sql`select count(*)::int as n from contacts`);
        return { org, n: r.rows[0]?.n };
      });
    const results = await Promise.all(
      Array.from({ length: 200 }, (_, i) => countAs(i % 2 === 0 ? a.org : b.org)),
    );
    for (const r of results) expect(r.n).toBe(r.org === a.org ? 3 : 5);
    // 文脈なしの問い合わせ（プールの接続が使い回されても）は 1 行も見えない
    const none = await scope.withTenant(undefined, async (tx) => {
      const r = await tx.execute<{ n: number }>(sql`select count(*)::int as n from contacts`);
      return r.rows[0]?.n;
    });
    expect(none).toBe(0);
  });
});
