import type { Database } from "@tac/db";
import type { SimulatedEvent, SimulatedScenario } from "@tac/telephony";
import { sql } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  buildApi,
  call,
  deliver,
  freshDatabase,
  login,
  seedTenant,
  seedUser,
  WEBHOOK_SECRET,
} from "./support.js";

/*
 * Phase 7・8：POST /v1/webhooks/mock（署名検証 → 重複排除 → 状態の反映）を、
 * 電話シミュレーターのシナリオ（VOICE.md）で端から端まで確かめる。
 */

let database: Database;

beforeAll(async () => {
  database = await freshDatabase();
}, 60_000);

afterAll(async () => {
  await database.close();
});

/** ログインして 1 件発信し、シミュレーターが作ったイベント列を返す */
async function placeCall(scenario: SimulatedScenario = "ANSWER") {
  const t = await seedTenant(database);
  const operator = await seedUser(database, t.org, "OPERATOR");
  const api = buildApi(database);
  const session = await login(api, operator.email);
  api.telephony.nextScenario(scenario);
  const res = await call(api, "POST", "/v1/calls", {
    session,
    body: { contactId: t.contactIds[0], campaignId: t.campaignId, mode: "HUMAN_DIALED" },
    headers: { "idempotency-key": "k" },
  });
  const created = (await res.json()) as { call?: { id: string; providerCallId: string } };
  // 発信が失敗した（502）ときは本文に通話がないので、DB から引く
  const callId =
    created.call?.id ??
    (
      await database.db.execute<{ id: string }>(
        sql`select id from calls where organization_id = ${t.org}`,
      )
    ).rows[0]?.id ??
    "";
  const events = created.call ? api.telephony.takeEvents(created.call.providerCallId) : [];
  const status = async () => {
    const r = await call(api, "GET", `/v1/calls/${callId}`, { session });
    return ((await r.json()) as { call: { status: string } }).call.status;
  };
  return { api, t, session, callId, events, status, createStatus: res.status };
}

const deliverAll = async (api: Parameters<typeof deliver>[0], events: SimulatedEvent[]) => {
  const results: unknown[] = [];
  for (const e of events) {
    const res = await deliver(api, e);
    expect(res.status).toBe(200);
    results.push(await res.json());
  }
  return results;
};

describe("シミュレーターのシナリオ（端から端まで）", () => {
  it.each<[SimulatedScenario, string]>([
    ["ANSWER", "ENDED"],
    ["BUSY", "BUSY"],
    ["REJECT", "FAILED"],
    ["NO_ANSWER", "NO_ANSWER"],
    ["DISCONNECT", "FAILED"],
    ["VOICEMAIL", "ENDED"],
  ])("%s → 最終状態 %s", async (scenario, expected) => {
    const ctx = await placeCall(scenario);
    await deliverAll(ctx.api, ctx.events);
    expect(await ctx.status()).toBe(expected);
  });

  it("留守電の「機械が応答した」は生データとして保存される", async () => {
    const ctx = await placeCall("VOICEMAIL");
    await deliverAll(ctx.api, ctx.events);
    const raw = await database.db.execute<{ payload: { answeredBy?: string } }>(
      sql`select payload from webhook_events where payload->>'callId' = ${ctx.callId} and payload->>'status' = 'in-progress'`,
    );
    expect(raw.rows[0]?.payload.answeredBy).toBe("machine");
  });

  it("PROVIDER_ERROR は 502 で、自動で掛け直さない", async () => {
    const ctx = await placeCall("PROVIDER_ERROR");
    expect(ctx.createStatus).toBe(502);
    expect(ctx.api.telephony.placed).toHaveLength(1);
    expect(await ctx.status()).toBe("FAILED");
  });
});

describe("重複・遅延・順序違いの Webhook", () => {
  it("同じイベントが 2 回届いても、2 回目は DUPLICATE で状態もイベントの記録も増えない", async () => {
    const ctx = await placeCall();
    const doubled = ctx.events.flatMap((e) => [e, e]);
    const results = (await deliverAll(ctx.api, doubled)) as { result: string }[];
    expect(results.map((r) => r.result)).toEqual([
      "APPLIED",
      "DUPLICATE",
      "APPLIED",
      "DUPLICATE",
      "APPLIED",
      "DUPLICATE",
    ]);
    const n = await database.db.execute<{ n: number }>(
      sql`select count(*)::int as n from call_events where call_id = ${ctx.callId}`,
    );
    expect(n.rows[0]?.n).toBe(3);
    expect(await ctx.status()).toBe("ENDED");
  });

  it("遅れて届いた in-progress（終話の後）は状態を戻さない", async () => {
    const ctx = await placeCall();
    const [ringing, answered, completed] = ctx.events;
    if (!ringing || !answered || !completed) throw new Error("setup");
    await deliverAll(ctx.api, [ringing, completed]);
    const late = (await deliverAll(ctx.api, [answered])) as { result: string }[];
    expect(late[0]?.result).toBe("STALE");
    expect(await ctx.status()).toBe("ENDED");
  });

  it("completed が ringing より先に届いても、最終状態は ENDED", async () => {
    const ctx = await placeCall();
    await deliverAll(ctx.api, [...ctx.events].reverse());
    expect(await ctx.status()).toBe("ENDED");
  });

  it("全イベントを同時に届けても、最終状態は ENDED", async () => {
    const ctx = await placeCall();
    const responses = await Promise.all(ctx.events.map((e) => deliver(ctx.api, e)));
    expect(responses.every((r) => r.status === 200)).toBe(true);
    expect(await ctx.status()).toBe("ENDED");
  });
});

describe("Webhook の検証", () => {
  it("署名が違う・無い・秘密鍵が違うものは 401 で、状態を変えない", async () => {
    const ctx = await placeCall();
    const [ringing] = ctx.events;
    if (!ringing) throw new Error("setup");
    expect((await deliver(ctx.api, ringing, { signature: "t=1,v1=00" })).status).toBe(401);
    expect((await deliver(ctx.api, ringing, { secret: `${WEBHOOK_SECRET}-other` })).status).toBe(
      401,
    );
    expect(await ctx.status()).toBe("DIALING");
  });

  it("5 分より古い署名は 401（再送攻撃の対策）", async () => {
    const ctx = await placeCall();
    const [ringing] = ctx.events;
    if (!ringing) throw new Error("setup");
    const old = new Date(ctx.api.clock.now().getTime() - 6 * 60 * 1000);
    expect((await deliver(ctx.api, ringing, { at: old })).status).toBe(401);
  });

  it("秘密鍵が設定されていなければ、すべて 401（fail closed）", async () => {
    const ctx = await placeCall();
    const noSecret = buildApi(database, {
      api: { mockWebhooks: { enabled: true, secret: undefined, verifySignatures: true } },
    });
    const [ringing] = ctx.events;
    if (!ringing) throw new Error("setup");
    expect((await deliver(noSecret, ringing)).status).toBe(401);
  });

  it("mock の受け口が無効（staging / production）なら 404", async () => {
    const ctx = await placeCall();
    const prod = buildApi(database, {
      api: { mockWebhooks: { enabled: false, secret: WEBHOOK_SECRET, verifySignatures: true } },
    });
    const [ringing] = ctx.events;
    if (!ringing) throw new Error("setup");
    expect((await deliver(prod, ringing)).status).toBe(404);
  });

  it("知らない状態の語彙は 400、知らない通話は 200（UNKNOWN_CALL、再送の嵐を起こさない）", async () => {
    const ctx = await placeCall();
    const [ringing] = ctx.events;
    if (!ringing) throw new Error("setup");
    const bad = await deliver(ctx.api, { ...ringing, eventId: "x1", status: "exploded" as never });
    expect(bad.status).toBe(400);
    const unknown = await deliver(ctx.api, {
      ...ringing,
      eventId: "x2",
      providerCallId: "MOCK-unknown",
      callId: "00000000-0000-4000-8000-000000000000",
    });
    expect(unknown.status).toBe(200);
    expect(await unknown.json()).toEqual({ result: "UNKNOWN_CALL" });
  });

  it("Webhook はセッションも CSRF も要らない（署名で認証する）", async () => {
    const ctx = await placeCall();
    const [ringing] = ctx.events;
    if (!ringing) throw new Error("setup");
    const res = await deliver(ctx.api, ringing);
    expect(res.status).toBe(200);
  });
});
