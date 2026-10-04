import { describe, expect, it } from "vitest";
import { CallQueueQuery } from "../src/call-queue.js";
import { CreateCallUseCase } from "../src/create-call.js";
import { RecordOutcomeUseCase } from "../src/record-outcome.js";
import { jst, OPERATOR, ORG_A, phone, setup } from "./support.js";

async function placedCall() {
  const ctx = setup();
  const r = await new CreateCallUseCase(ctx.deps).execute(ctx.callCommand());
  if (!r.ok) throw new Error("setup call failed");
  return { ...ctx, call: r.value.call };
}

const outcomeCommand = (callId: string, outcome: string, extra: Record<string, unknown> = {}) => ({
  organizationId: ORG_A,
  actorId: OPERATOR,
  callId,
  outcome,
  ...extra,
});

describe("RecordOutcomeUseCase", () => {
  it("accepts the current five button labels", async () => {
    const { deps, call } = await placedCall();
    const r = await new RecordOutcomeUseCase(deps).execute(outcomeCommand(call.id, "成約"));
    expect(r.ok && r.value.outcome.code).toBe("WON");
  });

  it("rejects unknown outcomes instead of storing free text", async () => {
    const { deps, call } = await placedCall();
    const r = await new RecordOutcomeUseCase(deps).execute(outcomeCommand(call.id, "たぶん"));
    expect(r).toMatchObject({ ok: false, error: { code: "INVALID_OUTCOME" } });
  });

  // 必須ドメインテスト 2：拒否 → 抑止 → キューから外れる → 以後発信できない
  it("拒否 suppresses the number, cancels open follow-ups, and blocks future calls", async () => {
    const { deps, clock, call, callCommand } = await placedCall();
    const outcome = new RecordOutcomeUseCase(deps);
    // 先に「検討」でフォローアップがある状態を作るため、別の通話を記録しておく
    await deps.followUps.insert({
      id: "fu-old",
      organizationId: ORG_A,
      contactId: "c-1",
      kind: "FOLLOW_UP",
      dueAt: jst("2026-10-05T09:00:00"),
      status: "OPEN",
      sourceCallId: "older-call",
    });
    const queue = new CallQueueQuery(deps);
    expect((await queue.listDue(ORG_A)).map((i) => i.contactId)).toEqual(["c-1"]);

    const r = await outcome.execute(outcomeCommand(call.id, "拒否"));
    expect(r.ok && r.value.suppressed).toBe(true);
    expect(await deps.suppression.canContact(ORG_A, phone("090-0000-0001"))).toBe(false);
    expect(await queue.listDue(ORG_A)).toEqual([]);
    expect(deps.events.types()).toEqual(
      expect.arrayContaining(["OutcomeRecorded", "SuppressionAdded"]),
    );
    expect(deps.audit.entries.map((e) => e.action)).toContain("suppression.added");

    clock.set(jst("2026-10-06T10:00:00"));
    const again = await new CreateCallUseCase(deps).execute(
      callCommand({ idempotencyKey: "next-day" }),
    );
    expect(again).toMatchObject({ ok: false, error: { code: "CONTACT_SUPPRESSED" } });
  });

  // 必須ドメインテスト 6
  it("検討 creates a follow-up three days later that appears in the queue when due", async () => {
    const { deps, clock, call } = await placedCall();
    const r = await new RecordOutcomeUseCase(deps).execute(outcomeCommand(call.id, "検討"));
    expect(r.ok && r.value.followUp?.dueAt).toEqual(jst("2026-10-08T10:00:00"));
    const queue = new CallQueueQuery(deps);
    expect(await queue.listDue(ORG_A)).toEqual([]);
    clock.set(jst("2026-10-08T10:00:00"));
    expect((await queue.listDue(ORG_A)).map((i) => i.kind)).toEqual(["FOLLOW_UP"]);
  });

  it("折り返し requires a callback time", async () => {
    const { deps, call } = await placedCall();
    const uc = new RecordOutcomeUseCase(deps);
    expect(await uc.execute(outcomeCommand(call.id, "折り返し"))).toMatchObject({
      ok: false,
      error: { code: "CALLBACK_TIME_REQUIRED" },
    });
    const at = jst("2026-10-06T11:00:00");
    const r = await uc.execute(outcomeCommand(call.id, "折り返し", { callbackAt: at }));
    expect(r.ok && r.value.followUp).toMatchObject({ kind: "CALLBACK", dueAt: at });
  });

  it("is idempotent for the same outcome and rejects a conflicting one", async () => {
    const { deps, call } = await placedCall();
    const uc = new RecordOutcomeUseCase(deps);
    await uc.execute(outcomeCommand(call.id, "検討"));
    const same = await uc.execute(outcomeCommand(call.id, "検討"));
    expect(same.ok && same.value.replayed).toBe(true);
    expect(deps.followUps.rows.size).toBe(1);
    const conflict = await uc.execute(outcomeCommand(call.id, "成約"));
    expect(conflict).toMatchObject({ ok: false, error: { code: "OUTCOME_ALREADY_RECORDED" } });
  });

  it("cannot record an outcome for another tenant's call", async () => {
    const { deps, call } = await placedCall();
    const r = await new RecordOutcomeUseCase(deps).execute({
      ...outcomeCommand(call.id, "成約"),
      organizationId: "org-b" as typeof ORG_A,
    });
    expect(r).toMatchObject({ ok: false, error: { code: "CALL_NOT_FOUND" } });
  });
});
