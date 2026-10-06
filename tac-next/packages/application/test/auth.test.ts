import { describe, expect, it } from "vitest";
import {
  hasRole,
  LoginUseCase,
  LogoutUseCase,
  ResolveSessionUseCase,
  type Role,
} from "../src/auth.js";
import type { OrganizationId, UserId } from "../src/ports.js";
import {
  FixedClock,
  InMemoryAuditLog,
  InMemoryAuthDirectory,
  InMemorySessions,
  PlainTextPasswordHasher,
  SequentialTokens,
} from "../src/testing/in-memory.js";

/*
 * Phase 3：ログイン・セッション・ロール。
 * - 存在しないユーザーとパスワード違いは同じ応答（アカウントの有無を漏らさない）
 * - セッションの ID と CSRF トークンは、保存するときはハッシュだけ（DB が漏れても使えない）
 * - organization_id はセッションから取る。所属していない組織は選べない
 */

const ORG_A = "00000000-0000-4000-8000-00000000000a" as OrganizationId;
const ORG_B = "00000000-0000-4000-8000-00000000000b" as OrganizationId;
const USER = "00000000-0000-4000-8000-0000000000f1" as UserId;

function setup(
  memberships: { organizationId: OrganizationId; role: Role }[] = [
    { organizationId: ORG_A, role: "OPERATOR" },
  ],
) {
  const clock = new FixedClock(new Date("2026-10-05T01:00:00Z"));
  const directory = new InMemoryAuthDirectory();
  directory.add({
    userId: USER,
    email: "operator@example.test",
    displayName: "佐藤",
    passwordHash: "plain:correct horse battery staple",
    memberships,
  });
  const sessions = new InMemorySessions(directory);
  const deps = {
    clock,
    directory,
    sessions,
    passwords: new PlainTextPasswordHasher(),
    tokens: new SequentialTokens(),
    audit: new InMemoryAuditLog(),
  };
  return { deps, clock, sessions, directory };
}

const login = (deps: ReturnType<typeof setup>["deps"], over: Record<string, unknown> = {}) =>
  new LoginUseCase(deps).execute({
    email: "operator@example.test",
    password: "correct horse battery staple",
    ...over,
  });

describe("LoginUseCase", () => {
  it("正しいパスワードでセッションを作る。保存されるのは ID と CSRF のハッシュだけ", async () => {
    const { deps, sessions } = setup();
    const r = await login(deps);
    expect(r).toMatchObject({
      ok: true,
      value: { organizationId: ORG_A, role: "OPERATOR", displayName: "佐藤" },
    });
    if (!r.ok) return;
    const [stored] = sessions.rows.values();
    expect(stored?.idHash).toBe(deps.tokens.hash(r.value.sessionToken));
    expect(stored?.csrfHash).toBe(deps.tokens.hash(r.value.csrfToken));
    expect(JSON.stringify([...sessions.rows.values()])).not.toContain(r.value.sessionToken);
    expect(r.value.expiresAt.getTime() - deps.clock.now().getTime()).toBe(12 * 60 * 60 * 1000);
    expect(deps.audit.entries.map((e) => e.action)).toEqual(["auth.login"]);
  });

  it("メールアドレスは大文字・前後の空白を区別しない", async () => {
    const { deps } = setup();
    expect((await login(deps, { email: "  Operator@Example.TEST " })).ok).toBe(true);
  });

  it("パスワード違いと存在しないユーザーは、同じ INVALID_CREDENTIALS", async () => {
    const { deps } = setup();
    const wrong = await login(deps, { password: "wrong" });
    const unknown = await login(deps, { email: "nobody@example.test" });
    expect(wrong).toEqual({ ok: false, error: { code: "INVALID_CREDENTIALS" } });
    expect(unknown).toEqual(wrong);
    // 存在しないユーザーでもハッシュの検証は行う（応答時間で有無を推測させない）
    expect(deps.passwords.verifications).toBe(2);
  });

  it("無効化されたユーザーはログインできない", async () => {
    const { deps, directory } = setup();
    directory.disable(USER);
    expect(await login(deps)).toEqual({ ok: false, error: { code: "INVALID_CREDENTIALS" } });
  });

  it("どの組織にも所属していなければログインできない", async () => {
    const { deps } = setup([]);
    expect(await login(deps)).toEqual({ ok: false, error: { code: "INVALID_CREDENTIALS" } });
  });

  it("複数の組織に所属しているなら、組織を選ばないとログインできない。所属していない組織は選べない", async () => {
    const { deps } = setup([
      { organizationId: ORG_A, role: "OPERATOR" },
      { organizationId: ORG_B, role: "ADMIN" },
    ]);
    expect(await login(deps)).toEqual({ ok: false, error: { code: "ORGANIZATION_REQUIRED" } });
    expect(await login(deps, { organizationId: ORG_B })).toMatchObject({
      ok: true,
      value: { organizationId: ORG_B, role: "ADMIN" },
    });
    expect(await login(deps, { organizationId: "00000000-0000-4000-8000-0000000000cc" })).toEqual({
      ok: false,
      error: { code: "ORGANIZATION_NOT_ALLOWED" },
    });
  });

  it("パスワードが違うときは、組織の選択の誤りより先に INVALID_CREDENTIALS（所属を漏らさない）", async () => {
    const { deps } = setup([
      { organizationId: ORG_A, role: "OPERATOR" },
      { organizationId: ORG_B, role: "ADMIN" },
    ]);
    expect(await login(deps, { password: "wrong" })).toEqual({
      ok: false,
      error: { code: "INVALID_CREDENTIALS" },
    });
  });
});

describe("ResolveSessionUseCase / LogoutUseCase", () => {
  it("トークンからセッションを引ける。期限切れ・取り消し済み・知らないトークンは undefined", async () => {
    const { deps, clock } = setup();
    const r = await login(deps);
    if (!r.ok) throw new Error("setup");
    const resolve = new ResolveSessionUseCase(deps);
    expect(await resolve.execute(r.value.sessionToken)).toMatchObject({
      userId: USER,
      organizationId: ORG_A,
      role: "OPERATOR",
    });
    expect(await resolve.execute("not-a-token")).toBeUndefined();

    clock.set(new Date(r.value.expiresAt.getTime()));
    expect(await resolve.execute(r.value.sessionToken)).toBeUndefined();
  });

  it("ログアウトしたセッションは使えない", async () => {
    const { deps } = setup();
    const r = await login(deps);
    if (!r.ok) throw new Error("setup");
    await new LogoutUseCase(deps).execute(r.value.sessionToken);
    expect(await new ResolveSessionUseCase(deps).execute(r.value.sessionToken)).toBeUndefined();
  });

  it("ログインのたびに新しいセッション（前のトークンを使い回さない＝固定化対策）", async () => {
    const { deps } = setup();
    const first = await login(deps);
    const second = await login(deps);
    if (!first.ok || !second.ok) throw new Error("setup");
    expect(first.value.sessionToken).not.toBe(second.value.sessionToken);
    expect(first.value.csrfToken).not.toBe(second.value.csrfToken);
  });

  it("所属を外されたユーザーのセッションは、その時点で使えなくなる", async () => {
    const { deps, directory } = setup();
    const r = await login(deps);
    if (!r.ok) throw new Error("setup");
    directory.removeMembership(USER, ORG_A);
    expect(await new ResolveSessionUseCase(deps).execute(r.value.sessionToken)).toBeUndefined();
  });

  it("ロールが変わったら、次の要求から新しいロールで判定する", async () => {
    const { deps, directory } = setup();
    const r = await login(deps);
    if (!r.ok) throw new Error("setup");
    directory.setRole(USER, ORG_A, "VIEWER");
    expect(await new ResolveSessionUseCase(deps).execute(r.value.sessionToken)).toMatchObject({
      role: "VIEWER",
    });
  });
});

describe("hasRole", () => {
  it.each([
    ["OWNER", "ADMIN", true],
    ["ADMIN", "OPERATOR", true],
    ["OPERATOR", "OPERATOR", true],
    ["VIEWER", "OPERATOR", false],
    ["MANAGER", "ADMIN", false],
  ] as const)("%s は %s 以上か → %s", (role, min, expected) => {
    expect(hasRole(role, min)).toBe(expected);
  });
});
