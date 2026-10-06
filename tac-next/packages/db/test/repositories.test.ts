import {
  ActiveCallExistsError,
  DuplicateIdempotencyKeyError,
  DuplicateOutcomeError,
} from "@tac/application";
import { sql } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createPgDeps, type Database, TenantScope } from "../src/index.js";
import { callRecord, freshDatabase, jst, OPERATOR, phone, seedTenant } from "./support.js";

/*
 * リポジトリの契約（ports.ts）を PostgreSQL の制約で満たしていることを確かめる。
 * INV-3（1つの発信要求は1件の外部発信）の DB 層：冪等キーの一意制約と、回線上の通話は番号ごとに1件。
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

describe("通話（calls）", () => {
  it("保存した通話をそのまま読み戻せる", async () => {
    const t = await seedTenant(database);
    const deps = createPgDeps(scope);
    const call = callRecord(t.org, t.contactIds[0] ?? "", t.campaignId);
    await deps.calls.insert(call);
    expect(await deps.calls.get(t.org, call.id)).toEqual(call);
    expect(await deps.calls.findByIdempotencyKey(t.org, call.idempotencyKey)).toEqual(call);
    const updated = {
      ...call,
      status: "DIALING" as const,
      provider: "mock",
      providerCallId: "PC-1",
    };
    await deps.calls.update(updated);
    expect(await deps.calls.get(t.org, call.id)).toEqual(updated);
  });

  it("同じ組織・同じ冪等キーは DuplicateIdempotencyKeyError", async () => {
    const t = await seedTenant(database);
    const deps = createPgDeps(scope);
    const first = callRecord(t.org, t.contactIds[0] ?? "", t.campaignId, { status: "ENDED" });
    await deps.calls.insert(first);
    await expect(
      deps.calls.insert(
        callRecord(t.org, t.contactIds[1] ?? "", t.campaignId, {
          idempotencyKey: first.idempotencyKey,
          to: phone("090-0000-0002"),
        }),
      ),
    ).rejects.toBeInstanceOf(DuplicateIdempotencyKeyError);
  });

  it("別の組織なら同じ冪等キーを使える", async () => {
    const t1 = await seedTenant(database);
    const t2 = await seedTenant(database);
    const deps = createPgDeps(scope);
    await deps.calls.insert(
      callRecord(t1.org, t1.contactIds[0] ?? "", t1.campaignId, { idempotencyKey: "same" }),
    );
    await expect(
      deps.calls.insert(
        callRecord(t2.org, t2.contactIds[0] ?? "", t2.campaignId, { idempotencyKey: "same" }),
      ),
    ).resolves.toBeUndefined();
  });

  it.each(["REQUESTED", "DIALING", "RINGING", "IN_PROGRESS"] as const)(
    "同じ番号に %s の通話があれば ActiveCallExistsError",
    async (status) => {
      const t = await seedTenant(database);
      const deps = createPgDeps(scope);
      await deps.calls.insert(callRecord(t.org, t.contactIds[0] ?? "", t.campaignId, { status }));
      await expect(
        deps.calls.insert(callRecord(t.org, t.contactIds[0] ?? "", t.campaignId)),
      ).rejects.toBeInstanceOf(ActiveCallExistsError);
    },
  );

  it.each(["ENDED", "FAILED", "NO_ANSWER", "BUSY", "CANCELED"] as const)(
    "前の通話が %s なら同じ番号へ新しい通話を作れる",
    async (status) => {
      const t = await seedTenant(database);
      const deps = createPgDeps(scope);
      await deps.calls.insert(callRecord(t.org, t.contactIds[0] ?? "", t.campaignId, { status }));
      await expect(
        deps.calls.insert(callRecord(t.org, t.contactIds[0] ?? "", t.campaignId)),
      ).resolves.toBeUndefined();
    },
  );

  it("冪等キーの重複と回線上の重複が同時に起きたら、冪等キーの重複を優先する（契約の順序）", async () => {
    const t = await seedTenant(database);
    const deps = createPgDeps(scope);
    const first = callRecord(t.org, t.contactIds[0] ?? "", t.campaignId);
    await deps.calls.insert(first);
    await expect(
      deps.calls.insert(
        callRecord(t.org, t.contactIds[0] ?? "", t.campaignId, {
          idempotencyKey: first.idempotencyKey,
        }),
      ),
    ).rejects.toBeInstanceOf(DuplicateIdempotencyKeyError);
  });

  it("件数の集計：CANCELED を除く・期間で絞る・回線上だけを数える", async () => {
    const t = await seedTenant(database, {
      phones: ["090-0000-0001", "090-0000-0002", "090-0000-0003"],
    });
    const deps = createPgDeps(scope);
    const [c1 = "", c2 = "", c3 = ""] = t.contactIds;
    await deps.calls.insert(
      callRecord(t.org, c1, t.campaignId, {
        status: "ENDED",
        createdAt: jst("2026-10-04T09:00:00"),
      }),
    );
    await deps.calls.insert(
      callRecord(t.org, c1, t.campaignId, {
        status: "CANCELED",
        createdAt: jst("2026-10-05T09:00:00"),
      }),
    );
    await deps.calls.insert(
      callRecord(t.org, c2, t.campaignId, {
        to: phone("090-0000-0002"),
        status: "IN_PROGRESS",
        createdAt: jst("2026-10-05T09:30:00"),
      }),
    );
    await deps.calls.insert(
      callRecord(t.org, c3, t.campaignId, {
        to: phone("090-0000-0003"),
        status: "RINGING",
        createdAt: jst("2026-10-05T09:40:00"),
      }),
    );
    const since = jst("2026-10-05T00:00:00");
    expect(await deps.calls.countDialedSince(t.org, since)).toBe(2);
    expect(await deps.calls.countToNumberSince(t.org, phone("090-0000-0001"), since)).toBe(0);
    expect(await deps.calls.countToNumberSince(t.org, phone("090-0000-0002"), since)).toBe(1);
    expect(await deps.calls.countForContact(t.org, c1)).toBe(2);
    expect(await deps.calls.countActive(t.org)).toBe(2);
  });
});

describe("結果（outcomes）", () => {
  it("同じ通話の結果は 1 件だけ（DuplicateOutcomeError）", async () => {
    const t = await seedTenant(database);
    const deps = createPgDeps(scope);
    const call = callRecord(t.org, t.contactIds[0] ?? "", t.campaignId, { status: "ENDED" });
    await deps.calls.insert(call);
    const outcome = {
      callId: call.id,
      organizationId: t.org,
      code: "INTERESTED" as const,
      recordedBy: OPERATOR,
      recordedAt: jst("2026-10-05T10:05:00"),
    };
    await deps.outcomes.insert(outcome);
    expect(await deps.outcomes.get(t.org, call.id)).toEqual(outcome);
    await expect(deps.outcomes.insert({ ...outcome, code: "WON" })).rejects.toBeInstanceOf(
      DuplicateOutcomeError,
    );
  });
});

describe("抑止（suppression_entries）", () => {
  it("登録した番号は canContact = false。二重登録しても 1 件", async () => {
    const t = await seedTenant(database);
    const deps = createPgDeps(scope);
    const entry = {
      organizationId: t.org,
      phone: phone("090-0000-0001"),
      reason: "DO_NOT_CALL",
      source: "outcome",
      actorId: OPERATOR,
    };
    expect(await deps.suppression.canContact(t.org, entry.phone)).toBe(true);
    await deps.suppression.add(entry);
    await deps.suppression.add(entry);
    expect(await deps.suppression.canContact(t.org, entry.phone)).toBe(false);
    const rows = await database.db.execute<{ n: number }>(
      sql`select count(*)::int as n from suppression_entries where organization_id = ${t.org}`,
    );
    expect(rows.rows[0]?.n).toBe(1);
  });

  it("E.164 でない番号は保存できない（表記ゆれで抑止をすり抜けない）", async () => {
    const t = await seedTenant(database);
    const deps = createPgDeps(scope);
    await expect(
      deps.suppression.add({
        organizationId: t.org,
        phone: "090-0000-0001" as never,
        reason: "DO_NOT_CALL",
        source: "test",
        actorId: OPERATOR,
      }),
    ).rejects.toThrow();
  });
});

describe("フォローアップ・同意・全発信停止", () => {
  it("期限の来た OPEN だけを期限順に返し、取り消しは OPEN だけに効く", async () => {
    const t = await seedTenant(database);
    const deps = createPgDeps(scope);
    const contactId = t.contactIds[0] ?? "";
    const call = callRecord(t.org, contactId, t.campaignId, { status: "ENDED" });
    await deps.calls.insert(call);
    const base = {
      organizationId: t.org,
      contactId,
      kind: "CALLBACK" as const,
      sourceCallId: call.id,
    };
    const due1 = {
      ...base,
      id: crypto.randomUUID(),
      dueAt: jst("2026-10-05T09:00:00"),
      status: "OPEN" as const,
    };
    const due0 = {
      ...base,
      id: crypto.randomUUID(),
      dueAt: jst("2026-10-05T08:00:00"),
      status: "OPEN" as const,
    };
    const later = {
      ...base,
      id: crypto.randomUUID(),
      dueAt: jst("2026-10-06T09:00:00"),
      status: "OPEN" as const,
    };
    for (const f of [due1, due0, later]) await deps.followUps.insert(f);
    const now = jst("2026-10-05T10:00:00");
    expect((await deps.followUps.listOpenDue(t.org, now)).map((f) => f.id)).toEqual([
      due0.id,
      due1.id,
    ]);
    expect(await deps.followUps.cancelOpenForContact(t.org, contactId)).toBe(3);
    expect(await deps.followUps.listOpenDue(t.org, now)).toEqual([]);
    expect(await deps.followUps.cancelOpenForContact(t.org, contactId)).toBe(0);
  });

  it("同意は付与〜撤回の間だけ有効", async () => {
    const t = await seedTenant(database);
    const deps = createPgDeps(scope);
    const contactId = t.contactIds[0] ?? "";
    await database.db.execute(sql`
      insert into consents (organization_id, contact_id, scope, legal_basis, source, granted_at, revoked_at)
      values (${t.org}, ${contactId}, 'AI_VOICE_OUTBOUND', 'CONSENT', 'test',
        ${jst("2026-10-01T00:00:00")}, ${jst("2026-10-10T00:00:00")})`);
    const has = (at: Date) =>
      deps.consents.hasValidConsent(t.org, contactId, "AI_VOICE_OUTBOUND", at);
    expect(await has(jst("2026-09-30T00:00:00"))).toBe(false);
    expect(await has(jst("2026-10-05T00:00:00"))).toBe(true);
    expect(await has(jst("2026-10-10T00:00:00"))).toBe(false);
  });

  it("全発信停止のフラグを読む（既定は停止していない）", async () => {
    const deps = createPgDeps(scope);
    expect(await deps.safety.isOutboundStopped()).toBe(false);
    await database.db.execute(sql`update system_controls set outbound_stopped = true`);
    expect(await deps.safety.isOutboundStopped()).toBe(true);
    await database.db.execute(sql`update system_controls set outbound_stopped = false`);
  });

  it("全発信停止の行が無ければ「止まっている」と答える（fail closed）", async () => {
    const deps = createPgDeps(scope);
    await database.db.execute(sql`delete from system_controls`);
    try {
      expect(await deps.safety.isOutboundStopped()).toBe(true);
    } finally {
      await database.db.execute(
        sql`insert into system_controls (id, outbound_stopped) values (true, false)`,
      );
    }
  });
});
