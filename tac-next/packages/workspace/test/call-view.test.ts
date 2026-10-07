import { describe, expect, it } from "vitest";
import {
  blockedReasonLabel,
  callStartSettlement,
  conversationForMode,
  errorKindForCallError,
  suppressionFromApi,
} from "../src/call-view.js";
import { callIndicator, canOfferCall } from "../src/index.js";

/*
 * Phase 9：API の応答 → 画面の表示の対応付け（React に業務ロジックを書かないため、ここに置く）。
 */

describe("suppressionFromApi", () => {
  it("SUPPRESSED は発信禁止（SAVED）、照会失敗・知らない値は UNKNOWN（発信ボタンを出さない）", () => {
    expect(suppressionFromApi("NONE")).toBe("NONE");
    expect(suppressionFromApi("SUPPRESSED")).toBe("SAVED");
    expect(suppressionFromApi("UNKNOWN")).toBe("UNKNOWN");
    expect(suppressionFromApi("something-else")).toBe("UNKNOWN");
    expect(suppressionFromApi(undefined)).toBe("UNKNOWN");
    for (const v of ["SUPPRESSED", "UNKNOWN", "x", undefined]) {
      expect(canOfferCall({ suppression: suppressionFromApi(v), activeCall: false })).toBe(false);
    }
  });
});

describe("callStartSettlement", () => {
  it("サーバーが結果を確定させた応答は CONFIRMED（次は新しい冪等キー）", () => {
    for (const status of [200, 201, 400, 401, 403, 404, 409, 422, 502]) {
      expect(callStartSettlement(status)).toBe("CONFIRMED");
    }
  });

  it("発信されたか分からない応答（504・応答なし・想定外の 5xx）は UNKNOWN（同じキーを持ち続ける）", () => {
    for (const status of [504, 500, 503, undefined]) {
      expect(callStartSettlement(status)).toBe("UNKNOWN");
    }
  });
});

describe("errorKindForCallError", () => {
  it.each([
    ["CONTACT_SUPPRESSED", "CALL_BLOCKED_SUPPRESSED"],
    ["OUTBOUND_STOPPED", "CALL_BLOCKED_STOPPED"],
    ["PROVIDER_TIMEOUT", "CALL_START_UNKNOWN"],
    [undefined, "CALL_START_UNKNOWN"],
    ["PROVIDER_ERROR", "CALL_START_FAILED"],
    ["DAILY_CAP_REACHED", "CALL_START_FAILED"],
  ] as const)("%s → %s", (code, kind) => {
    expect(errorKindForCallError(code)).toBe(kind);
  });
});

describe("blockedReasonLabel", () => {
  it("発信判定の理由を日本語で説明する。知らない理由はコードのまま出さず汎用の文にする", () => {
    expect(blockedReasonLabel("OUTSIDE_CALLING_WINDOW")).toBe("発信できる時間帯の外です");
    expect(blockedReasonLabel("OUTBOUND_DISABLED_BY_CONFIG")).toBe(
      "この環境では発信が無効になっています（設定）",
    );
    expect(blockedReasonLabel("SOMETHING_NEW")).toBe("発信の条件を満たしていません");
  });
});

describe("conversationForMode", () => {
  it("人が掛けた通話は、つながったら「あなたが対応中」（AI が話していると出さない）", () => {
    const conversation = conversationForMode("HUMAN_DIALED");
    expect(conversation.controller).toBe("HUMAN");
    expect(
      callIndicator({ status: "IN_PROGRESS", conversation, connection: "CONNECTED", speaker: "AI" })
        .label,
    ).toBe("あなたが対応中");
  });

  it("AI 音声の通話は AI が担当で始まる", () => {
    expect(conversationForMode("AI_VOICE").controller).toBe("AI");
  });
});

describe("Codex P1: 発信されたか分からない失敗（PROVIDER_UNCERTAIN）", () => {
  it("タイムアウトと同じく「確認できませんでした」の文言にする", () => {
    expect(errorKindForCallError("PROVIDER_UNCERTAIN")).toBe("CALL_START_UNKNOWN");
  });
});
