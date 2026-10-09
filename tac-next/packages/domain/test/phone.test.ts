import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { isAllowedCountry, maskE164, toE164 } from "../src/phone.js";

const ok = (raw: string) => {
  const r = toE164(raw);
  if (!r.ok) throw new Error(`expected ok for ${raw}: ${r.error}`);
  return r.value;
};

describe("toE164", () => {
  it.each([
    ["090-1234-5678", "+819012345678"],
    ["03 6899 5464", "+81368995464"],
    ["(03) 6899-5464", "+81368995464"],
    ["+81 90-1234-5678", "+819012345678"],
    ["+1 (659) 210-3801", "+16592103801"],
    ["0081312345678", "+81312345678"],
    ["０９０－１２３４－５６７８", "+819012345678"],
  ])("%s → %s", (raw, expected) => {
    expect(ok(raw)).toBe(expected);
  });

  it.each(["", "   ", "abc", "123", "+81", "090-1234-567a", "+1234567890123456"])(
    "rejects %j",
    (raw) => {
      expect(toE164(raw).ok).toBe(false);
    },
  );

  it("is idempotent: normalizing an E.164 number returns it unchanged", () => {
    // 国番号の後ろに国内の 0 が残った形（+81 0…）は正規の E.164 ではないので、そろえた形に変わる（IQA-09）。
    // それ以外の E.164 はそのまま。どの入力でも、2 回目の正規化で値は変わらない。
    // 0 を落とすと桁が足りなくなる形（例 +82000000 → 7 桁）は、正規の番号にならないので拒否する。
    const trunkZero = /^(81|82|86|44|49|33|61|64)0/;
    fc.assert(
      fc.property(fc.stringMatching(/^[1-9]\d{7,14}$/), (digits) => {
        const e164 = `+${digits}`;
        if (!trunkZero.test(digits)) {
          expect(ok(e164)).toBe(e164);
          expect(ok(ok(e164))).toBe(e164);
          return;
        }
        const first = toE164(e164);
        if (!first.ok) {
          // 0 を落とすと桁が足りない、または 0 が 2 つ以上続く（存在しない番号）
          expect(["INVALID_LENGTH", "INVALID_NUMBER"]).toContain(first.error);
          if (first.error === "INVALID_LENGTH") expect(digits.length - 1).toBeLessThan(8);
          return;
        }
        expect(ok(first.value)).toBe(first.value);
      }),
      // CI・ストレス実行で見つかった反例を毎回確かめる
      { examples: [["82000000"], ["330000000"], ["819012345678"], ["8109012345678"]] },
    );
  });

  it("domestic Japanese numbers and their +81 form normalize to the same key", () => {
    fc.assert(
      fc.property(fc.stringMatching(/^[1-9]\d{8,9}$/), (national) => {
        expect(ok(`0${national}`)).toBe(ok(`+81${national}`));
      }),
    );
  });

  it("formatting characters never change the result", () => {
    const sep = fc.constantFrom(" ", "-", "(", ")", ".", "－", "　");
    fc.assert(
      fc.property(fc.stringMatching(/^0[1-9]\d{8,9}$/), fc.array(sep), (digits, seps) => {
        const chars = digits.split("");
        const noisy = chars.map((c, i) => c + (seps[i % Math.max(seps.length, 1)] ?? "")).join("");
        expect(ok(noisy)).toBe(ok(digits));
      }),
    );
  });
});

describe("isAllowedCountry", () => {
  it("allows only listed country codes (blocks e.g. premium international routes)", () => {
    expect(isAllowedCountry(ok("090-1234-5678"), ["81"])).toBe(true);
    expect(isAllowedCountry(ok("+1 659 210 3801"), ["81"])).toBe(false);
    expect(isAllowedCountry(ok("+1 659 210 3801"), ["81", "1"])).toBe(true);
  });
});

describe("maskE164", () => {
  it("keeps country code and last 4 digits only (PII-safe for logs)", () => {
    expect(maskE164(ok("090-1234-5678"))).toBe("+8190****5678");
  });
});
