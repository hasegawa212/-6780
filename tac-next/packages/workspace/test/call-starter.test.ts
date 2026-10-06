import { describe, expect, it } from "vitest";
import { CallStarter } from "../src/call-starter.js";

const keys = () => {
  let n = 0;
  return () => `key-${++n}`;
};

describe("CallStarter: 発信ボタンの連打で二重発信しない", () => {
  it("応答待ちの間の連打は、同じ冪等キーの重複として扱う", () => {
    const s = new CallStarter(keys());
    const first = s.request("contact-1");
    const second = s.request("contact-1");
    expect(first).toEqual({ key: "key-1", duplicate: false });
    expect(second).toEqual({ key: "key-1", duplicate: true });
  });

  it("100 回連打しても、送るべき要求は1件だけ", () => {
    const s = new CallStarter(keys());
    const results = Array.from({ length: 100 }, () => s.request("contact-1"));
    expect(results.filter((r) => !r.duplicate)).toHaveLength(1);
    expect(new Set(results.map((r) => r.key)).size).toBe(1);
  });

  it("結果が確定したら、次の発信は新しいキーになる", () => {
    const s = new CallStarter(keys());
    s.request("contact-1");
    s.settle("contact-1", "CONFIRMED");
    expect(s.request("contact-1")).toEqual({ key: "key-2", duplicate: false });
  });

  it("タイムアウト（発信されたか不明）では、同じキーを持ち続け、新しいキーを出さない", () => {
    const s = new CallStarter(keys());
    s.request("contact-1");
    s.settle("contact-1", "UNKNOWN");
    // 再送しても同じキー → サーバー側の冪等性で二重発信にならない
    expect(s.request("contact-1")).toEqual({ key: "key-1", duplicate: false });
  });

  it("相手が違えば独立して扱う", () => {
    const s = new CallStarter(keys());
    expect(s.request("contact-1").key).toBe("key-1");
    expect(s.request("contact-2").key).toBe("key-2");
  });
});
