import { createPgliteDatabase } from "@tac/db";
import { describe, expect, it } from "vitest";
import { startServer } from "../src/server.js";

/*
 * 起動の組み立て（設定 → DB → マイグレーション → 電話プロバイダ → HTTP）。
 * 実際にポートを開いて HTTP で叩く（本物の電話はかけない：mock のみ）。
 */

const TEST_ENV = {
  APP_ENV: "test",
  MOCK_WEBHOOK_SECRET: "test-webhook-secret-at-least-32-chars",
};

describe("startServer", () => {
  it("test 環境では PGlite を用意・マイグレーションして起動し、HTTP で応答する", async () => {
    const server = await startServer(TEST_ENV, { log: () => {}, port: 0 });
    try {
      const me = await fetch(`${server.url}/v1/me`);
      expect(me.status).toBe(401);
      expect(me.headers.get("x-request-id")).toBeTruthy();
      // mock の Webhook の受け口は test では開いている（署名なしは 401）
      const hook = await fetch(`${server.url}/v1/webhooks/mock`, { method: "POST", body: "{}" });
      expect(hook.status).toBe(401);
    } finally {
      await server.close();
    }
  }, 60_000);

  it("local の補助：デモ用のシードでログインでき、発信するとシミュレーターの Webhook が自動で届いて終話まで進む", async () => {
    const server = await startServer(
      {
        ...TEST_ENV,
        DEV_SEED_PASSWORD: "demo-password-123",
        OUTBOUND_CALLS_ENABLED: "true",
      },
      { log: () => {}, port: 0 },
    );
    try {
      const login = await fetch(`${server.url}/v1/auth/login`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: "operator@example.test", password: "demo-password-123" }),
      });
      expect(login.status).toBe(200);
      const { csrfToken } = (await login.json()) as { csrfToken: string };
      const cookie = (login.headers.get("set-cookie") ?? "").split(";")[0] ?? "";
      const headers = { cookie, "content-type": "application/json", "x-csrf-token": csrfToken };
      const contacts = (await (await fetch(`${server.url}/v1/contacts`, { headers })).json()) as {
        items: { id: string }[];
      };
      const campaigns = (await (await fetch(`${server.url}/v1/campaigns`, { headers })).json()) as {
        items: { id: string }[];
      };
      expect(contacts.items.length).toBeGreaterThanOrEqual(3);
      const placed = await fetch(`${server.url}/v1/calls`, {
        method: "POST",
        headers: { ...headers, "idempotency-key": "demo-1" },
        body: JSON.stringify({
          contactId: contacts.items[0]?.id,
          campaignId: campaigns.items[0]?.id,
          mode: "HUMAN_DIALED",
        }),
      });
      expect(placed.status).toBe(201);
      const { call } = (await placed.json()) as { call: { id: string } };
      let status = "";
      for (let i = 0; i < 60 && status !== "ENDED"; i += 1) {
        await new Promise((r) => setTimeout(r, 200));
        const r = await fetch(`${server.url}/v1/calls/${call.id}`, { headers });
        status = ((await r.json()) as { call: { status: string } }).call.status;
      }
      expect(status).toBe("ENDED");
    } finally {
      await server.close();
    }
  }, 60_000);

  it("シードは 2 回起動しても重複しない（同じ DB）", async () => {
    const database = await createPgliteDatabase();
    const env = { ...TEST_ENV, DEV_SEED_PASSWORD: "demo-password-123" };
    for (let i = 0; i < 2; i += 1) {
      const server = await startServer(env, { log: () => {}, database, port: 0 });
      await server.close();
    }
    const { sql } = await import("drizzle-orm");
    const users = await database.db.execute<{ n: number }>(
      sql`select count(*)::int as n from users where email = 'operator@example.test'`,
    );
    expect(users.rows[0]?.n).toBe(1);
    await database.close();
  }, 60_000);

  it("設定が不正なら起動しない。エラーには項目名だけを出し、値は出さない", async () => {
    // DATABASE_URL も形式が不正（ポート番号なし）で、パスワードを含む
    const secretish = "mysql://user:super-secret-password@db";
    const error = await startServer(
      { ...TEST_ENV, PORT: "not-a-port", DATABASE_URL: secretish },
      { log: () => {}, port: 0 },
    ).then(
      () => undefined,
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(Error);
    const message = (error as Error).message;
    expect(message).toMatch(/PORT/);
    expect(message).toMatch(/DATABASE_URL/);
    expect(message).not.toContain("super-secret-password");
  });

  it("production では、未適用のマイグレーションがあれば起動しない（自動では適用しない）", async () => {
    const database = await createPgliteDatabase();
    await expect(
      startServer(
        {
          APP_ENV: "production",
          DATABASE_URL: "postgres://tac:pw@db.internal:5432/tac",
          SESSION_SECRET: "s".repeat(32),
        },
        { log: () => {}, database, port: 0 },
      ),
    ).rejects.toThrow(/pending migrations/);
    await database.close();
  });

  it("production では mock の Webhook の受け口を開かない", async () => {
    const database = await createPgliteDatabase();
    const { migrate } = await import("@tac/db");
    await migrate(database.db);
    const server = await startServer(
      {
        APP_ENV: "production",
        DATABASE_URL: "postgres://tac:pw@db.internal:5432/tac",
        SESSION_SECRET: "s".repeat(32),
        MOCK_WEBHOOK_SECRET: "m".repeat(32),
      },
      { log: () => {}, database, port: 0 },
    );
    try {
      const hook = await fetch(`${server.url}/v1/webhooks/mock`, { method: "POST", body: "{}" });
      expect(hook.status).toBe(404);
    } finally {
      await server.close();
    }
  }, 60_000);
});
