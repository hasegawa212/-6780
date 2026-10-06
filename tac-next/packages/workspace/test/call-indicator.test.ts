import { CALL_STATUSES, startConversation, takeOver } from "@tac/domain";
import { describe, expect, it } from "vitest";
import { aiActivityView, aiConfidenceNotice, callIndicator } from "../src/call-indicator.js";

const ai = startConversation();
const human = takeOver(ai);

describe("callIndicator: 通話状態は色だけに頼らない", () => {
  it("すべての通話状態に、文字ラベルとアイコンがある", () => {
    for (const status of CALL_STATUSES) {
      const v = callIndicator({ status, conversation: ai, connection: "CONNECTED" });
      expect(v.label.length, status).toBeGreaterThan(0);
      expect(v.icon.length, status).toBeGreaterThan(0);
      expect(v.announce, status).toContain(v.label);
    }
  });

  it("状態ごとにラベルが区別できる（同じ文言に潰れない）", () => {
    const labels = CALL_STATUSES.map(
      (status) => callIndicator({ status, conversation: ai, connection: "CONNECTED" }).label,
    );
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("発信要求〜ダイヤル中は「発信中」、呼び出し中は「呼び出し中」", () => {
    expect(
      callIndicator({ status: "REQUESTED", conversation: ai, connection: "CONNECTED" }).label,
    ).toBe("発信準備中");
    expect(
      callIndicator({ status: "DIALING", conversation: ai, connection: "CONNECTED" }).label,
    ).toBe("発信中");
    expect(
      callIndicator({ status: "RINGING", conversation: ai, connection: "CONNECTED" }).label,
    ).toBe("呼び出し中");
  });

  it("通話中は、誰が話しているか（AI / お客様 / あなた）を出す", () => {
    const base = { status: "IN_PROGRESS" as const, connection: "CONNECTED" as const };
    expect(callIndicator({ ...base, conversation: ai, speaker: "AI" }).label).toBe(
      "AI が話しています",
    );
    expect(callIndicator({ ...base, conversation: ai, speaker: "CUSTOMER" }).label).toBe(
      "お客様が話しています",
    );
    expect(callIndicator({ ...base, conversation: ai }).label).toBe("お客様とつながりました");
    expect(callIndicator({ ...base, conversation: human }).label).toBe("あなたが対応中");
  });

  it("引き継ぎ後は、AI の発話イベントが届いても「AI が話しています」と出さない", () => {
    const v = callIndicator({
      status: "IN_PROGRESS",
      conversation: human,
      speaker: "AI",
      connection: "CONNECTED",
    });
    expect(v.label).toBe("あなたが対応中");
  });

  it("通話中にリアルタイム接続が切れたら、最優先で危険表示にする（トーストに任せない）", () => {
    const v = callIndicator({ status: "IN_PROGRESS", conversation: ai, connection: "LOST" });
    expect(v.tone).toBe("danger");
    expect(v.blocking).toBe(true);
    expect(v.label).toBe("接続が切れました");
  });

  it("終端状態では live=false、通話中は live=true", () => {
    expect(
      callIndicator({ status: "IN_PROGRESS", conversation: ai, connection: "CONNECTED" }).live,
    ).toBe(true);
    for (const status of ["ENDED", "FAILED", "NO_ANSWER", "BUSY", "CANCELED"] as const) {
      expect(callIndicator({ status, conversation: ai, connection: "CONNECTED" }).live).toBe(false);
    }
  });

  it("通話が終わった後の切断は、危険表示にしない", () => {
    const v = callIndicator({ status: "ENDED", conversation: ai, connection: "LOST" });
    expect(v.blocking).toBe(false);
  });
});

describe("aiActivityView: AI が何をしているか", () => {
  it("人が引き継いだ後は、届いたイベントに関係なく「停止」と出す", () => {
    expect(aiActivityView("SPEAKING", human).label).toBe("停止中（あなたが対応中）");
    expect(aiActivityView("THINKING", human).label).toBe("停止中（あなたが対応中）");
  });

  it("AI が担当中は、活動ごとに具体的な文言を出す", () => {
    expect(aiActivityView("LISTENING", ai).label).toBe("聞いています");
    expect(aiActivityView("SEARCHING_KNOWLEDGE", ai).label).toBe("資料を確認しています");
    expect(aiActivityView("CHECKING_CALENDAR", ai).label).toBe("空き日程を確認しています");
    expect(aiActivityView("PREPARING_HANDOFF", ai).label).toBe(
      "担当者への引き継ぎを準備しています",
    );
  });
});

describe("aiConfidenceNotice: 確信度は必要なときだけ出す", () => {
  it("確信度が低いときは、人の確認をすすめる", () => {
    expect(aiConfidenceNotice(0.4)).toBe("人の確認をおすすめします");
  });
  it("十分に高いときは何も出さない", () => {
    expect(aiConfidenceNotice(0.9)).toBeUndefined();
  });
  it("不明・不正な値は低いものとして扱う（安全側）", () => {
    expect(aiConfidenceNotice(Number.NaN)).toBe("人の確認をおすすめします");
    expect(aiConfidenceNotice(undefined)).toBe("人の確認をおすすめします");
  });
});
