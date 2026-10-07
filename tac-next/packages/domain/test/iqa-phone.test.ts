import { describe, expect, it } from "vitest";
import { toE164 } from "../src/phone.js";

/*
 * 独立 QA（IQA-09, 2026-10-06）。抑止（DNC）は E.164 の文字列の完全一致で照合するので、
 * 同じ番号が 2 通りの E.164 になると、片方で登録した抑止がもう片方に効かない。修正されるまで RED。
 */
describe("IQA-09: 国番号の後ろの国内プレフィックス 0 を落とさない", () => {
  const canonical = toE164("090-0000-0001");

  it.each([
    "+81 (0)90-0000-0001", // 名刺・署名でよく見る表記
    "+81 090-0000-0001",
    "+81-090-0000-0001",
    "0081 090 0000 0001",
    "＋８１（０）９０－００００－０００１", // 全角
  ])("%s は 090-0000-0001 と同じ E.164 になる", (raw) => {
    expect(canonical).toEqual({ ok: true, value: "+819000000001" });
    expect(toE164(raw)).toEqual(canonical);
  });
});
