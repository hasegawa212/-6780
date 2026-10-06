import { describe, expect, it } from "vitest";
import { bucketFollowUps, type FollowUpItem } from "../src/follow-up-buckets.js";

const jst = (local: string) => new Date(`${local}+09:00`);
const item = (id: string, due: string, completed = false): FollowUpItem => ({
  id,
  dueAt: jst(due),
  completed,
});

describe("bucketFollowUps: 期限切れ・今日・今後・完了に分ける（組織のタイムゾーンで）", () => {
  const now = jst("2026-10-06T10:00:00");

  it("基本の4分類", () => {
    const b = bucketFollowUps(
      [
        item("late", "2026-10-05T18:00:00"),
        item("earlier-today", "2026-10-06T09:00:00"),
        item("later-today", "2026-10-06T17:00:00"),
        item("tomorrow", "2026-10-07T09:00:00"),
        item("done", "2026-10-05T09:00:00", true),
      ],
      now,
      "Asia/Tokyo",
    );
    expect(b.overdue.map((i) => i.id)).toEqual(["late", "earlier-today"]);
    expect(b.today.map((i) => i.id)).toEqual(["later-today"]);
    expect(b.upcoming.map((i) => i.id)).toEqual(["tomorrow"]);
    expect(b.completed.map((i) => i.id)).toEqual(["done"]);
  });

  it("「今日」は UTC ではなく組織の現地日付で決める（JST 0:30 は UTC では前日）", () => {
    const b = bucketFollowUps(
      [item("tonight", "2026-10-06T23:59:00"), item("next-midnight", "2026-10-07T00:00:00")],
      jst("2026-10-06T00:30:00"),
      "Asia/Tokyo",
    );
    expect(b.today.map((i) => i.id)).toEqual(["tonight"]);
    expect(b.upcoming.map((i) => i.id)).toEqual(["next-midnight"]);
  });

  it("各分類は期日の早い順、期限切れは古い順に並ぶ", () => {
    const b = bucketFollowUps(
      [item("b", "2026-10-09T09:00:00"), item("a", "2026-10-08T09:00:00")],
      now,
      "Asia/Tokyo",
    );
    expect(b.upcoming.map((i) => i.id)).toEqual(["a", "b"]);
  });

  it("完了済みは、期限を過ぎていても期限切れに入れない", () => {
    const b = bucketFollowUps([item("x", "2026-01-01T00:00:00", true)], now, "Asia/Tokyo");
    expect(b.overdue).toEqual([]);
    expect(b.completed.length).toBe(1);
  });

  it("不正なタイムゾーンは例外にする（黙って UTC にしない）", () => {
    expect(() => bucketFollowUps([], now, "Mars/Olympus")).toThrow(RangeError);
  });
});
