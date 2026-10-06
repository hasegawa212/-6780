import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { type ConversationPhase, SAFETY_PHASES } from "../src/conversation.js";
import { applyCustomerUtterance } from "../src/utterance-safety.js";

/**
 * AI が応答する前に通す決定的な安全層（発話 → Safety）を評価する。
 * 文言の一致ではなく、方針（止まるか・どの Safety か・AI がまだ話せるか）を確かめる。
 * LLM 応答の評価（tool の選び方・禁止行為）は Phase 12 で追加する。
 */
interface EvalCase {
  readonly id: string;
  readonly phase: ConversationPhase;
  readonly utterance: string;
  readonly expect: {
    readonly safety: (typeof SAFETY_PHASES)[number] | null;
    readonly softDecline?: boolean;
  };
}

const REQUIRED = [
  "normal",
  "interested",
  "rejection",
  "dnc",
  "objection",
  "scheduling",
  "handoff",
  "adversarial",
];
const root = join(import.meta.dirname, "..", "..", "..", "evals");
const categories = readdirSync(root, { withFileTypes: true })
  .filter((d) => d.isDirectory())
  .map((d) => d.name);

describe("eval dataset", () => {
  it("has every required category", () => {
    for (const c of REQUIRED) expect(categories).toContain(c);
  });
});

for (const category of categories) {
  const cases = JSON.parse(readFileSync(join(root, category, "cases.json"), "utf8")) as EvalCase[];
  describe(`evals/${category}`, () => {
    it.each(cases.map((c) => [c.id, c] as const))("%s", (_id, c) => {
      const before = { phase: c.phase, controller: "AI" as const };
      const r = applyCustomerUtterance(before, c.utterance);
      if (c.expect.safety === null && c.expect.softDecline) {
        // 抑止はしない・この通話では説得をやめる
        expect(r.softDecline).toBe(true);
        expect(r.effects).toEqual([]);
        expect(r.state.phase).toBe("WRAP_UP");
        return;
      }
      if (c.expect.safety === null) {
        expect(r).toEqual({ state: before, effects: [] });
        return;
      }
      expect(r.state.phase).toBe(c.expect.safety);
      // どの Safety に入っても、AI は営業を続けられない
      expect(SAFETY_PHASES).toContain(r.state.phase);
      if (c.expect.safety === "DO_NOT_CALL" || c.expect.safety === "STOP_REQUESTED") {
        expect(r.effects).toEqual(expect.arrayContaining(["END_CONVERSATION", "ADD_SUPPRESSION"]));
      }
      if (c.expect.safety === "COMPLAINT") expect(r.state.controller).toBe("HUMAN");
    });
  });
}
