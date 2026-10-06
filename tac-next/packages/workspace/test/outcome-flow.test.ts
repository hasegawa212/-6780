import { OUTCOME_PRESETS } from "@tac/domain";
import { describe, expect, it } from "vitest";
import { nextStepFor, outcomeButtons } from "../src/outcome-flow.js";

describe("outcomeButtons: 現行 TAC の5ボタンを先頭に保つ", () => {
  it("先頭5つは 成約・検討・折り返し・不在・拒否 の順", () => {
    expect(
      outcomeButtons()
        .slice(0, 5)
        .map((b) => b.label),
    ).toEqual(["成約", "検討", "折り返し", "不在", "拒否"]);
  });

  it("抑止を伴う結果は、危険な見た目にする", () => {
    const tones = Object.fromEntries(outcomeButtons().map((b) => [b.code, b.tone]));
    expect(tones.DO_NOT_CALL).toBe("danger");
    expect(tones.WRONG_NUMBER).toBe("danger");
    expect(tones.WON).not.toBe("danger");
  });

  it("拡張分（アポ確定・番号違い・留守電）は「その他」にまとめる", () => {
    const more = outcomeButtons().filter((b) => b.group === "more");
    expect(more.map((b) => b.code)).toEqual(["APPOINTMENT_SET", "WRONG_NUMBER", "VOICEMAIL"]);
  });
});

describe("nextStepFor: 結果を選んだら、次にやることを出す", () => {
  it("折り返し → 日時の入力が必須", () => {
    expect(nextStepFor("CALLBACK_REQUESTED")).toEqual({ kind: "PICK_DATETIME", required: true });
  });

  it("アポ確定 → 予定日時が必須", () => {
    expect(nextStepFor("APPOINTMENT_SET")).toEqual({ kind: "APPOINTMENT", required: true });
  });

  it("検討 → フォローアップを提案（既定の期日は変更できる）", () => {
    expect(nextStepFor("INTERESTED")).toEqual({ kind: "FOLLOW_UP", required: false });
  });

  it("拒否・番号違い → 抑止の確認を挟む", () => {
    expect(nextStepFor("DO_NOT_CALL")).toEqual({
      kind: "CONFIRM_SUPPRESSION",
      required: true,
      scope: "CONTACT",
    });
    expect(nextStepFor("WRONG_NUMBER")).toEqual({
      kind: "CONFIRM_SUPPRESSION",
      required: true,
      scope: "NUMBER",
    });
  });

  it("成約・不在・留守電 → 追加入力なしで完了できる", () => {
    for (const code of ["WON", "NO_ANSWER", "VOICEMAIL"] as const) {
      expect(nextStepFor(code).kind, code).toBe("NONE");
    }
  });

  it("抑止が必要な結果は、必ず抑止の確認になる（分類を増やしても崩れない）", () => {
    for (const p of OUTCOME_PRESETS) {
      if (p.requiresSuppression !== "NONE") {
        expect(nextStepFor(p.code).kind, p.code).toBe("CONFIRM_SUPPRESSION");
      }
    }
  });
});
