import fc from "fast-check";
import { describe, expect, it } from "vitest";
import {
  type CallingWindowPolicy,
  isWithinCallingWindow,
  nextWindowStart,
} from "../src/calling-window.js";

// 日本向けの初期値：月〜土 9:00〜20:00、日曜・祝日は不可
const JP: CallingWindowPolicy = {
  timeZone: "Asia/Tokyo",
  startMinute: 9 * 60,
  endMinute: 20 * 60,
  weekdays: [1, 2, 3, 4, 5, 6],
  holidays: ["2026-11-03"],
};

const jst = (local: string) => new Date(`${local}+09:00`);

describe("isWithinCallingWindow", () => {
  it.each([
    ["2026-10-05T09:00:00", true], // 月曜の開始ちょうど（含む）
    ["2026-10-05T19:59:00", true],
    ["2026-10-05T20:00:00", false], // 終了ちょうど（含まない）
    ["2026-10-05T08:59:00", false],
    ["2026-10-04T12:00:00", false], // 日曜
    ["2026-11-03T12:00:00", false], // 祝日（文化の日）
    ["2026-10-10T10:00:00", true], // 土曜
  ])("%s JST → %s", (local, expected) => {
    expect(isWithinCallingWindow(jst(local), JP)).toBe(expected);
  });

  it("judges by the callee's time zone, not the server's", () => {
    const ny: CallingWindowPolicy = { ...JP, timeZone: "America/New_York", holidays: [] };
    // 月曜 10:00 JST = 日曜 21:00 EDT → ニューヨークの相手には掛けない
    expect(isWithinCallingWindow(jst("2026-10-05T10:00:00"), ny)).toBe(false);
  });

  it("supports minute-level windows (e.g. until 21:30)", () => {
    const late = { ...JP, endMinute: 21 * 60 + 30 };
    expect(isWithinCallingWindow(jst("2026-10-05T21:29:00"), late)).toBe(true);
    expect(isWithinCallingWindow(jst("2026-10-05T21:30:00"), late)).toBe(false);
  });
});

describe("nextWindowStart", () => {
  it("returns the same instant when already inside the window", () => {
    const t = jst("2026-10-05T10:15:00");
    expect(nextWindowStart(t, JP).toISOString()).toBe(t.toISOString());
  });

  it("moves an early-morning time to 09:00 the same day", () => {
    expect(nextWindowStart(jst("2026-10-05T07:00:00"), JP).toISOString()).toBe(
      jst("2026-10-05T09:00:00").toISOString(),
    );
  });

  it("skips the evening, Sunday and holidays", () => {
    // 土曜 21:00 → 日曜は不可 → 月曜 9:00
    expect(nextWindowStart(jst("2026-10-10T21:00:00"), JP).toISOString()).toBe(
      jst("2026-10-12T09:00:00").toISOString(),
    );
    // 祝日の前日の夜 → 祝日を飛ばして翌日 9:00
    expect(nextWindowStart(jst("2026-11-02T22:00:00"), JP).toISOString()).toBe(
      jst("2026-11-04T09:00:00").toISOString(),
    );
  });

  it("always lands inside the window and never in the past", () => {
    const instant = fc.date({
      min: new Date("2026-01-01T00:00:00Z"),
      max: new Date("2027-12-31T00:00:00Z"),
      noInvalidDate: true,
    });
    const zone = fc.constantFrom("Asia/Tokyo", "America/New_York", "Europe/London");
    fc.assert(
      fc.property(instant, zone, (t, timeZone) => {
        const policy = { ...JP, timeZone };
        const next = nextWindowStart(t, policy);
        expect(next.getTime()).toBeGreaterThanOrEqual(t.getTime());
        expect(isWithinCallingWindow(next, policy)).toBe(true);
      }),
    );
  });
});

describe("QA: 不正なタイムゾーン・境界", () => {
  const base = {
    startMinute: 9 * 60,
    endMinute: 20 * 60,
    weekdays: [0, 1, 2, 3, 4, 5, 6],
    holidays: [],
  };
  it("unknown time zone is treated as outside the window (fail closed, never throws)", () => {
    expect(isWithinCallingWindow(new Date(), { ...base, timeZone: "Mars/Olympus_Mons" })).toBe(
      false,
    );
  });
  it.each([
    ["2026-10-05T19:59:00+09:00", true],
    ["2026-10-05T20:00:00+09:00", false],
    ["2026-10-05T23:59:00+09:00", false],
    ["2026-10-06T00:00:00+09:00", false],
    ["2026-10-06T09:00:00+09:00", true],
  ])("Asia/Tokyo %s → %s", (iso, expected) => {
    expect(isWithinCallingWindow(new Date(iso), { ...base, timeZone: "Asia/Tokyo" })).toBe(
      expected,
    );
  });
  it("DST spring-forward day in New York: 09:00 local is inside, 08:59 is outside", () => {
    const ny = { ...base, timeZone: "America/New_York" };
    // 2027-03-14 は米国の夏時間開始日（02:00→03:00）。09:00 EDT = 13:00Z
    expect(isWithinCallingWindow(new Date("2027-03-14T13:00:00Z"), ny)).toBe(true);
    expect(isWithinCallingWindow(new Date("2027-03-14T12:59:00Z"), ny)).toBe(false);
  });
  it("leap day is handled as a normal date", () => {
    expect(
      isWithinCallingWindow(new Date("2028-02-29T10:00:00+09:00"), {
        ...base,
        timeZone: "Asia/Tokyo",
      }),
    ).toBe(true);
  });
});
