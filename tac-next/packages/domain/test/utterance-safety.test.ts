import { describe, expect, it } from "vitest";
import { startConversation, transitionPhase } from "../src/conversation.js";
import { applyCustomerUtterance, detectSafetySignals } from "../src/utterance-safety.js";

describe("detectSafetySignals — 顧客の発話から Safety を検知する", () => {
  it.each([
    "もう電話しないでください",
    "電話をかけないでください",
    "二度とかけてこないで",
    "連絡不要です",
    "リストから消してください",
    "迷惑です",
    "営業電話はお断りです",
    "Please stop calling me",
  ])("%s → DO_NOT_CALL", (text) => {
    expect(detectSafetySignals(text)).toContain("DO_NOT_CALL");
  });

  it.each([
    "もう結構です",
    "いえ、結構です",
    "興味ありません",
    "必要ないです",
    "いらないです",
    "ｹｯｺｳﾃﾞｽ",
    "not interested",
  ])("%s → STOP_REQUESTED（再勧誘の禁止）", (text) => {
    expect(detectSafetySignals(text)).toEqual(["STOP_REQUESTED"]);
  });

  it.each(["しつこいんだけど", "消費者センターに相談します", "責任者を出して", "ふざけるな"])(
    "%s → COMPLAINT",
    (text) => {
      expect(detectSafetySignals(text)).toContain("COMPLAINT");
    },
  );

  it.each([
    "はい、その時間で結構です",
    "ソノ時間デケッコウデス",
    "水曜日の午後で結構ですよ",
    "もう少し詳しく聞きたいです",
    "資料を送ってもらえますか",
    "",
  ])("%j は Safety ではない", (text) => {
    expect(detectSafetySignals(text)).toEqual([]);
  });
});

describe("applyCustomerUtterance — AI が話す前に毎回通す", () => {
  const discovery = () => {
    let s = startConversation();
    for (const to of ["PERMISSION", "IDENTIFICATION", "QUALIFICATION", "DISCOVERY"] as const) {
      const r = transitionPhase(s, to);
      if (!r.ok) throw new Error(to);
      s = r.value;
    }
    return s;
  };

  it("「電話しないで」で DO_NOT_CALL に入り、抑止と終話を指示する（AI は話せない）", () => {
    const r = applyCustomerUtterance(discovery(), "もう電話しないで");
    expect(r.state.phase).toBe("DO_NOT_CALL");
    expect(r.effects).toEqual(
      expect.arrayContaining(["END_CONVERSATION", "ADD_SUPPRESSION", "AUDIT"]),
    );
  });

  it("クレームは人へ渡す（controller が HUMAN になる）", () => {
    const r = applyCustomerUtterance(discovery(), "責任者を出して");
    expect(r.state).toEqual({ phase: "COMPLAINT", controller: "HUMAN" });
    expect(r.effects).toContain("REQUEST_HANDOFF");
  });

  it("クレームと拒否が同時なら、優先度の高い DO_NOT_CALL", () => {
    expect(applyCustomerUtterance(discovery(), "しつこい。もう電話しないで").state.phase).toBe(
      "DO_NOT_CALL",
    );
  });

  it("名乗りの直後（DISCLOSURE）でも効く", () => {
    expect(applyCustomerUtterance(startConversation(), "電話しないで").state.phase).toBe(
      "DO_NOT_CALL",
    );
  });

  it("Safety でない発話は状態を変えない", () => {
    const s = discovery();
    expect(applyCustomerUtterance(s, "今は賃貸に住んでいます")).toEqual({ state: s, effects: [] });
  });
});

describe("QA: 「連絡いりません」は明確な連絡拒否", () => {
  it.each(["連絡いりません", "今後の連絡は要りません"])("%s → DO_NOT_CALL", (text) => {
    expect(detectSafetySignals(text)).toContain("DO_NOT_CALL");
  });
});

describe("QA: 曖昧な断り（SOFT_DECLINE）— 抑止はしないが、その通話で説得を続けない", () => {
  const discovery = () => {
    let s = startConversation();
    for (const to of ["PERMISSION", "IDENTIFICATION", "QUALIFICATION", "DISCOVERY"] as const) {
      const r = transitionPhase(s, to);
      if (!r.ok) throw new Error(to);
      s = r.value;
    }
    return s;
  };

  it.each([
    "今はいいです",
    "今日は大丈夫です",
    "また今度",
    "ちょっと忙しい",
    "考えておきます",
    "今は忙しいので",
  ])("%s → 抑止せず WRAP_UP（丁寧に終える）", (text) => {
    expect(detectSafetySignals(text)).toEqual([]);
    const r = applyCustomerUtterance(discovery(), text);
    expect(r.softDecline).toBe(true);
    expect(r.state.phase).toBe("WRAP_UP");
    expect(r.effects).toEqual([]);
  });

  it("名乗り直後（WRAP_UP へ遷移できない段階）でも softDecline は立つ", () => {
    const r = applyCustomerUtterance(startConversation(), "今はいいです");
    expect(r.softDecline).toBe(true);
    expect(r.state.phase).toBe("DISCLOSURE");
  });

  it("「その時間で大丈夫です」など肯定は断りではない", () => {
    for (const t of ["その時間で大丈夫です", "水曜で大丈夫です", "今なら大丈夫ですよ"]) {
      expect(applyCustomerUtterance(discovery(), t).softDecline).not.toBe(true);
    }
  });

  it("明確な拒否は SOFT_DECLINE より強い（抑止される）", () => {
    const r = applyCustomerUtterance(discovery(), "今はいいです、もう電話しないで");
    expect(r.state.phase).toBe("DO_NOT_CALL");
    expect(r.effects).toContain("ADD_SUPPRESSION");
  });
});
