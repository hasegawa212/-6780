import { describe, expect, it } from "vitest";
import { canOfferCall, suppressionBanner } from "../src/suppression-banner.js";

describe("suppressionBanner: 発信禁止は普通のタグではなく、重大な状態として出す", () => {
  it("保存できたら、今後は発信できないことを明示する", () => {
    const b = suppressionBanner("SAVED");
    expect(b?.title).toBe("発信禁止");
    expect(b?.body).toBe("今後、この相手には発信できません。");
    expect(b?.tone).toBe("danger");
  });

  it("保存に失敗しても、発信は止めたままにする（fail-safe）", () => {
    const b = suppressionBanner("SAVE_FAILED");
    expect(b?.title).toBe("発信禁止を保存できませんでした");
    expect(b?.body).toContain("確認が終わるまで、この相手への発信は止めています");
    expect(b?.action).toBe("もう一度保存する");
  });

  it("未登録なら何も出さない", () => {
    expect(suppressionBanner("NONE")).toBeUndefined();
  });
});

describe("canOfferCall: 発信ボタンを出してよいか", () => {
  it("抑止の状態が確定して NONE のときだけ発信できる", () => {
    expect(canOfferCall({ suppression: "NONE", activeCall: false })).toBe(true);
  });

  it.each(["SAVING", "SAVED", "SAVE_FAILED", "UNKNOWN"] as const)(
    "抑止が %s のときは発信させない",
    (suppression) => {
      expect(canOfferCall({ suppression, activeCall: false })).toBe(false);
    },
  );

  it("すでに通話中の相手には発信させない", () => {
    expect(canOfferCall({ suppression: "NONE", activeCall: true })).toBe(false);
  });
});
