import type { CallStatus } from "@tac/domain";
import { describe, expect, it } from "vitest";
import { CreateCallUseCase } from "../src/create-call.js";
import { ApplyProviderEventUseCase, type ProviderEventCommand } from "../src/provider-events.js";
import { ORG_A, setup } from "./support.js";

/*
 * Phase 7：プロバイダの Webhook（状態の通知）を通話に反映する。
 * Webhook は at-least-once（重複する）・順序が入れ替わる・遅れて届く・同時に届く。
 * どの場合も、終端状態は後退せず、同じイベントは1回だけ効く。
 */

async function placedCall() {
  const ctx = setup();
  const r = await new CreateCallUseCase(ctx.deps).execute(ctx.callCommand());
  if (!r.ok) throw new Error(`setup: ${r.error.code}`);
  return { ...ctx, call: r.value.call, uc: new ApplyProviderEventUseCase(ctx.deps) };
}

let seq = 0;
const event = (
  providerCallId: string | undefined,
  status: CallStatus,
  over: Partial<ProviderEventCommand> = {},
): ProviderEventCommand => {
  seq += 1;
  return {
    provider: "recording",
    eventId: `evt-${seq}`,
    providerCallId,
    callId: undefined,
    status,
    occurredAt: new Date("2026-10-05T01:00:00Z"),
    payload: { status },
    ...over,
  };
};

const statusOf = async (ctx: Awaited<ReturnType<typeof placedCall>>) =>
  (await ctx.deps.calls.get(ORG_A, ctx.call.id))?.status;

describe("ApplyProviderEventUseCase：正常系", () => {
  it("ring → answer → end の順に進み、イベントが記録される", async () => {
    const ctx = await placedCall();
    const pcid = ctx.call.providerCallId;
    for (const s of ["RINGING", "IN_PROGRESS", "ENDED"] as const) {
      expect(await ctx.uc.execute(event(pcid, s))).toMatchObject({ kind: "APPLIED", status: s });
    }
    expect(await statusOf(ctx)).toBe("ENDED");
    expect(ctx.deps.callEvents.entries.map((e) => [e.status, e.applied])).toEqual([
      ["RINGING", true],
      ["IN_PROGRESS", true],
      ["ENDED", true],
    ]);
    expect(ctx.deps.events.types()).toContain("CallStatusChanged");
  });
});

describe("ApplyProviderEventUseCase：重複・順序違い・遅延", () => {
  it("同じイベント（同じ event_id）が2回届いても、2回目は何も変えない", async () => {
    const ctx = await placedCall();
    const e = event(ctx.call.providerCallId, "RINGING");
    expect(await ctx.uc.execute(e)).toMatchObject({ kind: "APPLIED" });
    expect(await ctx.uc.execute(e)).toMatchObject({ kind: "DUPLICATE" });
    expect(ctx.deps.callEvents.entries).toHaveLength(1);
  });

  it("ENDED が RINGING より先に届いても、最終状態は ENDED", async () => {
    const ctx = await placedCall();
    const pcid = ctx.call.providerCallId;
    await ctx.uc.execute(event(pcid, "ENDED"));
    expect(await ctx.uc.execute(event(pcid, "RINGING"))).toMatchObject({
      kind: "STALE",
      status: "ENDED",
    });
    expect(await statusOf(ctx)).toBe("ENDED");
    expect(ctx.deps.callEvents.entries.map((e) => e.applied)).toEqual([true, false]);
  });

  it("終端の後に遅れて届いた IN_PROGRESS は無視する（後退しない）", async () => {
    const ctx = await placedCall();
    const pcid = ctx.call.providerCallId;
    await ctx.uc.execute(event(pcid, "BUSY"));
    await ctx.uc.execute(event(pcid, "IN_PROGRESS"));
    expect(await statusOf(ctx)).toBe("BUSY");
  });

  it("同時に届いた IN_PROGRESS と ENDED を何度試しても、最終状態は ENDED", async () => {
    for (let i = 0; i < 20; i += 1) {
      const ctx = await placedCall();
      const pcid = ctx.call.providerCallId;
      await ctx.uc.execute(event(pcid, "RINGING"));
      await Promise.all([
        ctx.uc.execute(event(pcid, "IN_PROGRESS")),
        ctx.uc.execute(event(pcid, "ENDED")),
      ]);
      expect(await statusOf(ctx)).toBe("ENDED");
    }
  });

  it("同じイベントが同時に2回届いても、効くのは1回", async () => {
    const ctx = await placedCall();
    const e = event(ctx.call.providerCallId, "RINGING");
    const results = await Promise.all([ctx.uc.execute(e), ctx.uc.execute(e)]);
    expect(results.filter((r) => r.kind === "APPLIED")).toHaveLength(1);
    expect(ctx.deps.callEvents.entries).toHaveLength(1);
  });
});

describe("ApplyProviderEventUseCase：通話の特定", () => {
  it("知らない通話のイベントは例外にせず UNKNOWN_CALL として記録だけする", async () => {
    const ctx = await placedCall();
    expect(await ctx.uc.execute(event("PC-unknown", "ENDED"))).toMatchObject({
      kind: "UNKNOWN_CALL",
    });
    expect(await statusOf(ctx)).toBe("DIALING");
  });

  it("発信がタイムアウトした通話（providerCallId 未記録）も、callId で特定してプロバイダの ID を記録する", async () => {
    const ctx = setup();
    ctx.deps.telephony.mode = "timeout";
    const r = await new CreateCallUseCase(ctx.deps).execute(ctx.callCommand());
    expect(r).toMatchObject({ ok: false, error: { code: "PROVIDER_TIMEOUT" } });
    const [call] = [...ctx.deps.calls.rows.values()];
    if (!call) throw new Error("setup");
    const uc = new ApplyProviderEventUseCase(ctx.deps);
    expect(await uc.execute(event("PC-late", "RINGING", { callId: call.id }))).toMatchObject({
      kind: "APPLIED",
    });
    expect(await ctx.deps.calls.get(ORG_A, call.id)).toMatchObject({
      status: "RINGING",
      providerCallId: "PC-late",
      provider: "recording",
    });
  });

  it("別のプロバイダ名のイベントでは、同じ providerCallId でも通話を特定しない", async () => {
    const ctx = await placedCall();
    expect(
      await ctx.uc.execute(event(ctx.call.providerCallId, "ENDED", { provider: "other" })),
    ).toMatchObject({ kind: "UNKNOWN_CALL" });
  });

  it("処理の途中で失敗したイベントは、再送されたら処理し直す（重複扱いで捨てない）", async () => {
    const ctx = await placedCall();
    const e = event(ctx.call.providerCallId, "RINGING");
    const append = ctx.deps.callEvents.append.bind(ctx.deps.callEvents);
    ctx.deps.callEvents.append = async () => {
      throw new Error("db down");
    };
    await expect(ctx.uc.execute(e)).rejects.toThrow("db down");
    ctx.deps.callEvents.append = append;
    // インメモリの UnitOfWork はロールバックしないので APPLIED / STALE のどちらもありうる
    // （PostgreSQL では状態の変更ごとロールバックされる：db/test/provider-events.test.ts）
    expect(await ctx.uc.execute(e)).not.toMatchObject({ kind: "DUPLICATE" });
    expect(ctx.deps.callEvents.entries.map((x) => x.eventId)).toEqual([e.eventId]);
    expect(await statusOf(ctx)).toBe("RINGING");
  });
});

describe("CreateCall と Webhook の競合", () => {
  it("発信 API の応答より先に RINGING の Webhook が届いても、DIALING に戻さない", async () => {
    const ctx = setup();
    const uc = new ApplyProviderEventUseCase(ctx.deps);
    const createCall = ctx.deps.telephony.createCall.bind(ctx.deps.telephony);
    ctx.deps.telephony.createCall = async (req) => {
      const placed = await createCall(req);
      // プロバイダは応答を返す前に Webhook を送ってくることがある
      await uc.execute(event(placed.providerCallId, "RINGING", { callId: req.callId }));
      return placed;
    };
    const r = await new CreateCallUseCase(ctx.deps).execute(ctx.callCommand());
    expect(r).toMatchObject({ ok: true, value: { call: { status: "RINGING" } } });
    const [call] = [...ctx.deps.calls.rows.values()];
    expect(call?.status).toBe("RINGING");
  });
});
