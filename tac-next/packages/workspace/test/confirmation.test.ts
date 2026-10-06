import { describe, expect, it } from "vitest";
import { confirmationFor, describeConfirmation } from "../src/confirmation.js";

describe("confirmationFor: 何でも確認ダイアログにしない", () => {
  it("通話終了は即時（緊急時に遅らせない）", () => {
    expect(confirmationFor("END_CALL")).toBe("IMMEDIATE");
  });

  it("メモの保存は即時、スキップは取り消しで対応", () => {
    expect(confirmationFor("SAVE_NOTE")).toBe("IMMEDIATE");
    expect(confirmationFor("SKIP_LEAD")).toBe("UNDO");
  });

  it("発信禁止の登録・転送は確認を挟む", () => {
    expect(confirmationFor("MARK_DNC")).toBe("CONFIRM");
    expect(confirmationFor("TRANSFER")).toBe("CONFIRM");
  });

  it("全発信の停止は確認を挟む（通常操作から誤って押さないため。ただし入力は求めない）", () => {
    expect(confirmationFor("ENGAGE_KILL_SWITCH")).toBe("CONFIRM");
  });

  it("発信禁止の解除・全発信の再開・自動発信 ON は、取り消しに頼らず強い確認にする", () => {
    expect(confirmationFor("REMOVE_DNC")).toBe("STRONG_CONFIRM");
    expect(confirmationFor("RELEASE_KILL_SWITCH")).toBe("STRONG_CONFIRM");
    expect(confirmationFor("ENABLE_AUTO_DIAL")).toBe("STRONG_CONFIRM");
  });
});

describe("describeConfirmation: 何を・誰に・どうなるか を出す", () => {
  it("強い確認では、対象名の入力を求める", () => {
    const d = describeConfirmation("REMOVE_DNC", "山田 太郎");
    expect(d.level).toBe("STRONG_CONFIRM");
    expect(d.typeToConfirm).toBe("山田 太郎");
    expect(d.title).toContain("発信禁止を解除");
    expect(d.consequence).toContain("再び発信できる");
  });

  it("確認では対象と結果を出すが、入力は求めない", () => {
    const d = describeConfirmation("MARK_DNC", "山田 太郎");
    expect(d.typeToConfirm).toBeUndefined();
    expect(d.target).toBe("山田 太郎");
    expect(d.consequence).toContain("今後、発信できなくなります");
  });
});
