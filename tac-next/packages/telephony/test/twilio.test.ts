import { ProviderRejectedError, ProviderTimeoutError } from "@tac/application";
import { toE164 } from "@tac/domain";
// 公式 SDK は「署名の正解」を出す参照実装としてだけ使う（実行時の依存にはしない）
import twilio from "twilio";
import { describe, expect, it } from "vitest";
import {
  createTelephonyProvider,
  normalizeTwilioStatus,
  TWILIO_WEBHOOK_PATH,
  TwilioTelephonyProvider,
  twilioSignature,
  verifyTwilioSignature,
} from "../src/index.js";

const e164 = (raw: string) => {
  const r = toE164(raw);
  if (!r.ok) throw new Error(raw);
  return r.value;
};

// 形式だけ正しい架空の値（本物のアカウント・番号ではない）
const ACCOUNT = "AC00000000000000000000000000000000";
const TOKEN = "test-auth-token-0000000000000000";
const AGENT = e164("+81300000001");
const BASE = "https://tac.example.test";

interface Sent {
  readonly method: string;
  readonly url: string;
  readonly headers: Record<string, string>;
  readonly form: URLSearchParams;
}

type Reply = Response | Error | (() => Promise<Response>);

/** 送った要求を記録し、用意した応答を順に返す偽の fetch（本物の Twilio には接続しない） */
function fakeFetch(replies: Reply[]) {
  const sent: Sent[] = [];
  const fn = async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const headers = Object.fromEntries(new Headers(init?.headers).entries());
    sent.push({
      method: init?.method ?? "GET",
      url: String(input),
      headers,
      form: new URLSearchParams(typeof init?.body === "string" ? init.body : ""),
    });
    const next = replies.shift();
    if (next === undefined) throw new Error("unexpected request");
    if (next instanceof Error) throw next;
    if (typeof next === "function") return next();
    return next;
  };
  return { fetch: fn as typeof fetch, sent };
}

const callJson = (sid: string, status = "queued") =>
  new Response(JSON.stringify({ sid, status, to: "+819000000001" }), {
    status: 201,
    headers: { "content-type": "application/json" },
  });

const twilioError = (status: number, code: number) =>
  new Response(
    JSON.stringify({ code, message: "The 'To' number +819000000001 is not valid", status }),
    {
      status,
      headers: { "content-type": "application/json" },
    },
  );

const provider = (f: typeof fetch, extra: Partial<{ requestTimeoutMs: number }> = {}) =>
  new TwilioTelephonyProvider({
    accountSid: ACCOUNT,
    authToken: TOKEN,
    agentNumber: AGENT,
    publicBaseUrl: BASE,
    ringTimeoutSeconds: 30,
    timeLimitSeconds: 1800,
    fetch: f,
    ...extra,
  });

const request = {
  idempotencyKey: "call-uuid-1",
  to: e164("090-0000-0001"),
  from: e164("03-0000-0000"),
  callId: "call-uuid-1",
  disclosureText: "こちらは株式会社サンプル<不動産>の佐藤です。",
};

const CALLS_URL = `https://api.twilio.com/2010-04-01/Accounts/${ACCOUNT}/Calls.json`;

describe("TwilioTelephonyProvider.createCall", () => {
  it("dials the operator first, then the customer, and returns the customer leg", async () => {
    const f = fakeFetch([callJson("CAagent"), callJson("CAcustomer")]);
    const call = await provider(f.fetch).createCall(request);

    expect(call).toEqual({ provider: "twilio", providerCallId: "CAcustomer", status: "DIALING" });
    expect(f.sent).toHaveLength(2);
    const [agent, customer] = f.sent;
    for (const s of f.sent) {
      expect(s.method).toBe("POST");
      expect(s.url).toBe(CALLS_URL);
      expect(s.headers["content-type"]).toBe("application/x-www-form-urlencoded");
      expect(s.headers.authorization).toBe(
        `Basic ${Buffer.from(`${ACCOUNT}:${TOKEN}`).toString("base64")}`,
      );
      expect(s.form.get("From")).toBe(request.from);
    }
    expect(agent?.form.get("To")).toBe(AGENT);
    expect(customer?.form.get("To")).toBe(request.to);
  });

  it("joins both legs to one conference named after the call; only the operator starts it", async () => {
    const f = fakeFetch([callJson("CAagent"), callJson("CAcustomer")]);
    await provider(f.fetch).createCall(request);
    const [agent, customer] = f.sent;
    const agentTwiml = agent?.form.get("Twiml") ?? "";
    const customerTwiml = customer?.form.get("Twiml") ?? "";
    expect(agentTwiml).toContain('startConferenceOnEnter="true"');
    expect(agentTwiml).toContain(">tac-call-uuid-1</Conference>");
    expect(customerTwiml).toContain('startConferenceOnEnter="false"');
    expect(customerTwiml).toContain(">tac-call-uuid-1</Conference>");
    // 名乗りは相手のレッグだけで、XML として安全にエスケープする
    expect(customerTwiml).toContain(
      '<Say language="ja-JP">こちらは株式会社サンプル&lt;不動産&gt;の佐藤です。</Say>',
    );
    expect(agentTwiml).not.toContain("<Say");
  });

  it("asks for signed status callbacks for the customer leg, carrying our call ID", async () => {
    const f = fakeFetch([callJson("CAagent"), callJson("CAcustomer")]);
    await provider(f.fetch).createCall(request);
    const [agent, customer] = f.sent;
    expect(customer?.form.get("StatusCallback")).toBe(
      `${BASE}${TWILIO_WEBHOOK_PATH}?callId=call-uuid-1`,
    );
    expect(customer?.form.get("StatusCallbackMethod")).toBe("POST");
    expect(customer?.form.getAll("StatusCallbackEvent")).toEqual([
      "initiated",
      "ringing",
      "answered",
      "completed",
    ]);
    expect(customer?.form.get("Timeout")).toBe("30");
    expect(customer?.form.get("TimeLimit")).toBe("1800");
    // 担当者のレッグにも時間の上限はかける（会議に1人で残り続けない）
    expect(agent?.form.get("TimeLimit")).toBe("1800");
    expect(agent?.form.get("StatusCallback")).toBeNull();
  });

  it("never records the call (recording is gated and off)", async () => {
    const f = fakeFetch([callJson("CAagent"), callJson("CAcustomer")]);
    await provider(f.fetch).createCall(request);
    for (const s of f.sent) {
      expect(s.form.get("Record")).toBeNull();
      expect(s.form.get("Twiml")).not.toContain("record=");
    }
  });

  it("does not call the customer when the operator leg is rejected", async () => {
    const f = fakeFetch([twilioError(400, 21211)]);
    await expect(provider(f.fetch).createCall(request)).rejects.toBeInstanceOf(
      ProviderRejectedError,
    );
    expect(f.sent).toHaveLength(1);
  });

  it("does not call the customer when the operator leg fails in an unknown way (customer was never dialed)", async () => {
    const f = fakeFetch([new TypeError("fetch failed")]);
    await expect(provider(f.fetch).createCall(request)).rejects.toBeInstanceOf(
      ProviderRejectedError,
    );
    expect(f.sent).toHaveLength(1);
  });

  it("hangs up the operator leg when Twilio rejects the customer leg", async () => {
    const f = fakeFetch([
      callJson("CAagent"),
      twilioError(400, 21211),
      new Response("{}", { status: 200 }),
    ]);
    await expect(provider(f.fetch).createCall(request)).rejects.toBeInstanceOf(
      ProviderRejectedError,
    );
    expect(f.sent).toHaveLength(3);
    expect(f.sent[2]?.url).toBe(
      `https://api.twilio.com/2010-04-01/Accounts/${ACCOUNT}/Calls/CAagent.json`,
    );
    expect(f.sent[2]?.form.get("Status")).toBe("canceled");
  });

  it("does not leak the phone number from Twilio's error message", async () => {
    const f = fakeFetch([callJson("CAagent"), twilioError(400, 21211), new Response("{}")]);
    const error = await provider(f.fetch)
      .createCall(request)
      .catch((e: unknown) => e);
    expect(String((error as Error).message)).toContain("21211");
    expect(String((error as Error).message)).not.toContain("819000000001");
  });

  it("treats a dropped connection on the customer leg as uncertain (not a rejection) and does not retry", async () => {
    const f = fakeFetch([callJson("CAagent"), new TypeError("fetch failed")]);
    const error = await provider(f.fetch)
      .createCall(request)
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(Error);
    expect(error).not.toBeInstanceOf(ProviderRejectedError);
    // Twilio の発信 API に冪等キーは無いので、自分で再送しない（2 回目の発信をしない）
    expect(f.sent).toHaveLength(2);
  });

  it("treats a 5xx on the customer leg as uncertain", async () => {
    const f = fakeFetch([callJson("CAagent"), new Response("bad gateway", { status: 502 })]);
    const error = await provider(f.fetch)
      .createCall(request)
      .catch((e: unknown) => e);
    expect(error).not.toBeInstanceOf(ProviderRejectedError);
    expect(f.sent).toHaveLength(2);
  });

  it("treats a 2xx without a call SID as uncertain", async () => {
    const f = fakeFetch([callJson("CAagent"), new Response("{}", { status: 201 })]);
    const error = await provider(f.fetch)
      .createCall(request)
      .catch((e: unknown) => e);
    expect(error).not.toBeInstanceOf(ProviderRejectedError);
  });

  it("gives up waiting after the request timeout and reports a timeout (the call may exist)", async () => {
    let calls = 0;
    // 2 本目（お客様）だけ応答せず、中断されたら reject する
    const slow = async (_input: string | URL | Request, init?: RequestInit) => {
      if (calls++ === 0) return callJson("CAagent");
      return await new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(init.signal?.reason));
      });
    };
    const error = await provider(slow as typeof fetch, { requestTimeoutMs: 20 })
      .createCall(request)
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ProviderTimeoutError);
    expect(calls).toBe(2);
  });

  it("asks Twilio only once per idempotency key within the process", async () => {
    const f = fakeFetch([callJson("CAagent"), callJson("CAcustomer")]);
    const p = provider(f.fetch);
    const [a, b] = await Promise.all([p.createCall(request), p.createCall(request)]);
    expect(a).toEqual(b);
    expect(f.sent).toHaveLength(2);
  });

  it("refuses an over-long disclosure instead of truncating it (Twiml is limited to 4000 chars)", async () => {
    const f = fakeFetch([]);
    await expect(
      provider(f.fetch).createCall({ ...request, disclosureText: "あ".repeat(4000) }),
    ).rejects.toBeInstanceOf(ProviderRejectedError);
    expect(f.sent).toHaveLength(0);
  });
});

describe("TwilioTelephonyProvider: other operations", () => {
  it("getCall maps Twilio's status vocabulary", async () => {
    const f = fakeFetch([
      new Response(JSON.stringify({ sid: "CAx", status: "in-progress" }), { status: 200 }),
    ]);
    expect(await provider(f.fetch).getCall("CAx")).toEqual({
      provider: "twilio",
      providerCallId: "CAx",
      status: "IN_PROGRESS",
    });
    expect(f.sent[0]?.method).toBe("GET");
    expect(f.sent[0]?.url).toBe(
      `https://api.twilio.com/2010-04-01/Accounts/${ACCOUNT}/Calls/CAx.json`,
    );
  });

  it("getCall refuses an unknown status instead of guessing", async () => {
    const f = fakeFetch([new Response(JSON.stringify({ sid: "CAx", status: "weird" }))]);
    await expect(provider(f.fetch).getCall("CAx")).rejects.toThrow(/status/);
  });

  it("endCall hangs up a connected call, falling back to cancel for one still ringing", async () => {
    const f = fakeFetch([twilioError(400, 21220), new Response("{}")]);
    await provider(f.fetch).endCall("CAx");
    expect(f.sent.map((s) => s.form.get("Status"))).toEqual(["completed", "canceled"]);
  });

  it("rejects provider call IDs that are not Twilio SIDs (no path injection)", async () => {
    const f = fakeFetch([]);
    await expect(provider(f.fetch).getCall("../Accounts")).rejects.toThrow();
    await expect(provider(f.fetch).endCall("CA/../../x")).rejects.toThrow();
    expect(f.sent).toHaveLength(0);
  });

  it("transfer is not implemented yet and says so", async () => {
    const f = fakeFetch([]);
    await expect(
      provider(f.fetch).transferCall("CAx", { kind: "PHONE", to: AGENT }),
    ).rejects.toThrow(/not implemented/);
  });
});

describe("Twilio webhook signature", () => {
  const url = `${BASE}${TWILIO_WEBHOOK_PATH}?callId=call-uuid-1`;
  const params = {
    CallSid: "CA0123",
    CallStatus: "ringing",
    AccountSid: ACCOUNT,
    To: "+819000000001",
  };

  it("matches Twilio's official SDK", () => {
    expect(twilioSignature(TOKEN, url, params)).toBe(
      twilio.getExpectedTwilioSignature(TOKEN, url, params),
    );
  });

  it("accepts what the official SDK signs, and the SDK accepts what we sign", () => {
    const sdk = twilio.getExpectedTwilioSignature(TOKEN, url, params);
    expect(verifyTwilioSignature(TOKEN, url, params, sdk)).toBe(true);
    expect(twilio.validateRequest(TOKEN, twilioSignature(TOKEN, url, params), url, params)).toBe(
      true,
    );
  });

  it("rejects a changed parameter, URL, token, or a missing header", () => {
    const sig = twilioSignature(TOKEN, url, params);
    expect(verifyTwilioSignature(TOKEN, url, { ...params, CallStatus: "completed" }, sig)).toBe(
      false,
    );
    expect(verifyTwilioSignature(TOKEN, url.replace("call-uuid-1", "other"), params, sig)).toBe(
      false,
    );
    expect(verifyTwilioSignature("another-token-000000", url, params, sig)).toBe(false);
    expect(verifyTwilioSignature(TOKEN, url, params, undefined)).toBe(false);
    expect(verifyTwilioSignature(TOKEN, url, params, "")).toBe(false);
  });
});

describe("normalizeTwilioStatus", () => {
  it("maps every status in Twilio's OpenAPI enum", () => {
    expect(normalizeTwilioStatus("queued")).toBe("DIALING");
    expect(normalizeTwilioStatus("initiated")).toBe("DIALING");
    expect(normalizeTwilioStatus("ringing")).toBe("RINGING");
    expect(normalizeTwilioStatus("in-progress")).toBe("IN_PROGRESS");
    expect(normalizeTwilioStatus("completed")).toBe("ENDED");
    expect(normalizeTwilioStatus("busy")).toBe("BUSY");
    expect(normalizeTwilioStatus("failed")).toBe("FAILED");
    expect(normalizeTwilioStatus("no-answer")).toBe("NO_ANSWER");
    expect(normalizeTwilioStatus("canceled")).toBe("CANCELED");
  });

  it("returns undefined for anything else (never invents a state)", () => {
    expect(normalizeTwilioStatus("answered")).toBeUndefined();
    expect(normalizeTwilioStatus("constructor")).toBeUndefined();
    expect(normalizeTwilioStatus("")).toBeUndefined();
  });
});

describe("createTelephonyProvider with twilio", () => {
  const twilioConfig = {
    accountSid: ACCOUNT,
    authToken: TOKEN,
    agentNumber: AGENT,
    publicBaseUrl: BASE,
    ringTimeoutSeconds: 30,
    timeLimitSeconds: 1800,
  };

  it("builds the Twilio adapter in staging / production", () => {
    for (const appEnv of ["staging", "production"] as const) {
      const p = createTelephonyProvider({ appEnv, provider: "twilio", twilio: twilioConfig });
      expect(p.name).toBe("twilio");
    }
  });

  it("still refuses Twilio in local / test, even with credentials", () => {
    for (const appEnv of ["local", "test"] as const) {
      expect(() =>
        createTelephonyProvider({ appEnv, provider: "twilio", twilio: twilioConfig }),
      ).toThrow(/not allowed/);
    }
  });

  it("refuses Twilio without its settings", () => {
    expect(() => createTelephonyProvider({ appEnv: "staging", provider: "twilio" })).toThrow(
      /settings/,
    );
  });
});
