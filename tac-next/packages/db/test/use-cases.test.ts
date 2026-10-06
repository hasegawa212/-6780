import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  CreateCallUseCase,
  type Deps,
  type OrganizationId,
  RecordOutcomeUseCase,
} from "@tac/application";
import {
  FixedClock,
  InMemoryBudget,
  InMemoryEvents,
  RecordingTelephony,
} from "@tac/application/testing";
import { sql } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createPgDeps, type Database, TenantScope, UuidIds } from "../src/index.js";
import { freshDatabase, jst, OPERATOR, phone, seedTenant } from "./support.js";

/*
 * ユースケース（CreateCall / RecordOutcome）を PostgreSQL のリポジトリで動かす結合テスト。
 * INV-1（抑止は再起動の後も残る）・INV-3（二重発信しない）・上限の競合の解消（Phase 2）。
 */

/** 通話ごとに一意な providerCallId を返す（calls の (provider, provider_call_id) 一意制約に合わせる） */
class UniqueIdTelephony extends RecordingTelephony {
  override async createCall(request: Parameters<RecordingTelephony["createCall"]>[0]) {
    const placed = await super.createCall(request);
    return { ...placed, providerCallId: `PC-${request.callId}` };
  }
}

function depsFor(database: Database): Deps & { telephony: RecordingTelephony; clock: FixedClock } {
  return {
    ...createPgDeps(new TenantScope(database.db)),
    clock: new FixedClock(jst("2026-10-05T10:00:00")), // 月曜 10:00
    ids: new UuidIds(),
    events: new InMemoryEvents(),
    telephony: new UniqueIdTelephony(),
    budget: new InMemoryBudget(),
    features: { outboundCalls: true, aiVoice: true },
  };
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

let database: Database;

beforeAll(async () => {
  database = await freshDatabase();
}, 60_000);

afterAll(async () => {
  await database.close();
});

describe("発信（CreateCall × PostgreSQL）", () => {
  it("発信すると通話が DIALING で保存され、監査ログが残る", async () => {
    const t = await seedTenant(database);
    const deps = depsFor(database);
    const r = await new CreateCallUseCase(deps).execute(
      command(t.org, t.contactIds[0] ?? "", t.campaignId, "k-1"),
    );
    expect(r).toMatchObject({ ok: true, value: { replayed: false, call: { status: "DIALING" } } });
    if (!r.ok) return;
    expect(await deps.calls.get(t.org, r.value.call.id)).toMatchObject({
      status: "DIALING",
      providerCallId: `PC-${r.value.call.id}`,
    });
    const audit = await database.db.execute<{ action: string }>(
      sql`select action from audit_logs where organization_id = ${t.org} order by id`,
    );
    expect(audit.rows.map((x) => x.action)).toEqual(["call.requested"]);
  });

  it("同じ冪等キーを 20 並列で送っても外部発信は 1 件", async () => {
    const t = await seedTenant(database);
    const deps = depsFor(database);
    const uc = new CreateCallUseCase(deps);
    const results = await Promise.all(
      Array.from({ length: 20 }, () =>
        uc.execute(command(t.org, t.contactIds[0] ?? "", t.campaignId, "same-key")),
      ),
    );
    expect(deps.telephony.requests).toHaveLength(1);
    expect(results.every((x) => x.ok)).toBe(true);
  });

  it("1日上限 1 件で、別々の相手・別々のキーを同時に送っても外部発信は 1 件（組織ロック）", async () => {
    const t = await seedTenant(database, {
      dailyCap: 1,
      phones: ["090-0000-0001", "090-0000-0002", "090-0000-0003"],
    });
    const deps = depsFor(database);
    const uc = new CreateCallUseCase(deps);
    const results = await Promise.all(
      t.contactIds.map((c, i) => uc.execute(command(t.org, c, t.campaignId, `k-${i}`))),
    );
    expect(deps.telephony.requests).toHaveLength(1);
    expect(results.filter((x) => !x.ok).map((x) => !x.ok && x.error.code)).toEqual([
      "DAILY_CAP_REACHED",
      "DAILY_CAP_REACHED",
    ]);
  });

  it("全発信停止（system_controls）中は発信しない", async () => {
    const t = await seedTenant(database);
    const deps = depsFor(database);
    await database.db.execute(sql`update system_controls set outbound_stopped = true`);
    try {
      const r = await new CreateCallUseCase(deps).execute(
        command(t.org, t.contactIds[0] ?? "", t.campaignId, "k-stop"),
      );
      expect(r).toMatchObject({ ok: false, error: { code: "OUTBOUND_STOPPED" } });
      expect(deps.telephony.requests).toHaveLength(0);
    } finally {
      await database.db.execute(sql`update system_controls set outbound_stopped = false`);
    }
  });
});

describe("組織単位の排他（UnitOfWork.runExclusive）", () => {
  it("実行中は、その組織の advisory lock を握っている（コミット後に解放）", async () => {
    const t = await seedTenant(database);
    const deps = depsFor(database);
    const heldDuring = await deps.uow.runExclusive(t.org, async () => {
      // 排他の中のクエリは同じトランザクションで流れる
      const scope = new TenantScope(database.db);
      return scope.withTenant(t.org, async (tx) => {
        const r = await tx.execute<{ n: number }>(
          sql`select count(*)::int as n from pg_locks where locktype = 'advisory' and granted`,
        );
        return r.rows[0]?.n ?? 0;
      });
    });
    expect(heldDuring).toBeGreaterThanOrEqual(1);
    const after = await database.db.execute<{ n: number }>(
      sql`select count(*)::int as n from pg_locks where locktype = 'advisory'`,
    );
    expect(after.rows[0]?.n).toBe(0);
  });
});

describe("結果の記録（RecordOutcome × PostgreSQL）", () => {
  it("「拒否」で抑止が永続化され、その相手の予定は取り消され、以後の発信は拒否される", async () => {
    const t = await seedTenant(database);
    const deps = depsFor(database);
    const contactId = t.contactIds[0] ?? "";
    const placed = await new CreateCallUseCase(deps).execute(
      command(t.org, contactId, t.campaignId, "k-dnc"),
    );
    if (!placed.ok) throw new Error(`setup: ${placed.error.code}`);
    await deps.calls.update({ ...placed.value.call, status: "ENDED" });

    const recorded = await new RecordOutcomeUseCase(deps).execute({
      organizationId: t.org,
      actorId: OPERATOR,
      callId: placed.value.call.id,
      outcome: "拒否",
    });
    expect(recorded).toMatchObject({ ok: true, value: { suppressed: true } });
    expect(await deps.suppression.canContact(t.org, phone("090-0000-0001"))).toBe(false);

    deps.clock.set(jst("2026-10-06T10:00:00"));
    const again = await new CreateCallUseCase(deps).execute(
      command(t.org, contactId, t.campaignId, "k-dnc-2"),
    );
    expect(again).toMatchObject({ ok: false, error: { code: "CONTACT_SUPPRESSED" } });
    expect(deps.telephony.requests).toHaveLength(1);
  });

  it("トランザクションの途中で失敗したら、結果も抑止も残らない（ロールバック）", async () => {
    const t = await seedTenant(database);
    const deps = depsFor(database);
    const placed = await new CreateCallUseCase(deps).execute(
      command(t.org, t.contactIds[0] ?? "", t.campaignId, "k-rb"),
    );
    if (!placed.ok) throw new Error(`setup: ${placed.error.code}`);
    // 抑止を書いた後の監査ログで失敗させる
    const append = deps.audit.append.bind(deps.audit);
    deps.audit.append = async (entry) => {
      if (entry.action === "suppression.added") throw new Error("injected failure");
      return append(entry);
    };
    await expect(
      new RecordOutcomeUseCase(deps).execute({
        organizationId: t.org,
        actorId: OPERATOR,
        callId: placed.value.call.id,
        outcome: "拒否",
      }),
    ).rejects.toThrow("injected failure");
    expect(await deps.outcomes.get(t.org, placed.value.call.id)).toBeUndefined();
    expect(await deps.suppression.canContact(t.org, phone("090-0000-0001"))).toBe(true);
  });
});

describe("抑止は再起動・再接続の後も残る（INV-1 の DB 層）", () => {
  it("抑止を登録して DB を閉じ、開き直しても発信は拒否される", async () => {
    const dir = mkdtempSync(join(tmpdir(), "tac-db-"));
    try {
      const first = await freshDatabase(dir);
      const t = await seedTenant(first);
      await createPgDeps(new TenantScope(first.db)).suppression.add({
        organizationId: t.org,
        phone: phone("090-0000-0001"),
        reason: "DO_NOT_CALL",
        source: "test",
        actorId: OPERATOR,
      });
      await first.close();

      const reopened = await freshDatabase(dir);
      try {
        const deps = depsFor(reopened);
        expect(await deps.suppression.canContact(t.org, phone("090-0000-0001"))).toBe(false);
        const r = await new CreateCallUseCase(deps).execute(
          command(t.org, t.contactIds[0] ?? "", t.campaignId, "k-restart"),
        );
        expect(r).toMatchObject({ ok: false, error: { code: "CONTACT_SUPPRESSED" } });
        expect(deps.telephony.requests).toHaveLength(0);
      } finally {
        await reopened.close();
      }
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  }, 60_000);
});
