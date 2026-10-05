import fc from "fast-check";
import { describe, expect, it } from "vitest";
import {
  type ConversationState,
  canAiSpeak,
  enterSafety,
  highestPrioritySafety,
  isSafetyPhase,
  resumeAi,
  SAFETY_PHASES,
  SALES_PHASES,
  startConversation,
  takeOver,
  transitionPhase,
} from "../src/conversation.js";

const at = (phase: ConversationState["phase"], controller: "AI" | "HUMAN" = "AI") =>
  ({ phase, controller }) as ConversationState;

describe("conversation start", () => {
  it("always starts with DISCLOSURE and the AI in control", () => {
    expect(startConversation()).toEqual({ phase: "DISCLOSURE", controller: "AI" });
  });
});

describe("sales phase transitions", () => {
  it.each([
    ["DISCLOSURE", "PERMISSION"],
    ["PERMISSION", "IDENTIFICATION"],
    ["PERMISSION", "WRAP_UP"], // 「今は忙しい」→ 折り返しの約束をして終える
    ["IDENTIFICATION", "QUALIFICATION"],
    ["QUALIFICATION", "FAQ"],
    ["FAQ", "DISCOVERY"],
    ["DISCOVERY", "FAQ"],
    ["FAQ", "INTERESTED"],
    ["QUALIFICATION", "DISCOVERY"],
    ["DISCOVERY", "OBJECTION"],
    ["OBJECTION", "DISCOVERY"],
    ["DISCOVERY", "INTERESTED"],
    ["INTERESTED", "SCHEDULING"],
    ["SCHEDULING", "WRAP_UP"],
    ["WRAP_UP", "COMPLETED"],
  ] as const)("allows %s → %s", (from, to) => {
    const r = transitionPhase(at(from), to);
    expect(r.ok && r.value.phase).toBe(to);
  });

  it.each([
    ["DISCLOSURE", "QUALIFICATION"], // 名乗りを飛ばさない
    ["DISCLOSURE", "IDENTIFICATION"], // 話してよいかの確認（PERMISSION）を飛ばさない
    ["PERMISSION", "SCHEDULING"],
    ["FAQ", "SCHEDULING"],
    ["DISCLOSURE", "SCHEDULING"],
    ["IDENTIFICATION", "INTERESTED"],
    ["COMPLETED", "DISCOVERY"],
  ] as const)("forbids %s → %s", (from, to) => {
    expect(transitionPhase(at(from), to).ok).toBe(false);
  });

  it("safety phases cannot be entered through transitionPhase (only via enterSafety)", () => {
    expect(transitionPhase(at("DISCOVERY"), "DO_NOT_CALL").ok).toBe(false);
  });
});

describe("safety states take precedence over sales", () => {
  it("can be entered from every active sales phase", () => {
    const active = SALES_PHASES.filter((p) => p !== "COMPLETED");
    fc.assert(
      fc.property(fc.constantFrom(...active), fc.constantFrom(...SAFETY_PHASES), (p, s) => {
        expect(enterSafety(at(p), s).state.phase).toBe(s);
      }),
    );
  });

  it("never returns to a sales phase from a safety phase", () => {
    const target = fc.constantFrom(...SALES_PHASES.filter((p) => p !== "COMPLETED"));
    fc.assert(
      fc.property(fc.constantFrom(...SAFETY_PHASES), target, (s, p) => {
        expect(transitionPhase(at(s), p).ok).toBe(false);
      }),
    );
  });

  it("DO_NOT_CALL ends the conversation, adds a suppression and writes an audit entry", () => {
    const r = enterSafety(at("DISCOVERY"), "DO_NOT_CALL");
    expect(r.effects).toEqual(["END_CONVERSATION", "ADD_SUPPRESSION", "AUDIT"]);
    expect(canAiSpeak(r.state)).toBe(false);
  });

  it("STOP_REQUESTED also suppresses (a request to stop calling is a refusal)", () => {
    expect(enterSafety(at("OBJECTION"), "STOP_REQUESTED").effects).toContain("ADD_SUPPRESSION");
  });

  it("HUMAN_REQUIRED hands control to a human and silences the AI", () => {
    const r = enterSafety(at("QUALIFICATION"), "HUMAN_REQUIRED");
    expect(r.state.controller).toBe("HUMAN");
    expect(r.effects).toContain("REQUEST_HANDOFF");
    expect(canAiSpeak(r.state)).toBe(false);
  });

  it("a weaker safety signal cannot overwrite a stronger one", () => {
    const dnc = enterSafety(at("DISCOVERY"), "DO_NOT_CALL").state;
    expect(enterSafety(dnc, "HUMAN_REQUIRED").state.phase).toBe("DO_NOT_CALL");
    const emergency = enterSafety(dnc, "EMERGENCY").state;
    expect(emergency.phase).toBe("EMERGENCY");
  });

  it("COMPLAINT and SYSTEM_FAILURE hand the call to a human", () => {
    for (const s of ["COMPLAINT", "SYSTEM_FAILURE"] as const) {
      const r = enterSafety(at("DISCOVERY"), s);
      expect(r.state).toEqual({ phase: s, controller: "HUMAN" });
      expect(r.effects).toEqual(["REQUEST_HANDOFF", "AUDIT"]);
      expect(canAiSpeak(r.state)).toBe(false);
    }
  });

  it("a complaint never outranks a refusal", () => {
    expect(highestPrioritySafety(["COMPLAINT", "STOP_REQUESTED"])).toBe("STOP_REQUESTED");
    expect(highestPrioritySafety(["SYSTEM_FAILURE", "COMPLAINT"])).toBe("COMPLAINT");
  });

  it("picks the highest-priority safety phase when several are detected at once", () => {
    expect(highestPrioritySafety(["HUMAN_REQUIRED", "DO_NOT_CALL", "ABUSE"])).toBe("DO_NOT_CALL");
    expect(highestPrioritySafety(["PRIVACY_REQUEST", "EMERGENCY"])).toBe("EMERGENCY");
    expect(highestPrioritySafety([])).toBeUndefined();
  });

  it("isSafetyPhase distinguishes the two families", () => {
    expect(isSafetyPhase("LEGAL_BLOCK")).toBe(true);
    expect(isSafetyPhase("DISCOVERY")).toBe(false);
  });
});

describe("human override", () => {
  it("after a human takes over, the AI does not speak", () => {
    const s = takeOver(at("DISCOVERY"));
    expect(s.controller).toBe("HUMAN");
    expect(canAiSpeak(s)).toBe(false);
    // フェーズを進めても AI は黙ったまま（人が明示的に戻すまで）
    const r = transitionPhase(s, "INTERESTED");
    expect(r.ok && canAiSpeak(r.value)).toBe(false);
  });

  it("only an explicit resumeAi gives control back, and never in a safety phase", () => {
    expect(canAiSpeak(resumeAi(takeOver(at("DISCOVERY"))))).toBe(true);
    const dnc = enterSafety(at("DISCOVERY"), "DO_NOT_CALL").state;
    expect(canAiSpeak(resumeAi(dnc))).toBe(false);
  });
});
