import { randomUUID } from "node:crypto";
import { type Deps, ReconcileUncertainCallsUseCase } from "@tac/application";
import {
  FixedClock,
  InMemoryBudget,
  InMemoryEvents,
  RecordingTelephony,
} from "@tac/application/testing";
import { sql } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  createPgDeps,
  type Database,
  PgUncertainCallFinder,
  TenantScope,
  UuidIds,
} from "../src/index.js";
import { callRecord, freshDatabase, phone, seedTenant } from "./support.js";

/*
 * 確定しない発信の照合（ADR-0016）の DB 層：
 * - list_uncertain_calls（0006）は全組織から REQUESTED・プロバイダの ID なし・古いものだけを、古い順に ID だけ返す
 * - 照合の結果（ID の付与・FAILED）が PostgreSQL に残る
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

const at = (iso: string) => new Date(iso);

describe("list_uncertain_calls（0006）", () => {
  it("全組織から、REQUESTED でプロバイダの ID が無い古い通話だけを古い順に返す", async () => {
    const a = await seedTenant(database);
    const b = await seedTenant(database);
    const deps = createPgDeps(scope);
    const mk = (org: typeof a, n: number, over: Parameters<typeof callRecord>[3]) =>
      callRecord(org.org, org.contactIds[0] ?? "", org.campaignId, {
        to: phone(`090-1111-${String(n).padStart(4, "0")}`),
        ...over,
      });
    const oldA = mk(a, 1, { createdAt: at("2026-10-07T01:00:00Z") });
    const oldB = mk(b, 2, { createdAt: at("2026-10-07T01:01:00Z") });
    const withId = mk(a, 3, {
      createdAt: at("2026-10-07T00:59:00Z"),
      provider: "recording",
      providerCallId: `PC-${randomUUID()}`,
    });
    const failed = mk(a, 4, { createdAt: at("2026-10-07T00:58:00Z"), status: "FAILED" });
    const fresh = mk(a, 5, { createdAt: at("2026-10-07T01:30:00Z") });
    for (const c of [oldA, oldB, withId, failed, fresh]) await deps.calls.insert(c);

    const found = await new PgUncertainCallFinder(scope).list(at("2026-10-07T01:10:00Z"), 100);
    const ids = found.map((f) => f.callId);
    expect(ids).toContain(oldA.id);
    expect(ids).toContain(oldB.id);
    expect(ids.indexOf(oldA.id)).toBeLessThan(ids.indexOf(oldB.id));
    for (const excluded of [withId, failed, fresh]) expect(ids).not.toContain(excluded.id);
    expect(found.find((f) => f.callId === oldB.id)?.organizationId).toBe(b.org);
    expect(await new PgUncertainCallFinder(scope).list(at("2026-10-07T01:10:00Z"), 1)).toHaveLength(
      1,
    );
  });

  it("アプリのロールは関数を通さずに他の組織の通話を読めない（関数が返すのは ID だけ）", async () => {
    const r = await scope.withTenant(undefined, (tx) =>
      tx.execute(sql`select count(*)::int as n from calls`),
    );
    expect((r.rows[0] as { n: number }).n).toBe(0);
  });
});

describe("ReconcileUncertainCallsUseCase × PostgreSQL", () => {
  function build(now: Date) {
    const telephony = new RecordingTelephony();
    const deps: Deps = {
      ...createPgDeps(scope),
      clock: new FixedClock(now),
      ids: new UuidIds(),
      events: new InMemoryEvents(),
      telephony,
      budget: new InMemoryBudget(),
      features: { outboundCalls: true, aiVoice: false },
    };
    return {
      deps,
      telephony,
      uc: new ReconcileUncertainCallsUseCase(deps, new PgUncertainCallFinder(scope)),
    };
  }

  it("見つかった ID と状態、見つからなかった通話の FAILED が保存され、監査ログが残る", async () => {
    const t = await seedTenant(database);
    const created = at("2026-10-08T01:00:00Z");
    const found = callRecord(t.org, t.contactIds[0] ?? "", t.campaignId, {
      to: phone("090-2222-0001"),
      createdAt: created,
    });
    const missing = callRecord(t.org, t.contactIds[0] ?? "", t.campaignId, {
      to: phone("090-2222-0002"),
      createdAt: created,
    });
    const s = build(at("2026-10-08T01:20:00Z"));
    await s.deps.calls.insert(found);
    await s.deps.calls.insert(missing);
    const sid = `CA-${randomUUID()}`;
    s.telephony.findCalls = async (q) =>
      q.to === found.to
        ? [
            {
              providerCallId: sid,
              status: "IN_PROGRESS",
              createdAt: new Date(created.getTime() + 2000),
            },
          ]
        : [];

    const r = await s.uc.execute({ limit: 500 });
    expect(r.results).toEqual(
      expect.arrayContaining([
        { callId: found.id, kind: "MATCHED" },
        { callId: missing.id, kind: "NOT_PLACED" },
      ]),
    );
    expect(await s.deps.calls.get(t.org, found.id)).toMatchObject({
      status: "IN_PROGRESS",
      provider: "recording",
      providerCallId: sid,
    });
    expect((await s.deps.calls.get(t.org, missing.id))?.status).toBe("FAILED");
    const audits = await database.db.execute<{ actor_id: string; n: number }>(
      sql`select actor_id, count(*)::int as n from audit_logs
          where organization_id = ${t.org} and action = 'call.reconciled' group by actor_id`,
    );
    expect(audits.rows).toEqual([{ actor_id: "system:reconciler", n: 2 }]);
  });
});
