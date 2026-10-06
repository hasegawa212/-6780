import { toE164 } from "@tac/domain";
import { describe, expect, it } from "vitest";
import {
  MockTelephonyProvider,
  normalizeMockStatus,
  type SimulatedScenario,
  signMockWebhook,
  verifyMockWebhook,
} from "../src/index.js";

/*
 * Phase 8：テスト用の電話シミュレーター（VOICE.md の表）。本物の電話はかけない。
 * 発信のたびにシナリオに沿った Webhook のイベント列を作り、テストが好きな順序・回数で届ける。
 */

const e164 = (raw: string) => {
  const r = toE164(raw);
  if (!r.ok) throw new Error(raw);
  return r.value;
};

const request = (key: string, callId = `call-${key}`) => ({
  idempotencyKey: key,
  to: e164("090-0000-0001"),
  from: e164("03-0000-0000"),
  callId,
  disclosureText: "こちらは株式会社サンプル不動産の佐藤です。",
});

const statuses = (p: MockTelephonyProvider, providerCallId: string) =>
  p.takeEvents(providerCallId).map((e) => e.status);

describe("シナリオごとのイベント列", () => {
  it.each<[SimulatedScenario, string[]]>([
    ["ANSWER", ["ringing", "in-progress", "completed"]],
    ["BUSY", ["busy"]],
    ["REJECT", ["ringing", "failed"]],
    ["NO_ANSWER", ["ringing", "no-answer"]],
    ["DISCONNECT", ["ringing", "in-progress", "failed"]],
    ["VOICEMAIL", ["ringing", "in-progress", "completed"]],
  ])("%s → %j", async (scenario, expected) => {
    const p = new MockTelephonyProvider();
    p.nextScenario(scenario);
    const placed = await p.createCall(request("k"));
    expect(statuses(p, placed.providerCallId)).toEqual(expected);
  });

  it("留守電は「機械が応答した」ことをイベントに含める", async () => {
    const p = new MockTelephonyProvider();
    p.nextScenario("VOICEMAIL");
    const placed = await p.createCall(request("k"));
    const answered = p.takeEvents(placed.providerCallId).find((e) => e.status === "in-progress");
    expect(answered?.answeredBy).toBe("machine");
  });

  it("イベントには発信時の callId と一意な event_id が入る", async () => {
    const p = new MockTelephonyProvider();
    const placed = await p.createCall(request("k", "our-call-id"));
    const events = p.takeEvents(placed.providerCallId);
    expect(new Set(events.map((e) => e.eventId)).size).toBe(events.length);
    expect(events.every((e) => e.callId === "our-call-id")).toBe(true);
    expect(p.takeEvents(placed.providerCallId)).toEqual([]);
  });

  it("PROVIDER_ERROR は発信の API そのものが失敗する（イベントは出ない）", async () => {
    const p = new MockTelephonyProvider();
    p.nextScenario("PROVIDER_ERROR");
    await expect(p.createCall(request("k"))).rejects.toThrow(/503/);
    expect(p.placed).toHaveLength(1);
  });

  it("シナリオの指定は次の 1 件だけに効き、その後は ANSWER に戻る", async () => {
    const p = new MockTelephonyProvider();
    p.nextScenario("BUSY");
    const first = await p.createCall(request("k1"));
    const second = await p.createCall(request("k2"));
    expect(statuses(p, first.providerCallId)).toEqual(["busy"]);
    expect(statuses(p, second.providerCallId)).toEqual(["ringing", "in-progress", "completed"]);
  });

  it("idPrefix を指定すると、再起動しても providerCallId が重複しない", async () => {
    const a = new MockTelephonyProvider({ idPrefix: "run-a" });
    const b = new MockTelephonyProvider({ idPrefix: "run-b" });
    expect((await a.createCall(request("k"))).providerCallId).not.toBe(
      (await b.createCall(request("k"))).providerCallId,
    );
  });
});

describe("状態の正規化（プロバイダの語彙 → CallStatus）", () => {
  it.each([
    ["queued", "DIALING"],
    ["ringing", "RINGING"],
    ["in-progress", "IN_PROGRESS"],
    ["completed", "ENDED"],
    ["busy", "BUSY"],
    ["failed", "FAILED"],
    ["no-answer", "NO_ANSWER"],
    ["canceled", "CANCELED"],
  ] as const)("%s → %s", (raw, status) => {
    expect(normalizeMockStatus(raw)).toBe(status);
  });

  it("知らない状態は undefined（推測で状態を作らない）", () => {
    expect(normalizeMockStatus("answered-by-alien")).toBeUndefined();
  });
});

describe("Webhook の署名", () => {
  const secret = "test-secret-at-least-32-characters-long";
  const body = JSON.stringify({ eventId: "e1" });
  const now = new Date("2026-10-05T01:00:00Z");

  it("署名したものは検証に通る", () => {
    const header = signMockWebhook(secret, body, now);
    expect(verifyMockWebhook(secret, body, header, now)).toBe(true);
  });

  it("本文・秘密鍵・署名が違えば通らない", () => {
    const header = signMockWebhook(secret, body, now);
    expect(verifyMockWebhook(secret, `${body} `, header, now)).toBe(false);
    expect(verifyMockWebhook(`${secret}x`, body, header, now)).toBe(false);
    expect(verifyMockWebhook(secret, body, header.replace(/v1=./, "v1=0"), now)).toBe(false);
    expect(verifyMockWebhook(secret, body, undefined, now)).toBe(false);
    expect(verifyMockWebhook(secret, body, "garbage", now)).toBe(false);
  });

  it("5 分より古い・未来すぎるタイムスタンプは通らない（再送攻撃の対策）", () => {
    const header = signMockWebhook(secret, body, now);
    const later = new Date(now.getTime() + 5 * 60 * 1000 + 1000);
    const earlier = new Date(now.getTime() - 5 * 60 * 1000 - 1000);
    expect(verifyMockWebhook(secret, body, header, later)).toBe(false);
    expect(verifyMockWebhook(secret, body, header, earlier)).toBe(false);
  });
});
