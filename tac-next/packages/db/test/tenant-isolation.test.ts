import { randomUUID } from "node:crypto";
import type { OrganizationId } from "@tac/application";
import { sql } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  createPgDeps,
  type Database,
  InvalidOrganizationIdError,
  TenantMismatchError,
  TenantScope,
} from "../src/index.js";
import {
  callRecord,
  expectPermissionDenied,
  freshDatabase,
  jst,
  OPERATOR,
  phone,
  seedTenant,
  sqlState,
} from "./support.js";

/*
 * INV-2 テナント A はテナント B のデータに決してアクセスできない（DB 層）。
 * アプリのスコープ（WHERE organization_id）と RLS の二重。ここでは両方を別々に確かめる：
 * - リポジトリ経由：別テナントの ID を指定しても見えない
 * - 生の SQL 経由（RLS だけが頼り）：WHERE を書き忘れても別テナントの行は返らない
 */

let database: Database;
let scope: TenantScope;
let a: Awaited<ReturnType<typeof seedTenant>>;
let b: Awaited<ReturnType<typeof seedTenant>>;

beforeAll(async () => {
  database = await freshDatabase();
  scope = new TenantScope(database.db);
  a = await seedTenant(database, { companyName: "株式会社サンプル不動産" });
  b = await seedTenant(database, { companyName: "テスト住宅株式会社", phones: ["090-0000-0009"] });
}, 60_000);

afterAll(async () => {
  await database.close();
});

const contactB = () => b.contactIds[0] ?? "";

describe("リポジトリ経由：別テナントの ID を指定しても取れない", () => {
  it("組織・連絡先・キャンペーン", async () => {
    const deps = createPgDeps(scope);
    expect(await deps.organizations.get(b.org)).toMatchObject({ id: b.org });
    expect(await deps.contacts.get(a.org, contactB())).toBeUndefined();
    expect(await deps.campaigns.get(a.org, b.campaignId)).toBeUndefined();
    expect(await deps.contacts.get(b.org, contactB())).toMatchObject({ id: contactB() });
  });

  it("通話・結果・冪等キーの照会", async () => {
    const deps = createPgDeps(scope);
    const call = callRecord(b.org, contactB(), b.campaignId, { to: phone("090-0000-0009") });
    await deps.calls.insert(call);
    await deps.outcomes.insert({
      callId: call.id,
      organizationId: b.org,
      code: "INTERESTED",
      recordedBy: OPERATOR,
      recordedAt: jst("2026-10-05T10:05:00"),
    });
    expect(await deps.calls.get(a.org, call.id)).toBeUndefined();
    expect(await deps.calls.findByIdempotencyKey(a.org, call.idempotencyKey)).toBeUndefined();
    expect(await deps.outcomes.get(a.org, call.id)).toBeUndefined();
    expect(await deps.calls.countActive(a.org, new Date(0))).toBe(0);
    expect(await deps.calls.get(b.org, call.id)).toMatchObject({ id: call.id });
  });

  it("抑止：B の抑止は A の発信可否に影響しない（逆も同じ）", async () => {
    const deps = createPgDeps(scope);
    const shared = phone("090-1111-2222");
    await deps.suppression.add({
      organizationId: b.org,
      phone: shared,
      reason: "DO_NOT_CALL",
      source: "test",
      actorId: OPERATOR,
    });
    expect(await deps.suppression.canContact(b.org, shared)).toBe(false);
    expect(await deps.suppression.canContact(a.org, shared)).toBe(true);
  });

  it("別テナントの通話の更新は、行が見つからない扱いで失敗する（上書きできない）", async () => {
    const deps = createPgDeps(scope);
    const call = callRecord(b.org, contactB(), b.campaignId, {
      to: phone("090-0000-0009"),
      status: "ENDED",
    });
    await deps.calls.insert(call);
    await expect(
      deps.calls.update({ ...call, organizationId: a.org, status: "FAILED" }),
    ).rejects.toThrow();
    expect(await deps.calls.get(b.org, call.id)).toMatchObject({ status: "ENDED" });
  });
});

describe("1つのトランザクションは1つの組織だけ", () => {
  it("UnitOfWork の中で別の組織を扱おうとしたら TenantMismatchError", async () => {
    const deps = createPgDeps(scope);
    await expect(
      deps.uow.run(async () => {
        await deps.contacts.get(a.org, a.contactIds[0] ?? "");
        await deps.contacts.get(b.org, contactB());
      }),
    ).rejects.toBeInstanceOf(TenantMismatchError);
  });

  it("UUID でない組織 ID では問い合わせず失敗する（抑止の照会は失敗 = 発信しない）", async () => {
    const deps = createPgDeps(scope);
    await expect(
      deps.suppression.canContact("org-a" as OrganizationId, phone("090-0000-0001")),
    ).rejects.toBeInstanceOf(InvalidOrganizationIdError);
  });
});

describe("生の SQL 経由：RLS だけで別テナントの行が遮断される", () => {
  const countIn = (org: OrganizationId | undefined, table: string) =>
    scope.withTenant(org, async (tx) => {
      const r = await tx.execute<{ n: number }>(
        sql`select count(*)::int as n from ${sql.identifier(table)}`,
      );
      return r.rows[0]?.n;
    });

  it.each(["organizations", "contacts", "campaigns"])(
    "%s：WHERE なしでも自分の組織の行しか見えない",
    async (table) => {
      const seenByA = await countIn(a.org, table);
      const owner = await database.db.execute<{ n: number }>(
        sql`select count(*)::int as n from ${sql.identifier(table)} where ${sql.identifier(table === "organizations" ? "id" : "organization_id")} = ${a.org}`,
      );
      expect(seenByA).toBe(owner.rows[0]?.n);
      expect(seenByA).toBeGreaterThan(0);
    },
  );

  it("組織が未設定なら、どのテナントの行も見えない（fail closed）", async () => {
    expect(await countIn(undefined, "contacts")).toBe(0);
    expect(await countIn(undefined, "organizations")).toBe(0);
  });

  it("自分の組織として、別の組織の行を挿入できない（RLS の WITH CHECK 違反 = 42501）", async () => {
    await expectPermissionDenied(
      scope.withTenant(a.org, (tx) =>
        tx.execute(sql`
          insert into contacts (id, organization_id, display_name, phone_e164)
          values (${randomUUID()}, ${b.org}, '侵入', '+819000000099')`),
      ),
    );
  });

  it("別テナントの連絡先を参照する通話は作れない（複合外部キー）", async () => {
    const deps = createPgDeps(scope);
    const error = await deps.calls
      .insert(callRecord(a.org, contactB(), a.campaignId, { to: phone("090-0000-0009") }))
      .then(
        () => undefined,
        (e: unknown) => e,
      );
    expect(sqlState(error)).toBe("23503"); // foreign_key_violation
  });
});

describe("アプリのロールの権限", () => {
  it("監査ログは追記専用（UPDATE / DELETE は権限エラー）", async () => {
    const deps = createPgDeps(scope);
    await deps.audit.append({
      organizationId: a.org,
      actorId: OPERATOR,
      action: "test.appended",
      resource: "test:1",
      at: jst("2026-10-05T10:00:00"),
      after: { ok: true },
    });
    await expectPermissionDenied(
      scope.withTenant(a.org, (tx) => tx.execute(sql`update audit_logs set action = 'tampered'`)),
    );
    await expectPermissionDenied(
      scope.withTenant(a.org, (tx) => tx.execute(sql`delete from audit_logs`)),
    );
  });

  it("抑止はアプリから削除・解除できない（INV-1：抑止を消す経路を DB で塞ぐ）", async () => {
    await expectPermissionDenied(
      scope.withTenant(a.org, (tx) => tx.execute(sql`delete from suppression_entries`)),
    );
    await expectPermissionDenied(
      scope.withTenant(a.org, (tx) =>
        tx.execute(sql`update suppression_entries set lifted_at = now()`),
      ),
    );
  });

  it("全発信停止のフラグはアプリから書き換えられない（読むだけ）", async () => {
    await expectPermissionDenied(
      scope.withTenant(a.org, (tx) =>
        tx.execute(sql`update system_controls set outbound_stopped = false`),
      ),
    );
  });
});
