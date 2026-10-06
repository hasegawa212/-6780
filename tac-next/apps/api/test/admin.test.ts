import type { Database } from "@tac/db";
import { sql } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { AdminError, createUser, ScryptPasswordHasher } from "../src/index.js";
import { buildApi, freshDatabase, seedTenant } from "./support.js";

/*
 * 初期管理者・ユーザーの作成（運用の CLI から使う。DB の所有者の接続で書き込む）。
 */

let database: Database;
const hasher = new ScryptPasswordHasher({ logN: 10 });
const PASSWORD = "a-long-enough-password";

beforeAll(async () => {
  database = await freshDatabase();
}, 60_000);

afterAll(async () => {
  await database.close();
});

const login = (api: ReturnType<typeof buildApi>, email: string, password = PASSWORD) =>
  api.app.request("/v1/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

describe("createUser", () => {
  it("新しい組織と OWNER を作り、そのアカウントでログインできる。監査ログに残る", async () => {
    const r = await createUser(database, hasher, {
      email: " Owner@Example.TEST ",
      displayName: "初期管理者",
      role: "OWNER",
      password: PASSWORD,
      organization: { newName: "新規不動産株式会社" },
    });
    const res = await login(buildApi(database), "owner@example.test");
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ organizationId: r.organizationId, role: "OWNER" });
    const audit = await database.db.execute<{ action: string; actor_id: string }>(
      sql`select action, actor_id from audit_logs where organization_id = ${r.organizationId}`,
    );
    expect(audit.rows).toContainEqual({ action: "admin.user_created", actor_id: "cli" });
    // パスワードは scrypt のハッシュだけが保存される
    const stored = await database.db.execute<{ password_hash: string }>(
      sql`select password_hash from users where id = ${r.userId}`,
    );
    expect(stored.rows[0]?.password_hash).toMatch(/^scrypt\$/);
    expect(stored.rows[0]?.password_hash).not.toContain(PASSWORD);
  });

  it("既存の組織に OPERATOR を追加できる", async () => {
    const t = await seedTenant(database);
    const r = await createUser(database, hasher, {
      email: "new-operator@example.test",
      displayName: "新しい担当",
      role: "OPERATOR",
      password: PASSWORD,
      organization: { id: t.org },
    });
    expect(r.organizationId).toBe(t.org);
    expect((await login(buildApi(database), "new-operator@example.test")).status).toBe(200);
  });

  it.each([
    [{ password: "short" }, "WEAK_PASSWORD"],
    [{ password: "dup@example.test", email: "dup@example.test" }, "WEAK_PASSWORD"],
    [{ email: "not-an-email" }, "INVALID_EMAIL"],
    [{ role: "SUPERUSER" }, "INVALID_ROLE"],
    [{ displayName: "  " }, "INVALID_DISPLAY_NAME"],
    [{ organization: { id: "00000000-0000-4000-8000-000000000000" } }, "ORGANIZATION_NOT_FOUND"],
  ] as const)("不正な入力 %j は %s で拒否し、何も作らない", async (over, code) => {
    const before = await database.db.execute<{ n: number }>(
      sql`select count(*)::int as n from users`,
    );
    await expect(
      createUser(database, hasher, {
        email: `ok-${Math.random()}@example.test`,
        displayName: "担当",
        role: "OPERATOR",
        password: PASSWORD,
        organization: { newName: "組織" },
        ...over,
      } as Parameters<typeof createUser>[2]),
    ).rejects.toMatchObject({ code });
    const after = await database.db.execute<{ n: number }>(
      sql`select count(*)::int as n from users`,
    );
    expect(after.rows[0]?.n).toBe(before.rows[0]?.n);
  });

  it("同じメールアドレスのユーザーは作れない", async () => {
    const input = {
      email: "twice@example.test",
      displayName: "担当",
      role: "OPERATOR" as const,
      password: PASSWORD,
      organization: { newName: "組織 A" },
    };
    await createUser(database, hasher, input);
    await expect(createUser(database, hasher, input)).rejects.toBeInstanceOf(AdminError);
    await expect(createUser(database, hasher, input)).rejects.toMatchObject({
      code: "USER_EXISTS",
    });
  });
});
