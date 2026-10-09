import { ReconcileUncertainCallsUseCase } from "@tac/application";
import { type Database, PgUncertainCallFinder, TenantScope } from "@tac/db";
import { toE164 } from "@tac/domain";
import { TWILIO_WEBHOOK_PATH, TwilioTelephonyProvider } from "@tac/telephony";
import { sql } from "drizzle-orm";
// 公式 SDK は「Twilio が付ける署名」を作る参照実装としてだけ使う
import twilio from "twilio";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { buildApi, call, freshDatabase, login, seedTenant, seedUser } from "./support.js";

/*
 * Phase 11：Twilio の状態通知（StatusCallback）を POST /v1/webhooks/twilio で受ける。
 * 発信は TwilioTelephonyProvider を偽の fetch で動かす（本物の Twilio には接続しない・電話はかけない）。
 * 署名は Twilio 公式 SDK で作る＝Twilio から届くものと同じ形。
 */

const ACCOUNT = "AC00000000000000000000000000000000";
const TOKEN = "test-auth-token-0000000000000000";
const BASE = "https://tac-next.example.test";
const e164 = (raw: string) => {
  const r = toE164(raw);
  if (!r.ok) throw new Error(raw);
  return r.value;
};

let database: Database;
beforeAll(async () => {
  database = await freshDatabase();
}, 60_000);
afterAll(async () => {
  await database.close();
});

type Reply = Response | Error;
function fakeTwilio(replies: Reply[]) {
  const sent: { url: string; form: URLSearchParams }[] = [];
  const f = async (input: string | URL | Request, init?: RequestInit) => {
    sent.push({
      url: String(input),
      form: new URLSearchParams(typeof init?.body === "string" ? init.body : ""),
    });
    const next = replies.shift();
    if (!next) throw new Error("unexpected Twilio request");
    if (next instanceof Error) throw next;
    return next;
  };
  return { fetch: f as typeof fetch, sent };
}
const created = (sid: string) =>
  new Response(JSON.stringify({ sid, status: "queued" }), { status: 201 });

async function setup(replies: Reply[], opts: { webhooks?: boolean } = {}) {
  const t = await seedTenant(database);
  const operator = await seedUser(database, t.org, "OPERATOR");
  const twilioApi = fakeTwilio(replies);
  const telephony = new TwilioTelephonyProvider({
    accountSid: ACCOUNT,
    authToken: TOKEN,
    agentNumber: e164("+81300000001"),
    publicBaseUrl: BASE,
    ringTimeoutSeconds: 30,
    timeLimitSeconds: 1800,
    fetch: twilioApi.fetch,
  });
  const api = buildApi(database, {
    deps: { telephony },
    api: {
      ...(opts.webhooks === false
        ? {}
        : { twilioWebhooks: { authToken: TOKEN, publicBaseUrl: BASE } }),
    },
  });
  const session = await login(api, operator.email);
  const res = await call(api, "POST", "/v1/calls", {
    session,
    body: { contactId: t.contactIds[0], campaignId: t.campaignId, mode: "HUMAN_DIALED" },
    headers: { "idempotency-key": "k" },
  });
  const callId = (
    await database.db.execute<{ id: string }>(
      sql`select id from calls where organization_id = ${t.org}`,
    )
  ).rows[0]?.id as string;
  const row = async () =>
    (
      await database.db.execute<{
        status: string;
        provider: string | null;
        provider_call_id: string | null;
      }>(sql`select status, provider, provider_call_id from calls where id = ${callId}`)
    ).rows[0];
  return { api, t, session, callId, res, row, twilioApi };
}

/** Twilio と同じ形で、状態通知を届ける（URL は Twilio に渡した StatusCallback そのもの） */
function notify(
  api: ReturnType<typeof buildApi>,
  callId: string,
  params: Record<string, string>,
  opts: { token?: string; signedUrl?: string; signature?: string; contentType?: string } = {},
) {
  const path = `${TWILIO_WEBHOOK_PATH}?callId=${encodeURIComponent(callId)}`;
  const signature =
    opts.signature ??
    twilio.getExpectedTwilioSignature(
      opts.token ?? TOKEN,
      opts.signedUrl ?? `${BASE}${path}`,
      params,
    );
  return api.app.request(path, {
    method: "POST",
    headers: {
      "content-type": opts.contentType ?? "application/x-www-form-urlencoded",
      "x-twilio-signature": signature,
    },
    body: new URLSearchParams(params).toString(),
  });
}

const status = (sid: string, s: string) => ({
  AccountSid: ACCOUNT,
  CallSid: sid,
  CallStatus: s,
  Direction: "outbound-api",
  To: "+819000000001",
});

describe("POST /v1/webhooks/twilio", () => {
  it("places the call through Twilio and follows the signed status callbacks to the end", async () => {
    const s = await setup([created("CAagent0001"), created("CAcustomer01")]);
    expect(s.res.status).toBe(201);
    expect(await s.row()).toMatchObject({
      status: "DIALING",
      provider: "twilio",
      provider_call_id: "CAcustomer01",
    });
    for (const [raw, expected] of [
      ["ringing", "RINGING"],
      ["in-progress", "IN_PROGRESS"],
      ["completed", "ENDED"],
    ] as const) {
      const r = await notify(s.api, s.callId, status("CAcustomer01", raw));
      expect(r.status).toBe(200);
      expect((await s.row())?.status).toBe(expected);
    }
  });

  it("rejects a missing or wrong signature and changes nothing", async () => {
    const s = await setup([created("CAagent0002"), created("CAcustomer02")]);
    const forged = await notify(s.api, s.callId, status("CAcustomer02", "completed"), {
      token: "attacker-token-00000000000000000",
    });
    expect(forged.status).toBe(401);
    const missing = await notify(s.api, s.callId, status("CAcustomer02", "completed"), {
      signature: "",
    });
    expect(missing.status).toBe(401);
    expect((await s.row())?.status).toBe("DIALING");
  });

  it("verifies against the configured public URL, not the Host the request claims", async () => {
    const s = await setup([created("CAagent0003"), created("CAcustomer03")]);
    // 別のホスト名の URL で正しく署名しても、設定した公開 URL とは違うので通らない
    const path = `${TWILIO_WEBHOOK_PATH}?callId=${s.callId}`;
    const r = await notify(s.api, s.callId, status("CAcustomer03", "completed"), {
      signedUrl: `https://evil.example.test${path}`,
    });
    expect(r.status).toBe(401);
    expect((await s.row())?.status).toBe("DIALING");
  });

  it("the call ID in the URL is covered by the signature (cannot be swapped to another call)", async () => {
    const a = await setup([created("CAagent0004"), created("CAcustomer04")]);
    const b = await setup([created("CAagent0005"), created("CAcustomer05")]);
    const params = status("CAcustomer04", "completed");
    const signedForA = twilio.getExpectedTwilioSignature(
      TOKEN,
      `${BASE}${TWILIO_WEBHOOK_PATH}?callId=${a.callId}`,
      params,
    );
    const r = await notify(a.api, b.callId, params, { signature: signedForA });
    expect(r.status).toBe(401);
    expect((await b.row())?.status).toBe("DIALING");
  });

  it("applies a re-delivered callback once", async () => {
    const s = await setup([created("CAagent0006"), created("CAcustomer06")]);
    const first = await notify(s.api, s.callId, status("CAcustomer06", "ringing"));
    const again = await notify(s.api, s.callId, status("CAcustomer06", "ringing"));
    expect(await first.json()).toEqual({ result: "APPLIED" });
    expect(await again.json()).toEqual({ result: "DUPLICATE" });
  });

  it("never moves a finished call backwards when callbacks arrive out of order", async () => {
    const s = await setup([created("CAagent0007"), created("CAcustomer07")]);
    await notify(s.api, s.callId, status("CAcustomer07", "completed"));
    await notify(s.api, s.callId, status("CAcustomer07", "ringing"));
    expect((await s.row())?.status).toBe("ENDED");
  });

  it("does not store the phone numbers Twilio sends in the webhook inbox", async () => {
    const s = await setup([created("CAagent0013"), created("CAcustomer13")]);
    await notify(s.api, s.callId, { ...status("CAcustomer13", "ringing"), From: "+81300000000" });
    const stored = (
      await database.db.execute<{ payload: Record<string, unknown> }>(
        sql`select payload from webhook_events where provider = 'twilio' and event_id = 'CAcustomer13:ringing'`,
      )
    ).rows[0]?.payload;
    expect(stored).toMatchObject({ CallSid: "CAcustomer13", CallStatus: "ringing" });
    expect(JSON.stringify(stored)).not.toMatch(/\+81/);
  });

  it("acknowledges an unknown CallStatus without inventing a state", async () => {
    const s = await setup([created("CAagent0008"), created("CAcustomer08")]);
    const r = await notify(s.api, s.callId, status("CAcustomer08", "something-new"));
    expect(r.status).toBe(200);
    expect(await r.json()).toEqual({ result: "IGNORED" });
    expect((await s.row())?.status).toBe("DIALING");
  });

  it("recovers a call whose create response was lost: the callback attaches Twilio's ID (IQA-03)", async () => {
    // お客様のレッグの応答が届かなかった → 発信されたか分からないので REQUESTED のまま
    const s = await setup([created("CAagent0009"), new TypeError("fetch failed")]);
    expect(s.res.status).toBe(504);
    expect(await s.row()).toMatchObject({ status: "REQUESTED", provider_call_id: null });
    // 実際には発信されていて、Twilio から状態通知が届いた
    const r = await notify(s.api, s.callId, status("CAcustomer09", "ringing"));
    expect(await r.json()).toEqual({ result: "APPLIED" });
    expect(await s.row()).toMatchObject({
      status: "RINGING",
      provider: "twilio",
      provider_call_id: "CAcustomer09",
    });
    // 自分では掛け直していない（担当者 1 本＋お客様 1 本だけ）
    expect(s.twilioApi.sent).toHaveLength(2);
  });

  it("refuses a callback whose CallSid differs from the one recorded for the call (IQA-05)", async () => {
    const s = await setup([created("CAagent0010"), created("CAcustomer10")]);
    const r = await notify(s.api, s.callId, status("CAotherleg10", "completed"));
    expect(await r.json()).toEqual({ result: "UNKNOWN_CALL" });
    expect((await s.row())?.status).toBe("DIALING");
  });

  it("requires a form body and a well-formed CallSid", async () => {
    const s = await setup([created("CAagent0011"), created("CAcustomer11")]);
    const json = await notify(s.api, s.callId, status("CAcustomer11", "completed"), {
      contentType: "application/json",
    });
    expect(json.status).toBe(415);
    const bad = await notify(s.api, s.callId, status("../x", "completed"));
    expect(bad.status).toBe(400);
    const noCallId = await s.api.app.request(TWILIO_WEBHOOK_PATH, { method: "POST" });
    expect(noCallId.status).toBe(415);
    expect((await s.row())?.status).toBe("DIALING");
  });

  it("is not exposed unless Twilio is configured", async () => {
    const s = await setup([created("CAagent0012"), created("CAcustomer12")], { webhooks: false });
    const r = await notify(s.api, s.callId, status("CAcustomer12", "completed"));
    expect(r.status).toBe(404);
  });
  it("応答が失われた発信を、Twilio の通話一覧との照合で回収する（ADR-0016）", async () => {
    const s = await setup([created("CAagent0014"), new TypeError("fetch failed")]);
    expect(await s.row()).toMatchObject({ status: "REQUESTED", provider_call_id: null });
    // 5 分後、Twilio の通話一覧にはお客様への発信が 1 件ある
    s.api.clock.set(new Date(s.api.clock.now().getTime() + 5 * 60_000));
    const replies = [
      new Response(
        JSON.stringify({
          calls: [
            {
              sid: "CAcustomer14",
              status: "in-progress",
              direction: "outbound-api",
              date_created: "Mon, 05 Oct 2026 01:00:03 +0000",
            },
          ],
          next_page_uri: null,
        }),
        { status: 200 },
      ),
    ];
    const sendList = fakeTwilio(replies);
    const telephony = new TwilioTelephonyProvider({
      accountSid: ACCOUNT,
      authToken: TOKEN,
      agentNumber: e164("+81300000001"),
      publicBaseUrl: BASE,
      ringTimeoutSeconds: 30,
      timeLimitSeconds: 1800,
      fetch: sendList.fetch,
    });
    const r = await new ReconcileUncertainCallsUseCase(
      { ...s.api.deps, telephony },
      new PgUncertainCallFinder(new TenantScope(database.db)),
    ).execute({ limit: 500 });
    expect(r.results).toContainEqual({ callId: s.callId, kind: "MATCHED" });
    expect(await s.row()).toMatchObject({
      status: "IN_PROGRESS",
      provider: "twilio",
      provider_call_id: "CAcustomer14",
    });
    // 照合では発信しない（一覧を 1 回読んだだけ）
    expect(sendList.sent).toHaveLength(1);
    expect(new URL(sendList.sent[0]?.url ?? "").searchParams.get("To")).toBe("+819000000001");
  });
});
