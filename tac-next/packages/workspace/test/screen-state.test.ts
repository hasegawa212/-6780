import { describe, expect, it } from "vitest";
import { errorCopy, screenState } from "../src/screen-state.js";

describe("screenState: 主要画面の状態を一つに決める", () => {
  const base = { online: true, loading: false, auth: "OK" as const, itemCount: 3 };

  it("未ログインは最優先", () => {
    expect(screenState({ ...base, auth: "UNAUTHENTICATED", online: false })).toBe("UNAUTHORIZED");
  });
  it("権限がなければ FORBIDDEN", () => {
    expect(screenState({ ...base, auth: "FORBIDDEN" })).toBe("FORBIDDEN");
  });
  it("オフラインで手元にデータもなければ OFFLINE", () => {
    expect(screenState({ ...base, online: false, itemCount: undefined })).toBe("OFFLINE");
  });
  it("初回読み込み中は LOADING", () => {
    expect(screenState({ ...base, loading: true, itemCount: undefined })).toBe("LOADING");
  });
  it("エラーで何も出せなければ ERROR、一部だけ出せれば PARTIAL", () => {
    expect(screenState({ ...base, error: true, itemCount: undefined })).toBe("ERROR");
    expect(screenState({ ...base, error: true, itemCount: 2 })).toBe("PARTIAL");
  });
  it("0 件は EMPTY、それ以外は SUCCESS", () => {
    expect(screenState({ ...base, itemCount: 0 })).toBe("EMPTY");
    expect(screenState(base)).toBe("SUCCESS");
  });
});

describe("errorCopy: 何が起きたか・何が安全か・次に何をするか", () => {
  it("発信の失敗：発信はされていないと明言する", () => {
    const c = errorCopy("CALL_START_FAILED");
    expect(c.what).toBe("発信できませんでした。");
    expect(c.safe).toBe("発信はされていません。");
    expect(c.next).toContain("もう一度");
  });

  it("発信のタイムアウト：発信済みかどうか分からないので、自動で再発信しないと伝える", () => {
    const c = errorCopy("CALL_START_UNKNOWN");
    expect(c.what).toContain("確認できませんでした");
    expect(c.safe).toContain("自動では再発信しません");
    expect(c.next).toContain("通話履歴");
  });

  it("発信禁止で止められた：理由をはっきり出す", () => {
    const c = errorCopy("CALL_BLOCKED_SUPPRESSED");
    expect(c.what).toContain("発信禁止");
    expect(c.safe).toBe("発信はされていません。");
  });

  it("「エラーが発生しました」のような中身のない文言を使わない", () => {
    for (const kind of [
      "CALL_START_FAILED",
      "CALL_START_UNKNOWN",
      "CALL_BLOCKED_SUPPRESSED",
      "CALL_BLOCKED_STOPPED",
      "OUTCOME_SAVE_FAILED",
      "REALTIME_LOST",
    ] as const) {
      const c = errorCopy(kind);
      for (const s of [c.what, c.safe, c.next]) {
        expect(s, kind).not.toMatch(/エラーが発生しました|Something went wrong/);
        expect(s.length, kind).toBeGreaterThan(4);
      }
    }
  });
});
