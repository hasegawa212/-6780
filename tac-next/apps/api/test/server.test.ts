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
