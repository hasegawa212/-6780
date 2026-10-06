import { describe, expect, it } from "vitest";
import { followCategoryFromLabel } from "../src/tac-follow.js";

// 現行 TAC（telegram-ai-bot/tac/followup.py の CATEGORIES）のフォロー分類を次世代の語彙へ写す
describe("followCategoryFromLabel", () => {
  it.each([
    ["再調整希望", { nextAction: "CALL_LATER", followUpKind: "CALLBACK", suppression: "NONE" }],
    ["日程返答待ち", { nextAction: "FOLLOW_UP", followUpKind: "FOLLOW_UP", suppression: "NONE" }],
    ["不在", { nextAction: "CALL_LATER", followUpKind: "RETRY", suppression: "NONE" }],
    ["要確認", { nextAction: "HUMAN_REVIEW", suppression: "NONE" }],
    ["連絡停止", { nextAction: "DO_NOT_CONTACT", suppression: "CONTACT" }],
  ])("%s", (label, expected) => {
    expect(followCategoryFromLabel(label)).toEqual({ ok: true, value: expected });
  });

  it("連絡停止は必ず抑止になる（現行は DNC に入らなかった: 監査 R13）", () => {
    const r = followCategoryFromLabel("連絡停止");
    expect(r.ok && r.value.suppression).toBe("CONTACT");
  });

  it("前後の空白・全角空白は許容する", () => {
    expect(followCategoryFromLabel("　要確認 ")).toMatchObject({ ok: true });
  });

  it.each(["", "連絡予定", "対応済み", "再調整"])("%j は推測で写さない", (label) => {
    expect(followCategoryFromLabel(label)).toEqual({ ok: false, error: "UNKNOWN_FOLLOW_CATEGORY" });
  });
});
