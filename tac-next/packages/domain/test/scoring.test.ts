import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { DEFAULT_WEIGHTS, decideNextAction, scoreLead } from "../src/scoring.js";

describe("scoreLead (explainable priority)", () => {
  it("returns a 0-100 priority with per-factor contributions", () => {
    const r = scoreLead({
      fit: 1,
      eligibility: 1,
      contactability: 1,
      intent: 1,
      freshness: 1,
      engagement: 1,
    });
    expect(r.priority).toBe(100);
    expect(r.contributions).toHaveLength(6);
  });

  it("explains the recommendation with the biggest contributors first", () => {
    const r = scoreLead({ fit: 0.9, eligibility: 0.95, contactability: 0.2, intent: 0.1 });
    expect(r.contributions[0]?.factor).toBe("eligibility");
    expect(r.explanation[0]).toContain("審査適性");
  });

  it("lists missing factors instead of silently treating them as known", () => {
    const r = scoreLead({ fit: 0.5 });
    expect(r.missing).toEqual(
      expect.arrayContaining([
        "eligibility",
        "contactability",
        "intent",
        "freshness",
        "engagement",
      ]),
    );
  });

  it("priority always stays within 0-100 and contributions sum to it", () => {
    const s = fc.double({ min: 0, max: 1, noNaN: true });
    fc.assert(
      fc.property(fc.record({ fit: s, eligibility: s, contactability: s, intent: s }), (f) => {
        const r = scoreLead(f);
        expect(r.priority).toBeGreaterThanOrEqual(0);
        expect(r.priority).toBeLessThanOrEqual(100);
        const sum = r.contributions.reduce((a, c) => a + c.contribution, 0);
        expect(Math.abs(sum - r.priority)).toBeLessThan(1e-6);
      }),
    );
  });

  it("rejects out-of-range factor values", () => {
    expect(() => scoreLead({ fit: 1.5 })).toThrow(RangeError);
  });

  it("default weights sum to 1", () => {
    const total = Object.values(DEFAULT_WEIGHTS).reduce((a, b) => a + b, 0);
    expect(total).toBeCloseTo(1, 10);
  });
});

describe("decideNextAction", () => {
  const base = {
    suppressed: false,
    followUpDue: false,
    priority: 80,
    withinCallingWindow: true,
    noAnswerAttemptsExhausted: false,
  };

  it("a suppressed lead is always DO_NOT_CONTACT, regardless of score", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 100 }),
        fc.boolean(),
        fc.boolean(),
        fc.boolean(),
        (priority, followUpDue, withinCallingWindow, noAnswerAttemptsExhausted) => {
          const r = decideNextAction({
            suppressed: true,
            priority,
            followUpDue,
            withinCallingWindow,
            noAnswerAttemptsExhausted,
          });
          expect(r.action).toBe("DO_NOT_CONTACT");
          expect(r.confidence).toBe(1);
        },
      ),
    );
  });

  it.each([
    [{}, "CALL_NOW"],
    [{ withinCallingWindow: false }, "CALL_LATER"],
    [{ followUpDue: true }, "FOLLOW_UP"],
    [{ noAnswerAttemptsExhausted: true }, "HUMAN_REVIEW"],
    [{ priority: 20 }, "CALL_LATER"],
  ])("%j → %s", (override, action) => {
    const r = decideNextAction({ ...base, ...override });
    expect(r.action).toBe(action);
    expect(r.reason.length).toBeGreaterThan(0);
    expect(r.decidedBy).toBe("rule");
  });
});
