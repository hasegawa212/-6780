import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { type CallPolicyFacts, evaluateCallPolicy } from "../src/call-policy.js";

const allowedFacts: CallPolicyFacts = {
  suppressed: false,
  countryAllowed: true,
  withinCallingWindow: true,
  dialedToday: 0,
  dailyCap: 100,
  callsToNumberToday: 0,
  perNumberDailyLimit: 1,
  disclosure: { companyName: "株式会社テスト不動産", agentName: "佐藤", product: "新築マンション" },
  mode: "HUMAN_DIALED",
  aiVoiceOutboundEnabled: false,
  hasValidConsent: false,
  outboundStopped: false,
  organizationPaused: false,
  campaignPaused: false,
  activeCalls: 0,
  maxConcurrentCalls: 5,
  budgetRemaining: null,
};

describe("evaluateCallPolicy", () => {
  it("allows a human-dialed call when every rule passes", () => {
    expect(evaluateCallPolicy(allowedFacts)).toEqual({ allowed: true });
  });

  it("denies a suppressed contact first, whatever else is true", () => {
    const facts = fc.record({
      countryAllowed: fc.boolean(),
      withinCallingWindow: fc.boolean(),
      dialedToday: fc.nat(500),
      callsToNumberToday: fc.nat(5),
      hasValidConsent: fc.boolean(),
      aiVoiceOutboundEnabled: fc.boolean(),
      mode: fc.constantFrom("HUMAN_DIALED" as const, "AI_VOICE" as const),
    });
    fc.assert(
      fc.property(facts, (f) => {
        const r = evaluateCallPolicy({ ...allowedFacts, ...f, suppressed: true });
        expect(r.allowed).toBe(false);
        if (!r.allowed) expect(r.code).toBe("CONTACT_SUPPRESSED");
      }),
    );
  });

  it.each<[Partial<CallPolicyFacts>, string]>([
    [{ countryAllowed: false }, "COUNTRY_NOT_ALLOWED"],
    [{ withinCallingWindow: false }, "OUTSIDE_CALLING_WINDOW"],
    [{ dialedToday: 100 }, "DAILY_CAP_REACHED"],
    [{ callsToNumberToday: 1 }, "NUMBER_DAILY_LIMIT_REACHED"],
    [
      { disclosure: { companyName: "株式会社テスト不動産", agentName: " ", product: "新築" } },
      "DISCLOSURE_INCOMPLETE",
    ],
    [{ mode: "AI_VOICE" }, "AI_VOICE_OUTBOUND_DISABLED"],
    [{ mode: "AI_VOICE", aiVoiceOutboundEnabled: true }, "CONSENT_REQUIRED"],
  ])("denies %j with %s", (override, code) => {
    const r = evaluateCallPolicy({ ...allowedFacts, ...override });
    expect(r.allowed).toBe(false);
    if (!r.allowed) expect(r.code).toBe(code);
  });

  it.each<[Partial<CallPolicyFacts>, string]>([
    [{ outboundStopped: true }, "OUTBOUND_STOPPED"],
    [{ organizationPaused: true }, "ORGANIZATION_PAUSED"],
    [{ campaignPaused: true }, "CAMPAIGN_PAUSED"],
    [{ activeCalls: 5 }, "CONCURRENCY_LIMIT_REACHED"],
    [{ budgetRemaining: 0 }, "BUDGET_EXCEEDED"],
  ])("production safety control %j denies with %s", (override, code) => {
    const r = evaluateCallPolicy({ ...allowedFacts, ...override });
    expect(r.allowed).toBe(false);
    if (!r.allowed) expect(r.code).toBe(code);
  });

  it("STOP ALL OUTBOUND wins over everything, including suppression", () => {
    fc.assert(
      fc.property(fc.boolean(), fc.boolean(), fc.boolean(), (suppressed, orgPaused, inWindow) => {
        const r = evaluateCallPolicy({
          ...allowedFacts,
          outboundStopped: true,
          suppressed,
          organizationPaused: orgPaused,
          withinCallingWindow: inWindow,
        });
        expect(!r.allowed && r.code).toBe("OUTBOUND_STOPPED");
      }),
    );
  });

  it("still lists suppression among the reasons while stopped (nothing is hidden)", () => {
    const r = evaluateCallPolicy({ ...allowedFacts, outboundStopped: true, suppressed: true });
    expect(!r.allowed && r.reasons).toEqual(["OUTBOUND_STOPPED", "CONTACT_SUPPRESSED"]);
  });

  it("a positive budget or no budget allows the call", () => {
    expect(evaluateCallPolicy({ ...allowedFacts, budgetRemaining: 100 }).allowed).toBe(true);
  });

  it("a null daily cap means no cap", () => {
    expect(evaluateCallPolicy({ ...allowedFacts, dailyCap: null, dialedToday: 9999 }).allowed).toBe(
      true,
    );
  });

  it("AI voice outbound is allowed only with the feature flag AND a valid consent", () => {
    expect(
      evaluateCallPolicy({
        ...allowedFacts,
        mode: "AI_VOICE",
        aiVoiceOutboundEnabled: true,
        hasValidConsent: true,
      }),
    ).toEqual({ allowed: true });
  });

  it("reports every failing rule, primary code first", () => {
    const r = evaluateCallPolicy({ ...allowedFacts, suppressed: true, withinCallingWindow: false });
    expect(!r.allowed && r.reasons).toEqual(["CONTACT_SUPPRESSED", "OUTSIDE_CALLING_WINDOW"]);
  });
});
