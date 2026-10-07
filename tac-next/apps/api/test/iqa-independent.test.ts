import { randomUUID } from "node:crypto";
import type { Database } from "@tac/db";
import { sql } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import {
  buildApi,
  call,
  deliver,
  freshDatabase,
  jst,
  login,
  seedTenant,
  seedUser,
} from "./support.js";

/*
 * 独立 QA（IQA, 2026-10-06）の再現テスト。期待する（安全な）振る舞いを書いているので、
 * 修正されるまでは RED になる。架空の番号だけを使い、電話は MockTelephonyProvider だけ。
 */

let database: Database;

beforeAll(async () => {
  database = await freshDatabase();
}, 60_000);

afterAll(async () => {
  await database.close();
});

async function world() {
  const t = await seedTenant(database);
  const operator = await seedUser(database, t.org, "OPERATOR");
  const api = buildApi(database);
  const session = await login(api, operator.email);
  const dial = (key: string, contact = 0) =>
    call(api, "POST", "/v1/calls", {
      session,
      body: { contactId: t.contactIds[contact], campaignId: t.campaignId, mode: "HUMAN_DIALED" },
      headers: { "idempotency-key": key },
    });
  return { t, api, session, dial, operator };
}

describe("IQA-01: 結果を先に記録すると、後から「拒否（DNC）」を記録できず、翌日また発信される", () => {
  it("「不在」を記録した通話でも、顧客の拒否は抑止として登録でき、以後の発信は 422", async () => {
    const { t, api, session, dial, operator } = await world();
    const created = await dial("k-1");
    expect(created.status).toBe(201);
    const { call: placed } = (await created.json()) as {
      call: { id: string; providerCallId: string };
    };
    // 通話を終わらせる（シミュレーターの Webhook を届ける）
    for (const e of api.telephony.takeEvents(placed.providerCallId)) {
      expect((await deliver(api, e)).status).toBe(200);
    }

    // 担当者が誤って「不在」を押した → 直後に顧客の「二度とかけてこないで」を「拒否」で記録しようとする
    const first = await call(api, "POST", `/v1/calls/${placed.id}/outcome`, {
      session,
      body: { outcome: "不在" },
    });
    expect(first.status).toBe(201);
    const dnc = await call(api, "POST", `/v1/calls/${placed.id}/outcome`, {
      session,
      body: { outcome: "拒否" },
    });
    // API には抑止を登録する別の経路がない。ここで拒否できないと DNC は永久に記録されない
    expect(dnc.status).not.toBe(409);

    const contact = await call(api, "GET", `/v1/contacts/${t.contactIds[0]}`, { session });
    expect(((await contact.json()) as { suppression: string }).suppression).toBe("SUPPRESSED");

    // 翌日（1日の上限が戻った後）に同じ相手へ発信しても、外部発信は生まれない
    api.clock.set(jst("2026-10-06T11:00:00"));
    const next = await login(api, operator.email); // セッションは 12 時間で切れるので翌日はログインし直す
    const again = await call(api, "POST", "/v1/calls", {
      session: next,
      body: { contactId: t.contactIds[0], campaignId: t.campaignId, mode: "HUMAN_DIALED" },
      headers: { "idempotency-key": "k-2" },
    });
    expect(again.status).toBe(422);
    expect(api.telephony.placed).toHaveLength(1);
  });
});

describe("IQA-02: プロバイダへ渡す冪等キーが組織で区別されていない（テナントをまたいで発信がまとめられる）", () => {
  it("組織 A と組織 B が同じ Idempotency-Key を使っても、それぞれ 1 件ずつ発信され、互いの通話に紐づかない", async () => {
    const a = await seedTenant(database, { phones: ["+819000000101"] });
    const b = await seedTenant(database, { phones: ["+819000000201"] });
    const opA = await seedUser(database, a.org, "OPERATOR");
    const opB = await seedUser(database, b.org, "OPERATOR");
    // 本番と同じく、1つのサーバー＝1つのプロバイダのインスタンスを全組織で共有する
    const api = buildApi(database);
    const sa = await login(api, opA.email);
    const sb = await login(api, opB.email);
    const key = "lead-1-attempt-1"; // 決定的なキー（連番・リード ID 由来）は現実に起こる
    const ra = await call(api, "POST", "/v1/calls", {
      session: sa,
      body: { contactId: a.contactIds[0], campaignId: a.campaignId, mode: "HUMAN_DIALED" },
      headers: { "idempotency-key": key },
    });
    const rb = await call(api, "POST", "/v1/calls", {
      session: sb,
      body: { contactId: b.contactIds[0], campaignId: b.campaignId, mode: "HUMAN_DIALED" },
      headers: { "idempotency-key": key },
    });
    expect(ra.status).toBe(201);
    // 組織 B の発信は、組織 A の発信として扱われてはならない
    expect(rb.status).toBe(201);
    expect(api.telephony.placed.map((p) => p.to)).toEqual(["+819000000101", "+819000000201"]);
    const rows = await database.db.execute<{ organization_id: string; status: string }>(
      sql`select organization_id, status from calls where organization_id in (${a.org}, ${b.org})`,
    );
    // B の通話が REQUESTED のまま放置されない（番号が「通話中」扱いで塞がれない）
    expect(rows.rows.find((r) => r.organization_id === b.org)?.status).not.toBe("REQUESTED");
  });
});

describe("IQA-05: Webhook のプロバイダ通話 ID が通話の記録と違っても、通話 ID だけで状態が動く", () => {
  it("記録済みの providerCallId と違う ID の Webhook は、その通話の状態を変えない", async () => {
    const { api, session, dial } = await world();
    const created = await dial("k-1");
    const { call: placed } = (await created.json()) as {
      call: { id: string; providerCallId: string };
    };
    const res = await deliver(api, {
      eventId: `other-leg:${randomUUID()}`,
      providerCallId: "MOCK-SOME-OTHER-CALL",
      callId: placed.id,
      status: "completed",
      occurredAt: api.clock.now().toISOString(),
    });
    expect(await res.json()).toEqual({ result: "UNKNOWN_CALL" });
    const now = await call(api, "GET", `/v1/calls/${placed.id}`, { session });
    expect(((await now.json()) as { call: { status: string } }).call.status).toBe("DIALING");
  });
});

describe("IQA-06: 入力の検証漏れで 500 になる（カーソル）", () => {
  it("NUL を含むカーソルは 400（DB のエラーにしない）", async () => {
    const { api, session } = await world();
    const cursor = Buffer.from(JSON.stringify({ n: "a\u0000b", i: randomUUID() })).toString(
      "base64url",
    );
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      const res = await call(api, "GET", `/v1/contacts?cursor=${cursor}`, { session });
      expect(res.status).toBe(400);
    } finally {
      spy.mockRestore();
    }
  });
});

describe("IQA-07: 想定外の DB の失敗で、完全な電話番号がサーバーのログに出る", () => {
  it("発信の保存が失敗しても、console.error に E.164 の完全な番号を出さない", async () => {
    const { api, dial } = await world();
    // 本番で起きうる DB の失敗（ディスク・タイムアウト・直列化失敗など）の代わり
    await database.db.execute(sql`
      create or replace function iqa_fail_insert() returns trigger language plpgsql as $$
      begin raise exception 'simulated storage failure'; end $$`);
    await database.db.execute(sql`
      create trigger iqa_fail_insert before insert on calls
      for each row when (new.to_e164 = '+819000000002') execute function iqa_fail_insert()`);
    const logged: string[] = [];
    const spy = vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
      logged.push(
        args.map((a) => (a instanceof Error ? `${a.message} ${a.stack}` : String(a))).join(" "),
      );
    });
    try {
      const res = await dial("k-fail", 1);
      expect(res.status).toBe(500);
      expect(api.telephony.placed).toHaveLength(0);
      expect(logged.join("\n")).not.toContain("+819000000002");
    } finally {
      spy.mockRestore();
      await database.db.execute(sql`drop trigger iqa_fail_insert on calls`);
    }
  });
});

describe("IQA-10: ログイン試行の制限は、同時に送られた試行を数え切る前に照合してしまう", () => {
  it("同じアドレスへ 20 件の誤ったパスワードを同時に送っても、照合（401）は上限の 5 件まで、残りは 429", async () => {
    const t = await seedTenant(database);
    const victim = await seedUser(database, t.org, "OWNER");
    const api = buildApi(database);
    const attempts = await Promise.all(
      Array.from({ length: 20 }, (_, i) =>
        api.app.request("/v1/auth/login", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ email: victim.email, password: `wrong-guess-${i}` }),
        }),
      ),
    );
    const statuses = attempts.map((r) => r.status);
    expect(statuses.filter((s) => s === 401).length).toBeLessThanOrEqual(5);
  });
});
