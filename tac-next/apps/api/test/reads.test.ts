import type { Database } from "@tac/db";
import { sql } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { buildApi, call, freshDatabase, login, seedTenant, seedUser } from "./support.js";

/*
 * Phase 9 の画面に必要な読み取り API：連絡先の一覧・詳細（抑止の状態つき）、キャンペーンの一覧。
 * 検索・絞り込み・取り込みは Phase 4。
 */

let database: Database;

beforeAll(async () => {
  database = await freshDatabase();
}, 60_000);

afterAll(async () => {
  await database.close();
});

async function world(phones = ["+819000000001", "+819000000002", "+819000000003"]) {
  const t = await seedTenant(database, { phones });
  const viewer = await seedUser(database, t.org, "VIEWER");
  const api = buildApi(database);
  const session = await login(api, viewer.email);
  return { t, api, session };
}

type ContactItem = { id: string; displayName: string; phone: string };

describe("GET /v1/contacts", () => {
  it("自分の組織の連絡先だけを、名前順に、電話番号をマスクして返す（VIEWER でも読める）", async () => {
    const { api, session, t } = await world();
    await seedTenant(database, { phones: ["+819000000009"] }); // 別の組織
    const res = await call(api, "GET", "/v1/contacts", { session });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { items: ContactItem[]; nextCursor: string | null };
    expect(body.items.map((c) => c.id).sort()).toEqual([...t.contactIds].sort());
    expect(body.items.map((c) => c.displayName)).toEqual(["架空 1", "架空 2", "架空 3"]);
    expect(body.items[0]?.phone).toBe("+8190****0001");
    expect(body.nextCursor).toBeNull();
  });

  it("limit とカーソルで全件を重複・欠落なく辿れる", async () => {
    const { api, session, t } = await world();
    const seen: string[] = [];
    let cursor: string | null = null;
    for (let page = 0; page < 5; page += 1) {
      const query: string = cursor ? `?limit=2&cursor=${encodeURIComponent(cursor)}` : "?limit=2";
      const res = await call(api, "GET", `/v1/contacts${query}`, { session });
      const body = (await res.json()) as { items: ContactItem[]; nextCursor: string | null };
      seen.push(...body.items.map((c) => c.id));
      cursor = body.nextCursor;
      if (!cursor) break;
    }
    expect(seen.sort()).toEqual([...t.contactIds].sort());
  });

  it("不正な limit・壊れたカーソルは 400", async () => {
    const { api, session } = await world();
    expect((await call(api, "GET", "/v1/contacts?limit=0", { session })).status).toBe(400);
    expect((await call(api, "GET", "/v1/contacts?limit=501", { session })).status).toBe(400);
    expect((await call(api, "GET", "/v1/contacts?cursor=%%%", { session })).status).toBe(400);
  });

  it("ログインしていなければ 401", async () => {
    const { api } = await world();
    expect((await call(api, "GET", "/v1/contacts")).status).toBe(401);
  });
});

describe("GET /v1/contacts/{id}", () => {
  it("抑止の状態を返す（NONE / SUPPRESSED）。別の組織の連絡先は 404", async () => {
    const { api, session, t } = await world();
    const [first = "", second = ""] = t.contactIds;
    await database.db.execute(sql`
      insert into suppression_entries (organization_id, phone_e164, reason, source, actor_id)
      values (${t.org}, '+819000000002', 'DO_NOT_CALL', 'test', 'test')`);
    const ok = await call(api, "GET", `/v1/contacts/${first}`, { session });
    expect(await ok.json()).toMatchObject({
      contact: { id: first, phone: "+8190****0001" },
      suppression: "NONE",
    });
    const blocked = await call(api, "GET", `/v1/contacts/${second}`, { session });
    expect(await blocked.json()).toMatchObject({ suppression: "SUPPRESSED" });

    const other = await seedTenant(database, { phones: ["+819000000008"] });
    const cross = await call(api, "GET", `/v1/contacts/${other.contactIds[0]}`, { session });
    expect(cross.status).toBe(404);
  });

  it("抑止の照会に失敗したら UNKNOWN（画面は発信ボタンを出さない）", async () => {
    const { api, session, t } = await world();
    api.deps.suppression.canContact = async () => {
      throw new Error("db down");
    };
    const res = await call(api, "GET", `/v1/contacts/${t.contactIds[0]}`, { session });
    expect(await res.json()).toMatchObject({ suppression: "UNKNOWN" });
  });
});

describe("GET /v1/campaigns", () => {
  it("自分の組織のキャンペーンだけを返す", async () => {
    const { api, session, t } = await world();
    await seedTenant(database);
    const res = await call(api, "GET", "/v1/campaigns", { session });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      items: [{ id: t.campaignId, product: "新築マンション", paused: false }],
    });
  });
});
