import { randomUUID } from "node:crypto";
import {
  ApplyProviderEventUseCase,
  type Deps,
  type OrganizationId,
  type Role,
  type UserId,
} from "@tac/application";
import {
  FixedClock,
  InMemoryBudget,
  InMemoryEvents,
  RecordingTelephony,
} from "@tac/application/testing";
import { sql } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  createPgAuthStores,
  createPgDeps,
  type Database,
  TenantScope,
  UuidIds,
} from "../src/index.js";
import {
  callRecord,
  expectPermissionDenied,
  freshDatabase,
  jst,
  seedTenant,
  sqlState,
} from "./support.js";

/*
 * Phase 3・7 の DB 層：
 * - 認証の表（users・memberships・sessions）と Webhook の受信箱は、アプリのロールから直接読めない
 * - SECURITY DEFINER 関数は目的の行だけを返す
 * - 通話の状態は compare-and-set で更新し、Webhook の処理は状態の変更ごとロールバックされる
 */

let database: Database;
let scope: TenantScope;

beforeAll(async () => {
  database = await freshDatabase();
  scope = new TenantScope(database.db);
}, 60_000);

afterAll(async () => {
  await database.close();
});

async function seedUser(
  org: OrganizationId,
  opts: { role?: Role; email?: string; disabled?: boolean } = {},
) {
  const id = randomUUID() as UserId;
  const email = opts.email ?? `user-${id}@example.test`;
  await database.db.execute(sql`
    insert into users (id, email, display_name, password_hash, disabled_at)
    values (${id}, ${email}, '佐藤', 'scrypt$dummy', ${opts.disabled ? new Date() : null})`);
  await database.db.execute(sql`
    insert into memberships (organization_id, user_id, role) values (${org}, ${id}, ${opts.role ?? "OPERATOR"})`);
  return { id, email };
}

describe("アプリのロールは認証・受信箱の表を直接読めない", () => {
  it.each(["users", "memberships", "sessions", "webhook_events"])("%s", async (table) => {
    await expectPermissionDenied(
      scope.withTenant(undefined, (tx) => tx.execute(sql`select * from ${sql.identifier(table)}`)),
    );
  });
});

describe("認証のストア（SECURITY DEFINER 関数）", () => {
  it("ログインの候補：所属つきで返す。無効なユーザーは返さない", async () => {
    const t = await seedTenant(database);
    const u = await seedUser(t.org, { role: "MANAGER" });
    const off = await seedUser(t.org, { disabled: true });
    const { directory } = createPgAuthStores(scope);
    expect(await directory.findForLogin(u.email)).toMatchObject({
      userId: u.id,
      passwordHash: "scrypt$dummy",
      memberships: [{ organizationId: t.org, role: "MANAGER" }],
    });
    expect(await directory.findForLogin(off.email)).toBeUndefined();
    expect(await directory.findForLogin("nobody@example.test")).toBeUndefined();
  });

  it("セッション：作成・解決・期限切れ・取り消し", async () => {
    const t = await seedTenant(database);
    const u = await seedUser(t.org);
    const { sessions } = createPgAuthStores(scope);
    const now = jst("2026-10-05T10:00:00");
    const expiresAt = jst("2026-10-05T22:00:00");
    await sessions.create({
      idHash: "hash-1",
      userId: u.id,
      organizationId: t.org,
      csrfHash: "csrf-1",
      createdAt: now,
      expiresAt,
    });
    expect(await sessions.resolve("hash-1", now)).toEqual({
      userId: u.id,
      organizationId: t.org,
      role: "OPERATOR",
      displayName: "佐藤",
      csrfHash: "csrf-1",
      expiresAt,
    });
    expect(await sessions.resolve("hash-1", expiresAt)).toBeUndefined();
    expect(await sessions.resolve("other", now)).toBeUndefined();
    await sessions.revoke("hash-1", now);
    expect(await sessions.resolve("hash-1", now)).toBeUndefined();
  });

  it("所属していない組織のセッションは作れない", async () => {
    const t = await seedTenant(database);
    const other = await seedTenant(database);
    const u = await seedUser(t.org);
    const { sessions } = createPgAuthStores(scope);
    const error = await sessions
      .create({
        idHash: "hash-x",
        userId: u.id,
        organizationId: other.org,
        csrfHash: "c",
        createdAt: new Date(),
        expiresAt: new Date(Date.now() + 1000),
      })
      .then(
        () => undefined,
        (e: unknown) => e,
      );
    expect(sqlState(error)).toBe("42501");
  });

  it("ロールの変更・所属の削除・ユーザーの無効化は、次の解決から効く", async () => {
    const t = await seedTenant(database);
    const u = await seedUser(t.org);
    const { sessions } = createPgAuthStores(scope);
    const now = new Date();
    const create = (idHash: string) =>
      sessions.create({
        idHash,
        userId: u.id,
        organizationId: t.org,
        csrfHash: "c",
        createdAt: now,
        expiresAt: new Date(now.getTime() + 3_600_000),
      });
    await create("h-role");
    await database.db.execute(sql`update memberships set role = 'VIEWER' where user_id = ${u.id}`);
    expect(await sessions.resolve("h-role", now)).toMatchObject({ role: "VIEWER" });

    await database.db.execute(sql`update users set disabled_at = now() where id = ${u.id}`);
    expect(await sessions.resolve("h-role", now)).toBeUndefined();
    await database.db.execute(sql`update users set disabled_at = null where id = ${u.id}`);

    await database.db.execute(sql`delete from memberships where user_id = ${u.id}`);
    expect(await sessions.resolve("h-role", now)).toBeUndefined();
  });
});

describe("Webhook の受信箱と通話の特定", () => {
  it("同じイベントの処理権は1回だけ。失敗で手放したら取り直せる。処理済みなら取れない", async () => {
    const { providerEvents } = createPgDeps(scope);
    const at = jst("2026-10-05T10:00:00");
    const e = {
      provider: "mock",
      eventId: `evt-${randomUUID()}`,
      receivedAt: at,
      payload: { a: 1 },
    };
    expect(await providerEvents.begin(e)).toBe(true);
    expect(await providerEvents.begin(e)).toBe(false);
    await providerEvents.release(e.provider, e.eventId);
    expect(await providerEvents.begin(e)).toBe(true);
    await providerEvents.complete(e.provider, e.eventId, at);
    expect(await providerEvents.begin(e)).toBe(false);
    const raw = await database.db.execute<{ payload: unknown }>(
      sql`select payload from webhook_events where event_id = ${e.eventId}`,
    );
    expect(raw.rows).toEqual([{ payload: { a: 1 } }]);
  });

  it("処理中のまま 60 秒以上経ったイベントは取り直せる（処理中に落ちたプロセスの分）", async () => {
    const { providerEvents } = createPgDeps(scope);
    const e = { provider: "mock", eventId: `evt-${randomUUID()}`, payload: {} };
    expect(await providerEvents.begin({ ...e, receivedAt: jst("2026-10-05T10:00:00") })).toBe(true);
    expect(await providerEvents.begin({ ...e, receivedAt: jst("2026-10-05T10:00:30") })).toBe(
      false,
    );
    expect(await providerEvents.begin({ ...e, receivedAt: jst("2026-10-05T10:01:01") })).toBe(true);
  });

  it("通話の特定はテナントをまたいで行えるが、プロバイダが違えば返さない", async () => {
    const t = await seedTenant(database);
    const deps = createPgDeps(scope);
    const call = callRecord(t.org, t.contactIds[0] ?? "", t.campaignId, {
      provider: "mock",
      providerCallId: `PC-${randomUUID()}`,
    });
    await deps.calls.insert(call);
    expect(await deps.callLocator.locate("mock", call.providerCallId, undefined)).toEqual({
      organizationId: t.org,
      callId: call.id,
    });
    expect(await deps.callLocator.locate("other", call.providerCallId, undefined)).toBeUndefined();
    expect(await deps.callLocator.locate("mock", undefined, call.id)).toMatchObject({
      callId: call.id,
    });
    expect(await deps.callLocator.locate("other", undefined, call.id)).toBeUndefined();
    expect(await deps.callLocator.locate("mock", undefined, "not-a-uuid")).toBeUndefined();
  });
});

describe("通話の状態の compare-and-set", () => {
  it("期待した状態のときだけ変わる。別テナントからは変えられない。プロバイダの ID は上書きしない", async () => {
    const t = await seedTenant(database);
    const other = await seedTenant(database);
    const deps = createPgDeps(scope);
    const call = callRecord(t.org, t.contactIds[0] ?? "", t.campaignId);
    await deps.calls.insert(call);
    expect(await deps.calls.transitionStatus(t.org, call.id, "DIALING", "RINGING")).toBe(false);
    expect(await deps.calls.transitionStatus(other.org, call.id, "REQUESTED", "DIALING")).toBe(
      false,
    );
    expect(await deps.calls.transitionStatus(t.org, call.id, "REQUESTED", "DIALING")).toBe(true);
    await deps.calls.attachProvider(t.org, call.id, "mock", "PC-first");
    await deps.calls.attachProvider(t.org, call.id, "mock", "PC-second");
    expect(await deps.calls.get(t.org, call.id)).toMatchObject({
      status: "DIALING",
      provider: "mock",
      providerCallId: "PC-first",
    });
  });
});

describe("ApplyProviderEventUseCase × PostgreSQL", () => {
  function deps(): Deps {
    return {
      ...createPgDeps(scope),
      clock: new FixedClock(jst("2026-10-05T10:00:00")),
      ids: new UuidIds(),
      events: new InMemoryEvents(),
      telephony: new RecordingTelephony(),
      budget: new InMemoryBudget(),
      features: { outboundCalls: true, aiVoice: true },
    };
  }

  it("イベントの記録に失敗したら、状態の変更もロールバックされ、再送で反映される", async () => {
    const t = await seedTenant(database);
    const d = deps();
    const call = callRecord(t.org, t.contactIds[0] ?? "", t.campaignId, {
      status: "DIALING",
      provider: "mock",
      providerCallId: `PC-${randomUUID()}`,
    });
    await d.calls.insert(call);
    const uc = new ApplyProviderEventUseCase(d);
    const cmd = {
      provider: "mock",
      eventId: `evt-${randomUUID()}`,
      providerCallId: call.providerCallId,
      callId: undefined,
      status: "RINGING" as const,
      occurredAt: jst("2026-10-05T10:00:01"),
      payload: { status: "ringing" },
    };
    const append = d.callEvents.append.bind(d.callEvents);
    d.callEvents.append = async () => {
      throw new Error("injected failure");
    };
    await expect(uc.execute(cmd)).rejects.toThrow("injected failure");
    expect((await d.calls.get(t.org, call.id))?.status).toBe("DIALING");

    d.callEvents.append = append;
    expect(await uc.execute(cmd)).toMatchObject({ kind: "APPLIED", status: "RINGING" });
    expect(await uc.execute(cmd)).toEqual({ kind: "DUPLICATE" });
    const events = await database.db.execute<{ status: string; applied: boolean }>(
      sql`select status, applied from call_events where call_id = ${call.id}`,
    );
    expect(events.rows).toEqual([{ status: "RINGING", applied: true }]);
  });
});
