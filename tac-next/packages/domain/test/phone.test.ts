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
    fc.assert(
      fc.property(fc.stringMatching(/^[1-9]\d{7,14}$/), (digits) => {
        const e164 = `+${digits}`;
        expect(ok(e164)).toBe(e164);
        expect(ok(ok(e164))).toBe(e164);
      }),
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
