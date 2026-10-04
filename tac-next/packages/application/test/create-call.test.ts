import { describe, expect, it } from "vitest";
import { CreateCallUseCase } from "../src/create-call.js";
import { jst, ORG_A, ORG_B, phone, setup } from "./support.js";

describe("CreateCallUseCase", () => {
  it("places a call through the provider and persists it", async () => {
    const { deps, callCommand } = setup();
    const r = await new CreateCallUseCase(deps).execute(callCommand());
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.value.replayed).toBe(false);
    expect(r.value.call.status).toBe("DIALING");
    expect(r.value.call.providerCallId).toBe("PC-1");
    expect(deps.telephony.requests).toHaveLength(1);
    expect(deps.telephony.requests[0]?.to).toBe(phone("090-0000-0001"));
    expect(deps.events.types()).toEqual(["CallRequested", "CallDialing"]);
    expect(deps.audit.entries.map((e) => e.action)).toContain("call.requested");
  });

  it("speaks the disclosure (company, agent, product) on connect", async () => {
    const { deps, callCommand } = setup();
    await new CreateCallUseCase(deps).execute(callCommand());
    const text = deps.telephony.requests[0]?.disclosureText ?? "";
    expect(text).toContain("株式会社サンプル不動産");
    expect(text).toContain("佐藤");
    expect(text).toContain("新築マンション");
  });

  // 必須ドメインテスト 1
  it("refuses to call a suppressed contact and never reaches the provider", async () => {
    const { deps, callCommand } = setup();
    await deps.suppression.add({
      organizationId: ORG_A,
      phone: phone("090-0000-0001"),
      reason: "拒否",
      source: "test",
      actorId: callCommand().actorId,
    });
    const r = await new CreateCallUseCase(deps).execute(callCommand());
    expect(r).toMatchObject({ ok: false, error: { code: "CONTACT_SUPPRESSED" } });
    expect(deps.telephony.requests).toHaveLength(0);
    expect(deps.calls.rows.size).toBe(0);
    expect(deps.audit.entries.map((e) => e.action)).toContain("call.blocked");
    expect(deps.events.types()).toEqual(["CallBlocked"]);
  });

  // 必須ドメインテスト 3
  it("does not dial twice when the same Idempotency-Key is retried", async () => {
    const { deps, callCommand } = setup();
    const uc = new CreateCallUseCase(deps);
    const first = await uc.execute(callCommand());
    const second = await uc.execute(callCommand());
    expect(deps.telephony.requests).toHaveLength(1);
    expect(second.ok && second.value.replayed).toBe(true);
    expect(first.ok && second.ok && second.value.call.id).toBe(first.ok && first.value.call.id);
  });

  it("does not dial twice when duplicate requests race", async () => {
    const { deps, callCommand } = setup();
    const uc = new CreateCallUseCase(deps);
    const results = await Promise.all([uc.execute(callCommand()), uc.execute(callCommand())]);
    expect(deps.telephony.requests).toHaveLength(1);
    expect(results.filter((r) => r.ok && r.value.replayed)).toHaveLength(1);
  });

  it("rejects reusing an Idempotency-Key for a different request", async () => {
    const { deps, callCommand } = setup();
    const uc = new CreateCallUseCase(deps);
    await uc.execute(callCommand());
    const r = await uc.execute(callCommand({ contactId: "c-2" }));
    expect(r).toMatchObject({ ok: false, error: { code: "IDEMPOTENCY_KEY_REUSED" } });
    expect(deps.telephony.requests).toHaveLength(1);
  });

  it.each(["", " ", "x".repeat(256)])("rejects an invalid Idempotency-Key %j", async (key) => {
    const { deps, callCommand } = setup();
    const r = await new CreateCallUseCase(deps).execute(callCommand({ idempotencyKey: key }));
    expect(r).toMatchObject({ ok: false, error: { code: "INVALID_IDEMPOTENCY_KEY" } });
  });

  // 必須ドメインテスト 4（アプリケーション層での分離。DB では Phase 2 で RLS を含めて検証）
  it("cannot use another tenant's contact or campaign", async () => {
    const { deps, callCommand } = setup();
    const uc = new CreateCallUseCase(deps);
    expect(await uc.execute(callCommand({ contactId: "c-b" }))).toMatchObject({
      ok: false,
      error: { code: "CONTACT_NOT_FOUND" },
    });
    expect(await uc.execute(callCommand({ campaignId: "camp-b" }))).toMatchObject({
      ok: false,
      error: { code: "CAMPAIGN_NOT_FOUND" },
    });
    expect(deps.telephony.requests).toHaveLength(0);
    expect(ORG_B).not.toBe(ORG_A);
  });

  it("refuses outside the calling window", async () => {
    const { deps, clock, callCommand } = setup();
    clock.set(jst("2026-10-05T21:00:00"));
    const r = await new CreateCallUseCase(deps).execute(callCommand());
    expect(r).toMatchObject({ ok: false, error: { code: "OUTSIDE_CALLING_WINDOW" } });
  });

  it("refuses a second call to the same number within 24 hours", async () => {
    const { deps, clock, callCommand } = setup();
    const uc = new CreateCallUseCase(deps);
    await uc.execute(callCommand());
    clock.set(jst("2026-10-05T15:00:00"));
    const r = await uc.execute(callCommand({ idempotencyKey: "key-2" }));
    expect(r).toMatchObject({ ok: false, error: { code: "NUMBER_DAILY_LIMIT_REACHED" } });
  });

  it("refuses when the disclosure would be incomplete (agent name missing)", async () => {
    const { deps, callCommand } = setup();
    const r = await new CreateCallUseCase(deps).execute(callCommand({ agentName: "  " }));
    expect(r).toMatchObject({ ok: false, error: { code: "DISCLOSURE_INCOMPLETE" } });
  });

  it("refuses AI-voice outbound unless the org enabled it and the contact consented", async () => {
    const { deps, callCommand } = setup();
    const uc = new CreateCallUseCase(deps);
    expect(await uc.execute(callCommand({ mode: "AI_VOICE" }))).toMatchObject({
      ok: false,
      error: { code: "AI_VOICE_OUTBOUND_DISABLED" },
    });
    const org = deps.organizations.rows.get(ORG_A);
    if (org) deps.organizations.rows.set(ORG_A, { ...org, aiVoiceOutboundEnabled: true });
    expect(await uc.execute(callCommand({ mode: "AI_VOICE", idempotencyKey: "k2" }))).toMatchObject(
      { ok: false, error: { code: "CONSENT_REQUIRED" } },
    );
    deps.consents.grant(ORG_A, "c-1");
    const ok = await uc.execute(callCommand({ mode: "AI_VOICE", idempotencyKey: "k3" }));
    expect(ok.ok).toBe(true);
  });

  it("marks the call FAILED when the provider rejects it, without retrying", async () => {
    const { deps, callCommand } = setup();
    deps.telephony.mode = "error";
    const r = await new CreateCallUseCase(deps).execute(callCommand());
    expect(r).toMatchObject({ ok: false, error: { code: "PROVIDER_ERROR" } });
    expect([...deps.calls.rows.values()][0]?.status).toBe("FAILED");
    expect(deps.telephony.requests).toHaveLength(1);
  });

  it("keeps the call REQUESTED on provider timeout so a retry cannot double-dial", async () => {
    const { deps, callCommand } = setup();
    const uc = new CreateCallUseCase(deps);
    deps.telephony.mode = "timeout";
    const r = await uc.execute(callCommand());
    expect(r).toMatchObject({ ok: false, error: { code: "PROVIDER_TIMEOUT" } });
    expect([...deps.calls.rows.values()][0]?.status).toBe("REQUESTED");
    deps.telephony.mode = "ok";
    const retry = await uc.execute(callCommand());
    expect(retry.ok && retry.value.replayed).toBe(true);
    expect(deps.telephony.requests).toHaveLength(1);
  });
});

describe("CreateCallUseCase — production safety controls (ADR-0006)", () => {
  it("STOP ALL OUTBOUND blocks every call before anything else", async () => {
    const { deps, callCommand } = setup();
    deps.safety.stopAllOutbound();
    const r = await new CreateCallUseCase(deps).execute(callCommand());
    expect(r).toMatchObject({ ok: false, error: { code: "OUTBOUND_STOPPED" } });
    expect(deps.telephony.requests).toHaveLength(0);
    expect(deps.audit.entries.map((e) => e.action)).toContain("call.blocked");
    deps.safety.resumeOutbound();
    expect((await new CreateCallUseCase(deps).execute(callCommand())).ok).toBe(true);
  });

  it("a paused organization cannot place calls", async () => {
    const { deps, callCommand } = setup();
    const org = deps.organizations.rows.get(ORG_A);
    if (org) deps.organizations.rows.set(ORG_A, { ...org, paused: true });
    expect(await new CreateCallUseCase(deps).execute(callCommand())).toMatchObject({
      ok: false,
      error: { code: "ORGANIZATION_PAUSED" },
    });
  });

  it("a paused campaign cannot place calls", async () => {
    const { deps, callCommand } = setup();
    const c = deps.campaigns.rows.get("camp-1");
    if (c) deps.campaigns.rows.set("camp-1", { ...c, paused: true });
    expect(await new CreateCallUseCase(deps).execute(callCommand())).toMatchObject({
      ok: false,
      error: { code: "CAMPAIGN_PAUSED" },
    });
  });

  it("enforces the organization's concurrent call limit and frees the slot when a call ends", async () => {
    const { deps, callCommand } = setup();
    const org = deps.organizations.rows.get(ORG_A);
    if (org) deps.organizations.rows.set(ORG_A, { ...org, maxConcurrentCalls: 1 });
    const uc = new CreateCallUseCase(deps);
    const first = await uc.execute(callCommand());
    const second = await uc.execute(callCommand({ contactId: "c-2", idempotencyKey: "k2" }));
    expect(second).toMatchObject({ ok: false, error: { code: "CONCURRENCY_LIMIT_REACHED" } });
    if (!first.ok) throw new Error("first call should succeed");
    await deps.calls.update({ ...first.value.call, status: "ENDED" });
    const third = await uc.execute(callCommand({ contactId: "c-2", idempotencyKey: "k3" }));
    expect(third.ok).toBe(true);
  });

  it("refuses when the budget is exhausted", async () => {
    const { deps, callCommand } = setup();
    deps.budget.set(ORG_A, 0);
    expect(await new CreateCallUseCase(deps).execute(callCommand())).toMatchObject({
      ok: false,
      error: { code: "BUDGET_EXCEEDED" },
    });
  });
});
