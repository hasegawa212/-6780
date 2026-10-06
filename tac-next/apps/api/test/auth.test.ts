import type { Database } from "@tac/db";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { ScryptPasswordHasher } from "../src/index.js";
import { buildApi, call, freshDatabase, login, PASSWORD, seedTenant, seedUser } from "./support.js";

/*
 * Phase 3：認証・セッション・CSRF（INV-2 の API 層の入口）。
 */

let database: Database;
let api: ReturnType<typeof buildApi>;
let user: Awaited<ReturnType<typeof seedUser>>;

beforeAll(async () => {
  database = await freshDatabase();
  const t = await seedTenant(database);
  user = await seedUser(database, t.org, "OPERATOR");
  api = buildApi(database);
}, 60_000);

afterAll(async () => {
  await database.close();
});

const postLogin = (body: unknown, headers: Record<string, string> = {}) =>
  api.app.request("/v1/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });

describe("POST /v1/auth/login", () => {
  it("成功すると HttpOnly・SameSite=Lax の Cookie を発行し、本文には CSRF トークンだけを返す", async () => {
    const res = await postLogin({ email: user.email, password: PASSWORD });
    expect(res.status).toBe(200);
    const cookie = res.headers.get("set-cookie") ?? "";
    expect(cookie).toMatch(/^tac_session=[A-Za-z0-9_-]{43}; /);
    expect(cookie).toMatch(/HttpOnly/);
    expect(cookie).toMatch(/SameSite=Lax/);
    expect(cookie).toMatch(/Path=\//);
    expect(cookie).not.toMatch(/Secure/); // local（cookieSecure=false）
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toMatchObject({ role: "OPERATOR", displayName: "佐藤" });
    expect(typeof body.csrfToken).toBe("string");
    const sessionToken = cookie.split(";")[0]?.split("=")[1] ?? "";
    expect(JSON.stringify(body)).not.toContain(sessionToken);
  });

  it("本番（cookieSecure=true）では Secure を付ける", async () => {
    const secureApi = buildApi(database, { api: { cookieSecure: true } });
    const res = await secureApi.app.request("/v1/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email: user.email, password: PASSWORD }),
    });
    expect(res.headers.get("set-cookie")).toMatch(/Secure/);
  });

  it("パスワード違いと存在しないユーザーは、同じ 401 の応答", async () => {
    const wrong = await postLogin({ email: user.email, password: "wrong-password" });
    const unknown = await postLogin({ email: "nobody@example.test", password: PASSWORD });
    expect(wrong.status).toBe(401);
    expect(unknown.status).toBe(401);
    const a = (await wrong.json()) as { error: { code: string; message: string } };
    const b = (await unknown.json()) as { error: { code: string; message: string } };
    expect(a.error.code).toBe("INVALID_CREDENTIALS");
    expect([b.error.code, b.error.message]).toEqual([a.error.code, a.error.message]);
    expect(wrong.headers.get("set-cookie")).toBeNull();
  });

  it("JSON でない要求は 415（フォームの自動送信による CSRF でログインさせない）", async () => {
    const res = await postLogin(`email=${user.email}&password=x`, {
      "content-type": "application/x-www-form-urlencoded",
    });
    expect(res.status).toBe(415);
  });

  it("不正な本文は 400。応答にパスワードを含めない", async () => {
    const res = await postLogin({ email: "not-an-email", password: "secret-value-123" });
    expect(res.status).toBe(400);
    const text = await res.text();
    expect(text).toContain("VALIDATION_FAILED");
    expect(text).not.toContain("secret-value-123");
  });

  it("壊れた JSON は 400", async () => {
    const res = await postLogin("{not json");
    expect(res.status).toBe(400);
  });
});

describe("ログイン試行の制限", () => {
  it("同じメールアドレスで 5 回失敗したら 429（Retry-After つき）。存在しないアドレスでも同じ応答", async () => {
    const t = await seedTenant(database);
    const target = await seedUser(database, t.org);
    const limited = buildApi(database);
    const attempt = (email: string, password: string) =>
      limited.app.request("/v1/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
    for (const email of [target.email, "nobody-limit@example.test"]) {
      for (let i = 0; i < 5; i += 1)
        expect((await attempt(email, "wrong-password")).status).toBe(401);
      const res = await attempt(email, PASSWORD);
      expect(res.status).toBe(429);
      expect(res.headers.get("retry-after")).toBe("900");
      expect(await res.json()).toMatchObject({ error: { code: "LOGIN_LOCKED" } });
    }
  });

  it("要求元の IP ごとにも数える（clientIp の取り出しは設定で決める）", async () => {
    const t = await seedTenant(database);
    const target = await seedUser(database, t.org);
    const limited = buildApi(database, { api: { clientIp: (c) => c.req.header("x-test-ip") } });
    const attempt = (email: string, ip: string, password = "wrong-password") =>
      limited.app.request("/v1/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json", "x-test-ip": ip },
        body: JSON.stringify({ email, password }),
      });
    for (let i = 0; i < 50; i += 1) await attempt(`spray-${i}@example.test`, "203.0.113.9");
    expect((await attempt(target.email, "203.0.113.9", PASSWORD)).status).toBe(429);
    expect((await attempt(target.email, "198.51.100.2", PASSWORD)).status).toBe(200);
  });
});

describe("セッションと CSRF", () => {
  it("GET /v1/me：Cookie がなければ 401、あれば自分と組織・ロールを返す", async () => {
    expect((await call(api, "GET", "/v1/me")).status).toBe(401);
    const session = await login(api, user.email);
    const res = await call(api, "GET", "/v1/me", { session });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ userId: user.id, role: "OPERATOR" });
  });

  it("偽の Cookie は 401", async () => {
    const res = await call(api, "GET", "/v1/me", {
      session: { cookie: "tac_session=forged", csrf: "x" },
    });
    expect(res.status).toBe(401);
  });

  it("状態を変える要求は CSRF トークンが必要（無い・違うなら 403）", async () => {
    const session = await login(api, user.email);
    const missing = await call(api, "POST", "/v1/auth/logout", { session, csrf: false });
    expect(missing.status).toBe(403);
    expect(await missing.json()).toMatchObject({ error: { code: "CSRF_TOKEN_INVALID" } });
    const wrong = await call(api, "POST", "/v1/auth/logout", {
      session: { ...session, csrf: "wrong" },
    });
    expect(wrong.status).toBe(403);
  });

  it("別のセッションの CSRF トークンは使えない", async () => {
    const a = await login(api, user.email);
    const b = await login(api, user.email);
    const res = await call(api, "POST", "/v1/auth/logout", {
      session: { cookie: a.cookie, csrf: b.csrf },
    });
    expect(res.status).toBe(403);
  });

  it("ログアウトしたら、同じ Cookie は使えない", async () => {
    const session = await login(api, user.email);
    const out = await call(api, "POST", "/v1/auth/logout", { session });
    expect(out.status).toBe(204);
    expect(out.headers.get("set-cookie")).toMatch(/tac_session=;/);
    expect((await call(api, "GET", "/v1/me", { session })).status).toBe(401);
  });

  it("セッションは 12 時間で切れる", async () => {
    const session = await login(api, user.email);
    const now = api.clock.now();
    api.clock.set(new Date(now.getTime() + 12 * 60 * 60 * 1000));
    try {
      expect((await call(api, "GET", "/v1/me", { session })).status).toBe(401);
    } finally {
      api.clock.set(now);
    }
  });
});

describe("共通の応答", () => {
  it("すべての応答に request id とセキュリティヘッダーが付く", async () => {
    const res = await call(api, "GET", "/v1/me");
    expect(res.headers.get("x-request-id")).toMatch(/^[0-9a-f-]{36}$/);
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("x-frame-options")).toBe("DENY");
    expect(res.headers.get("content-security-policy")).toContain("default-src 'none'");
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("エラーは { error: { code, message, requestId } } の形で、スタックトレースを含まない", async () => {
    const res = await call(api, "GET", "/v1/me");
    const body = (await res.json()) as { error: Record<string, unknown> };
    expect(Object.keys(body.error).sort()).toEqual(["code", "message", "requestId"]);
    expect(body.error.requestId).toBe(res.headers.get("x-request-id"));
  });

  it("知らないパスは 404（同じエラー形式）", async () => {
    const res = await call(api, "GET", "/v1/nothing-here");
    expect(res.status).toBe(404);
    expect(await res.json()).toMatchObject({ error: { code: "NOT_FOUND" } });
  });

  it("64KB を超える本文は 413", async () => {
    const res = await postLogin({ email: user.email, password: "x".repeat(70 * 1024) });
    expect(res.status).toBe(413);
  });
});

describe("ScryptPasswordHasher（本番の既定値）", () => {
  it("既定は N=2^17・r=8・p=1。同じパスワードでも毎回別のハッシュ（ソルト）で、検証できる", async () => {
    const h = new ScryptPasswordHasher();
    const a = await h.hash("pw-123456789");
    const b = await h.hash("pw-123456789");
    expect(a).toMatch(/^scrypt\$17\$8\$1\$[A-Za-z0-9_-]{22}\$[A-Za-z0-9_-]{43}$/);
    expect(a).not.toBe(b);
    expect(await h.verify("pw-123456789", a)).toBe(true);
    expect(await h.verify("pw-12345678", a)).toBe(false);
    expect(await h.verify("pw-123456789", "garbage")).toBe(false);
  }, 30_000);

  it("弱すぎるコスト（N < 2^10）は作れない", () => {
    expect(() => new ScryptPasswordHasher({ logN: 8 })).toThrow();
  });
});
