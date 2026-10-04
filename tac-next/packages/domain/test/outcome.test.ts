import { describe, expect, it } from "vitest";
import type { CallingWindowPolicy } from "../src/calling-window.js";
import { OUTCOME_PRESETS, outcomeFromLabel, planOutcome } from "../src/outcome.js";

const JP: CallingWindowPolicy = {
  timeZone: "Asia/Tokyo",
  startMinute: 9 * 60,
  endMinute: 20 * 60,
  weekdays: [1, 2, 3, 4, 5, 6],
  holidays: [],
};
const jst = (local: string) => new Date(`${local}+09:00`);
const now = jst("2026-10-05T15:00:00"); // 月曜 15:00
const base = { now, window: JP, attempt: 1, maxAttempts: 3 };

describe("outcome presets (current five buttons stay usable)", () => {
  it.each([
    ["成約", "WON"],
    ["検討", "INTERESTED"],
    ["折り返し", "CALLBACK_REQUESTED"],
    ["不在", "NO_ANSWER"],
    ["拒否", "DO_NOT_CALL"],
  ])("%s → %s", (label, code) => {
    expect(outcomeFromLabel(label)).toBe(code);
  });

  it("unknown labels are rejected rather than stored as free text", () => {
    expect(outcomeFromLabel("たぶん")).toBeUndefined();
  });

  it("only DO_NOT_CALL requires contact-level suppression", () => {
    const suppressing = OUTCOME_PRESETS.filter((p) => p.requiresSuppression === "CONTACT");
    expect(suppressing.map((p) => p.code)).toEqual(["DO_NOT_CALL"]);
  });
});

describe("planOutcome", () => {
  it("DO_NOT_CALL suppresses and creates no follow-up", () => {
    const r = planOutcome("DO_NOT_CALL", base);
    expect(r.ok && r.value).toEqual({
      suppress: "CONTACT",
      followUp: undefined,
      nextAction: "DO_NOT_CONTACT",
    });
  });

  it("INTERESTED schedules a follow-up 3 days later inside the calling window", () => {
    const r = planOutcome("INTERESTED", base);
    expect(r.ok && r.value.followUp).toEqual({
      kind: "FOLLOW_UP",
      dueAt: jst("2026-10-08T15:00:00"),
    });
  });

  it("CALLBACK_REQUESTED requires a future callback time and uses it as the due date", () => {
    expect(planOutcome("CALLBACK_REQUESTED", base)).toEqual({
      ok: false,
      error: "CALLBACK_TIME_REQUIRED",
    });
    expect(
      planOutcome("CALLBACK_REQUESTED", { ...base, callbackAt: jst("2026-10-05T14:00:00") }),
    ).toEqual({ ok: false, error: "CALLBACK_TIME_IN_PAST" });
    const at = jst("2026-10-06T11:00:00");
    const r = planOutcome("CALLBACK_REQUESTED", { ...base, callbackAt: at });
    expect(r.ok && r.value.followUp).toEqual({ kind: "CALLBACK", dueAt: at });
  });

  it("NO_ANSWER retries the next day within the window, then stops for human review", () => {
    const evening = { ...base, now: jst("2026-10-10T19:30:00") }; // 土曜の夜
    const r = planOutcome("NO_ANSWER", evening);
    // 翌日は日曜で不可 → 月曜 9:00
    expect(r.ok && r.value.followUp).toEqual({
      kind: "RETRY",
      dueAt: jst("2026-10-12T09:00:00"),
    });
    const exhausted = planOutcome("NO_ANSWER", { ...base, attempt: 3 });
    expect(exhausted.ok && exhausted.value).toEqual({
      suppress: "NONE",
      followUp: undefined,
      nextAction: "HUMAN_REVIEW",
    });
  });

  it("WRONG_NUMBER suppresses only the number and asks for review", () => {
    const r = planOutcome("WRONG_NUMBER", base);
    expect(r.ok && r.value.suppress).toBe("NUMBER");
    expect(r.ok && r.value.nextAction).toBe("HUMAN_REVIEW");
  });

  it("WON needs no follow-up", () => {
    const r = planOutcome("WON", base);
    expect(r.ok && r.value).toEqual({ suppress: "NONE", followUp: undefined, nextAction: "NONE" });
  });
});
