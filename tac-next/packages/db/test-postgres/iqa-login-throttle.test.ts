import { randomUUID } from "node:crypto";
import { LoginUseCase, type OrganizationId, type UserId } from "@tac/application";
import {
  FixedClock,
  InMemoryAuditLog,
  InMemoryAuthDirectory,
  InMemorySessions,
  SequentialTokens,
} from "@tac/application/testing";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  createNodePostgresDatabase,
  createPgAuthStores,
  type Database,
  migrate,
  TenantScope,
} from "../src/index.js";

/*
 * 独立 QA（IQA-10, 2026-10-06）：実 PostgreSQL（複数接続）で、ログイン試行の制限が
 * 「ロックの確認 → 照合 → 失敗の記録」の間の競合で破れることを確かめる。修正されるまで RED。
 */

const url = process.env.TEST_DATABASE_URL;
if (!url) throw new Error("TEST_DATABASE_URL が未設定です（pnpm test:postgres は実 DB が必須）");
let database: Database;

beforeAll(async () => {
  database = createNodePostgresDatabase(url, 12);
  await migrate(database.db);
}, 60_000);

afterAll(async () => {
  await database?.close();
});

describe("IQA-10（実 PG）: 同時に送られたログイン試行", () => {
  it("同じアドレスへ 30 件の誤ったパスワードを同時に送っても、照合されるのは上限の 5 件まで", async () => {
    const { throttle } = createPgAuthStores(new TenantScope(database.db));
    const directory = new InMemoryAuthDirectory();
    const email = `victim-${randomUUID()}@example.test`;
    directory.add({
      userId: randomUUID() as UserId,
      email,
      displayName: "架空",
      passwordHash: "plain:the-real-password-123",
      memberships: [{ organizationId: randomUUID() as OrganizationId, role: "OWNER" }],
    });
    let verifications = 0;
    const slowHasher = {
      // 本番の scrypt（N=2^17）は 1 回に数十〜数百 ms かかる
      async verify(password: string, hash: string) {
        verifications += 1;
        await new Promise((r) => setTimeout(r, 50));
        return hash === `plain:${password}`;
      },
      async verifyDummy() {
        verifications += 1;
      },
    };
    const deps = {
      clock: new FixedClock(new Date("2026-10-05T01:00:00Z")),
      directory,
      sessions: new InMemorySessions(directory),
      passwords: slowHasher,
      tokens: new SequentialTokens(),
      audit: new InMemoryAuditLog(),
      throttle,
    };
    const uc = new LoginUseCase(deps);
    await Promise.all(
      Array.from({ length: 30 }, (_, i) => uc.execute({ email, password: `guess-${i}` })),
    );
    expect(verifications).toBeLessThanOrEqual(5);
  });
});
