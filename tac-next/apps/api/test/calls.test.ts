import { randomUUID } from "node:crypto";
import type { Database } from "@tac/db";
import { sql } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { buildApi, call, freshDatabase, login, seedTenant, seedUser } from "./support.js";

/*
 * 発信 API（POST /v1/calls）と結果（POST /v1/calls/{id}/outcome）。
 * INV-1（抑止）・INV-2（テナント）・INV-3（冪等）・INV-6（全発信停止・設定のゲート）の API 層。
 */

let database: Database;

beforeAll(async () => {
  database = await freshDatabase();
}, 60_000);

afterAll(async () => {
  await database.close();
});

async function world(opts: { features?: { outboundCalls: boolean; aiVoice: boolean } } = {}) {
  const t = await seedTenant(database);
  const operator = await seedUser(database, t.org, "OPERATOR");
  const api = buildApi(database, opts.features ? { features: opts.features } : {});
  const session = await login(api, operator.email);
  const body = (contact = 0) => ({
    contactId: t.contactIds[contact],
    campaignId: t.campaignId,
    mode: "HUMAN_DIALED",
  });
  const dial = (key: string, contact = 0, over: Record<string, unknown> = {}) =>
    call(api, "POST", "/v1/calls", {
      session,
      body: { ...body(contact), ...over },
      headers: { "idempotency-key": key },
    });
  return { t, api, session, operator, dial };
}

describe("POST /v1/calls：認可", () => {
  it("ログインしていなければ 401", async () => {
    const { api, t } = await world();
    const res = await call(api, "POST", "/v1/calls", {
      body: { contactId: t.contactIds[0], campaignId: t.campaignId, mode: "HUMAN_DIALED" },
      headers: { "idempotency-key": "k" },
    });
    expect(res.status).toBe(401);
    expect(api.telephony.placed).toHaveLength(0);
  });

  it("VIEWER は 403（発信は OPERATOR 以上）", async () => {
    const { api, t } = await world();
    const viewer = await seedUser(database, t.org, "VIEWER");
    const session = await login(api, viewer.email);
    const res = await call(api, "POST", "/v1/calls", {
      session,
      body: { contactId: t.contactIds[0], campaignId: t.campaignId, mode: "HUMAN_DIALED" },
      headers: { "idempotency-key": "k" },
    });
    expect(res.status).toBe(403);
    expect(await res.json()).toMatchObject({ error: { code: "FORBIDDEN" } });
    expect(api.telephony.placed).toHaveLength(0);
  });

  it("CSRF トークンがなければ 403", async () => {
    const { api, t, session } = await world();
    const res = await call(api, "POST", "/v1/calls", {
      session,
      csrf: false,
      body: { contactId: t.contactIds[0], campaignId: t.campaignId, mode: "HUMAN_DIALED" },
      headers: { "idempotency-key": "k" },
    });
    expect(res.status).toBe(403);
    expect(api.telephony.placed).toHaveLength(0);
  });

  it("別テナントの連絡先は 404（存在を漏らさない）", async () => {
    const { dial, api } = await world();
    const other = await seedTenant(database, { phones: ["+819000000009"] });
    const res = await dial("k", 0, { contactId: other.contactIds[0] });
    expect(res.status).toBe(404);
    expect(await res.json()).toMatchObject({ error: { code: "CONTACT_NOT_FOUND" } });
    expect(api.telephony.placed).toHaveLength(0);
  });

  it("本文で organizationId を送っても使わない（未知の項目は 400）", async () => {
    const { dial, api } = await world();
    const other = await seedTenant(database);
    const res = await dial("k", 0, { organizationId: other.org });
    expect(res.status).toBe(400);
    expect(api.telephony.placed).toHaveLength(0);
  });
});

describe("POST /v1/calls：冪等性", () => {
  it("Idempotency-Key がなければ 400", async () => {
    const { api, session, t } = await world();
    const res = await call(api, "POST", "/v1/calls", {
      session,
      body: { contactId: t.contactIds[0], campaignId: t.campaignId, mode: "HUMAN_DIALED" },
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: { code: "IDEMPOTENCY_KEY_REQUIRED" } });
  });

  it("成功は 201。同じキーの再送は 200 で同じ通話を返し、2回目は発信しない", async () => {
    const { dial, api } = await world();
    const first = await dial("same-key");
    expect(first.status).toBe(201);
    const a = (await first.json()) as { call: { id: string; status: string; to: string } };
    expect(a.call.status).toBe("DIALING");
    expect(a.call.to).toBe("+8190****0001"); // 電話番号はマスクして返す
    const again = await dial("same-key");
    expect(again.status).toBe(200);
    expect(await again.json()).toMatchObject({ call: { id: a.call.id }, replayed: true });
    expect(api.telephony.placed).toHaveLength(1);
  });

  it("同じキー・違う内容は 409", async () => {
    const { dial } = await world();
    await dial("k");
    const res = await dial("k", 1);
    expect(res.status).toBe(409);
    expect(await res.json()).toMatchObject({ error: { code: "IDEMPOTENCY_KEY_REUSED" } });
  });

  it("同じキーを 10 並列で送っても、外部発信は 1 件", async () => {
    const { dial, api } = await world();
    const results = await Promise.all(Array.from({ length: 10 }, () => dial("parallel")));
    expect(results.map((r) => r.status).sort()).toEqual([
      200, 200, 200, 200, 200, 200, 200, 200, 200, 201,
    ]);
    expect(api.telephony.placed).toHaveLength(1);
  });
});

describe("POST /v1/calls：発信の拒否（理由のコードで返す）", () => {
  it("抑止中の相手は 422 CONTACT_SUPPRESSED", async () => {
    const { dial, api, t } = await world();
    await database.db.execute(sql`
      insert into suppression_entries (organization_id, phone_e164, reason, source, actor_id)
      values (${t.org}, '+819000000001', 'DO_NOT_CALL', 'test', 'test')`);
    const res = await dial("k");
    expect(res.status).toBe(422);
    expect(await res.json()).toMatchObject({
      error: { code: "CONTACT_SUPPRESSED", reasons: ["CONTACT_SUPPRESSED"] },
    });
    expect(api.telephony.placed).toHaveLength(0);
  });

  it("全発信停止中は 422 OUTBOUND_STOPPED", async () => {
    const { dial, api } = await world();
    await database.db.execute(sql`update system_controls set outbound_stopped = true`);
    try {
      const res = await dial("k");
      expect(res.status).toBe(422);
      expect(await res.json()).toMatchObject({ error: { code: "OUTBOUND_STOPPED" } });
      expect(api.telephony.placed).toHaveLength(0);
    } finally {
      await database.db.execute(sql`update system_controls set outbound_stopped = false`);
    }
  });

  it("設定のゲートが OFF なら 422 OUTBOUND_DISABLED_BY_CONFIG", async () => {
    const { dial, api } = await world({ features: { outboundCalls: false, aiVoice: false } });
    const res = await dial("k");
    expect(res.status).toBe(422);
    expect(await res.json()).toMatchObject({ error: { code: "OUTBOUND_DISABLED_BY_CONFIG" } });
    expect(api.telephony.placed).toHaveLength(0);
  });

  it("プロバイダの障害は 502。自動で掛け直さない", async () => {
    const { dial, api } = await world();
    api.telephony.nextScenario("PROVIDER_ERROR");
    const res = await dial("k");
    expect(res.status).toBe(502);
    expect(await res.json()).toMatchObject({ error: { code: "PROVIDER_ERROR" } });
    expect(api.telephony.placed).toHaveLength(1);
  });
});

describe("GET /v1/calls/{id}", () => {
  it("自分の組織の通話は 200、別の組織・不正な ID は 404", async () => {
    const { dial, api, session } = await world();
    const created = (await (await dial("k")).json()) as { call: { id: string } };
    const own = await call(api, "GET", `/v1/calls/${created.call.id}`, { session });
    expect(own.status).toBe(200);
    expect(await own.json()).toMatchObject({ call: { id: created.call.id } });

    const other = await world();
    const cross = await call(other.api, "GET", `/v1/calls/${created.call.id}`, {
      session: other.session,
    });
    expect(cross.status).toBe(404);
    expect((await call(api, "GET", "/v1/calls/not-a-uuid", { session })).status).toBe(404);
    expect((await call(api, "GET", `/v1/calls/${randomUUID()}`, { session })).status).toBe(404);
  });
});

describe("POST /v1/calls/{id}/outcome", () => {
  it("「拒否」を記録すると抑止され、同じ相手への次の発信は 422 になる", async () => {
    const { dial, api, session } = await world();
    const created = (await (await dial("k1")).json()) as { call: { id: string } };
    const res = await call(api, "POST", `/v1/calls/${created.call.id}/outcome`, {
      session,
      body: { outcome: "拒否" },
    });
    expect(res.status).toBe(201);
    expect(await res.json()).toMatchObject({ suppressed: true, outcome: { code: "DO_NOT_CALL" } });

    // 抑止は発信判定の理由の中で最優先（1日の回数制限などより先に返る）
    const again = await dial("k2");
    expect(again.status).toBe(422);
    expect(await again.json()).toMatchObject({ error: { code: "CONTACT_SUPPRESSED" } });
    expect(api.telephony.placed).toHaveLength(1);
  });

  it("同じ結果の再送は 200、違う結果は 409、知らない結果は 400", async () => {
    const { dial, api, session } = await world();
    const created = (await (await dial("k")).json()) as { call: { id: string } };
    const path = `/v1/calls/${created.call.id}/outcome`;
    expect((await call(api, "POST", path, { session, body: { outcome: "検討" } })).status).toBe(
      201,
    );
    expect((await call(api, "POST", path, { session, body: { outcome: "検討" } })).status).toBe(
      200,
    );
    expect((await call(api, "POST", path, { session, body: { outcome: "成約" } })).status).toBe(
      409,
    );
    expect((await call(api, "POST", path, { session, body: { outcome: "謎" } })).status).toBe(400);
  });
});
