import { randomUUID } from "node:crypto";
import type { Deps, OrganizationId, Role, UserId } from "@tac/application";
import { FixedClock, InMemoryBudget, InMemoryEvents } from "@tac/application/testing";
import {
  createPgAuthStores,
  createPgDeps,
  createPgliteDatabase,
  type Database,
  migrate,
  TenantScope,
  UuidIds,
} from "@tac/db";
import { MockTelephonyProvider, type SimulatedEvent, signMockWebhook } from "@tac/telephony";
import { sql } from "drizzle-orm";
import { type ApiOptions, createApp, HmacTokens, ScryptPasswordHasher } from "../src/index.js";

/** 架空のデータだけを使う（実在の人物・番号は使わない）。本物の電話はかけない（MockTelephonyProvider） */

export const PASSWORD = "correct horse battery staple";
export const WEBHOOK_SECRET = "test-webhook-secret-at-least-32-chars";
export const jst = (local: string) => new Date(`${local}+09:00`);

// テストは高速化のため scrypt のコストだけ下げる（本番の既定値は security.test.ts で確かめる）
const hasher = new ScryptPasswordHasher({ logN: 10 });

export async function freshDatabase(): Promise<Database> {
  const database = await createPgliteDatabase();
  await migrate(database.db);
  return database;
}

export async function seedTenant(
  database: Database,
  opts: { phones?: readonly string[]; dailyCap?: number } = {},
) {
  const org = randomUUID() as OrganizationId;
  const campaignId = randomUUID();
  const { db } = database;
  await db.execute(sql`
    insert into organizations (id, company_name, max_concurrent_calls)
    values (${org}, '株式会社サンプル不動産', 5)`);
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
      ${sql.raw("array['81']")}, ${opts.dailyCap ?? 100}, 1, 3)`);
  const contactIds: string[] = [];
  for (const [i, e164] of (opts.phones ?? ["+819000000001", "+819000000002"]).entries()) {
    const id = randomUUID();
    contactIds.push(id);
    await db.execute(sql`
      insert into contacts (id, organization_id, display_name, phone_e164, time_zone)
      values (${id}, ${org}, ${`架空 ${i + 1}`}, ${e164}, 'Asia/Tokyo')`);
  }
  return { org, campaignId, contactIds };
}

export async function seedUser(
  database: Database,
  org: OrganizationId,
  role: Role = "OPERATOR",
  extraOrgs: readonly { org: OrganizationId; role: Role }[] = [],
) {
  const id = randomUUID() as UserId;
  const email = `${role.toLowerCase()}-${id}@example.test`;
  await database.db.execute(sql`
    insert into users (id, email, display_name, password_hash)
    values (${id}, ${email}, '佐藤', ${await hasher.hash(PASSWORD)})`);
  for (const m of [{ org, role }, ...extraOrgs]) {
    await database.db.execute(sql`
      insert into memberships (organization_id, user_id, role) values (${m.org}, ${id}, ${m.role})`);
  }
  return { id, email };
}

export function buildApi(
  database: Database,
  opts: { features?: Deps["features"]; api?: Partial<ApiOptions> } = {},
) {
  const scope = new TenantScope(database.db);
  const clock = new FixedClock(jst("2026-10-05T10:00:00")); // 月曜 10:00
  const telephony = new MockTelephonyProvider({ idPrefix: randomUUID(), now: () => clock.now() });
  const deps: Deps = {
    ...createPgDeps(scope),
    clock,
    ids: new UuidIds(),
    events: new InMemoryEvents(),
    telephony,
    budget: new InMemoryBudget(),
    features: opts.features ?? { outboundCalls: true, aiVoice: false },
  };
  const app = createApp({
    deps,
    auth: {
      ...createPgAuthStores(scope),
      clock,
      passwords: hasher,
      tokens: new HmacTokens("test-session-secret-at-least-32-characters"),
      audit: deps.audit,
    },
    cookieSecure: false,
    mockWebhooks: { enabled: true, secret: WEBHOOK_SECRET, verifySignatures: true },
    ...opts.api,
  });
  return { app, deps, clock, telephony };
}

type Api = ReturnType<typeof buildApi>;

export interface Session {
  readonly cookie: string;
  readonly csrf: string;
}

export async function login(api: Api, email: string, extra: Record<string, unknown> = {}) {
  const res = await api.app.request("/v1/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password: PASSWORD, ...extra }),
  });
  if (res.status !== 200) throw new Error(`login failed: ${res.status} ${await res.text()}`);
  const body = (await res.json()) as { csrfToken: string };
  const setCookie = res.headers.get("set-cookie") ?? "";
  return { cookie: setCookie.split(";")[0] ?? "", csrf: body.csrfToken } satisfies Session;
}

export function call(
  api: Api,
  method: string,
  path: string,
  opts: {
    session?: Session;
    body?: unknown;
    headers?: Record<string, string>;
    csrf?: boolean;
  } = {},
) {
  const headers: Record<string, string> = { "content-type": "application/json", ...opts.headers };
  if (opts.session) {
    headers.cookie = opts.session.cookie;
    if (opts.csrf !== false) headers["x-csrf-token"] = opts.session.csrf;
  }
  return api.app.request(path, {
    method,
    headers,
    ...(opts.body === undefined ? {} : { body: JSON.stringify(opts.body) }),
  });
}

/** シミュレーターのイベントを、署名つきの Webhook として API に届ける */
export function deliver(
  api: Api,
  event: SimulatedEvent,
  opts: { secret?: string; at?: Date; signature?: string } = {},
) {
  const body = JSON.stringify(event);
  const signature =
    opts.signature ??
    signMockWebhook(opts.secret ?? WEBHOOK_SECRET, body, opts.at ?? api.clock.now());
  return api.app.request("/v1/webhooks/mock", {
    method: "POST",
    headers: { "content-type": "application/json", "x-tac-signature": signature },
    body,
  });
}
