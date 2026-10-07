import { toE164 } from "@tac/domain";
import { describe, expect, it } from "vitest";
import {
  createTelephonyProvider,
  MockTelephonyProvider,
  ProviderNotAllowedError,
} from "../src/index.js";

const e164 = (raw: string) => {
  const r = toE164(raw);
  if (!r.ok) throw new Error(raw);
  return r.value;
};

const request = (key: string) => ({
  idempotencyKey: key,
  to: e164("090-0000-0001"),
  from: e164("03-0000-0000"),
  callId: "call-1",
  disclosureText: "こちらは株式会社サンプル不動産の佐藤です。",
});

describe("MockTelephonyProvider", () => {
  it("never places a real call and records what it was asked to do", async () => {
    const p = new MockTelephonyProvider();
    const call = await p.createCall(request("k1"));
    expect(call).toEqual({ provider: "mock", providerCallId: "MOCK-1", status: "DIALING" });
    expect(p.placed).toHaveLength(1);
  });

  it("is idempotent by key, like a provider that honours idempotency", async () => {
    const p = new MockTelephonyProvider();
    const a = await p.createCall(request("same"));
    const b = await p.createCall(request("same"));
    expect(b.providerCallId).toBe(a.providerCallId);
    expect(p.placed).toHaveLength(1);
  });

  it("can simulate status progression and ending a call", async () => {
    const p = new MockTelephonyProvider();
    const { providerCallId } = await p.createCall(request("k2"));
    p.setStatus(providerCallId, "IN_PROGRESS");
    expect((await p.getCall(providerCallId)).status).toBe("IN_PROGRESS");
    await p.endCall(providerCallId);
    expect((await p.getCall(providerCallId)).status).toBe("ENDED");
  });

  it("records transfers (human handoff)", async () => {
    const p = new MockTelephonyProvider();
    const { providerCallId } = await p.createCall(request("k3"));
    await p.transferCall(providerCallId, { kind: "PHONE", to: e164("090-0000-0099") });
    expect(p.transfers).toEqual([{ providerCallId, to: "+819000000099" }]);
  });
});

describe("createTelephonyProvider", () => {
  it("builds the mock provider", () => {
    expect(createTelephonyProvider({ appEnv: "test", provider: "mock" }).name).toBe("mock");
  });

  it("作るたびに通話 ID の接頭辞が変わる（再起動しても providerCallId が重複しない）", async () => {
    const req = {
      idempotencyKey: "k",
      to: e164("090-0000-0001"),
      from: e164("03-0000-0000"),
      callId: "c",
      disclosureText: "x",
    };
    const a = await createTelephonyProvider({ appEnv: "test", provider: "mock" }).createCall(req);
    const b = await createTelephonyProvider({ appEnv: "test", provider: "mock" }).createCall(req);
    expect(a.providerCallId).not.toBe(b.providerCallId);
  });

  it("refuses to build a real provider in the test environment", () => {
    expect(() => createTelephonyProvider({ appEnv: "test", provider: "twilio" })).toThrow(
      ProviderNotAllowedError,
    );
  });

  it("refuses to build a real provider in local development too", () => {
    expect(() => createTelephonyProvider({ appEnv: "local", provider: "twilio" })).toThrow(
      ProviderNotAllowedError,
    );
  });

  it("reports real adapters that are not implemented yet instead of faking them", () => {
    expect(() => createTelephonyProvider({ appEnv: "staging", provider: "openai-sip" })).toThrow(
      /not implemented/,
    );
  });
});
