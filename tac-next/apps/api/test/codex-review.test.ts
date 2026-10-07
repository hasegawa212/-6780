import type { Database } from "@tac/db";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { buildApi, call, freshDatabase, login, seedTenant, seedUser } from "./support.js";

/* Codex のレビュー（PR #137）P1：発信されたか分からない失敗は、画面が「確定」と誤解しない応答にする */

let database: Database;
beforeAll(async () => {
  database = await freshDatabase();
}, 60_000);
afterAll(async () => {
  await database.close();
});

describe("Codex P1: PROVIDER_UNCERTAIN は「分からない」側の 5xx で返す", () => {
  it("プロバイダとの接続が切れたら 504 PROVIDER_UNCERTAIN（422 にしない）", async () => {
    const t = await seedTenant(database);
    const operator = await seedUser(database, t.org, "OPERATOR");
    const api = buildApi(database);
    const session = await login(api, operator.email);
    api.telephony.createCall = async () => {
      throw Object.assign(new Error("socket hang up"), { code: "ECONNRESET" });
    };
    const res = await call(api, "POST", "/v1/calls", {
      session,
      body: { contactId: t.contactIds[0], campaignId: t.campaignId, mode: "HUMAN_DIALED" },
      headers: { "idempotency-key": "k-uncertain" },
    });
    expect(res.status).toBe(504);
    expect(((await res.json()) as { error: { code: string } }).error.code).toBe(
      "PROVIDER_UNCERTAIN",
    );
  });
});
