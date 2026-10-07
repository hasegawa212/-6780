import { describe, expect, it } from "vitest";
import { LoginUseCase } from "../src/auth.js";
import { CreateCallUseCase } from "../src/create-call.js";
import type { OrganizationId, UserId } from "../src/ports.js";
import { RecordOutcomeUseCase } from "../src/record-outcome.js";
import {
  FixedClock,
  InMemoryAuditLog,
  InMemoryAuthDirectory,
  InMemoryLoginThrottle,
  InMemorySessions,
  PlainTextPasswordHasher,
  SequentialTokens,
} from "../src/testing/in-memory.js";
import { jst, OPERATOR, ORG_A, setup } from "./support.js";

/*
 * Codex のレビュー（PR #137）で指摘された 3 件の再現テスト。修正されるまで RED。
 */

describe("Codex P1: 「不在」と「拒否」が同時に送られ、「不在」が先に保存されても、拒否は抑止になる", () => {
  it("挿入の競合で負けた「拒否」も、抑止として追加される", async () => {
    const { deps, callCommand } = setup();
    const created = await new CreateCallUseCase(deps).execute(callCommand());
    if (!created.ok) throw new Error("setup");
    const callId = created.value.call.id;
    const record = new RecordOutcomeUseCase(deps);
    // 先に「不在」が保存された
    const first = await record.execute({
      organizationId: ORG_A,
      actorId: OPERATOR,
      callId,
      outcome: "不在",
    });
    expect(first.ok).toBe(true);
    // 「拒否」の要求は、その保存の前に「まだ結果がない」と読んでいた（同時に送られた）
    const realGet = deps.outcomes.get.bind(deps.outcomes);
    let stale = true;
    deps.outcomes.get = async (org, id) => {
      if (stale) {
        stale = false;
        return undefined;
      }
      return realGet(org, id);
    };
    const r = await record.execute({
      organizationId: ORG_A,
      actorId: OPERATOR,
      callId,
      outcome: "拒否",
    });
    expect(r).toMatchObject({ ok: true, value: { suppressed: true } });
    const contact = deps.contacts.rows.get("c-1");
    if (!contact) throw new Error("setup");
    expect(await deps.suppression.canContact(ORG_A, contact.phone)).toBe(false);
  });
});

describe("Codex P2: 予約を戻したら、その予約がかけたロックも外す", () => {
  const USER = "user-1" as UserId;
  const ORG = "org-1" as OrganizationId;
  function loginSetup() {
    const clock = new FixedClock(jst("2026-10-05T10:00:00"));
    const directory = new InMemoryAuthDirectory();
    directory.add({
      userId: USER,
      email: "operator@example.test",
      displayName: "佐藤",
      passwordHash: "plain:correct horse battery staple",
      memberships: [{ organizationId: ORG, role: "OPERATOR" }],
    });
    const deps = {
      clock,
      directory,
      sessions: new InMemorySessions(directory),
      passwords: new PlainTextPasswordHasher(),
      tokens: new SequentialTokens(),
      audit: new InMemoryAuditLog(),
      throttle: new InMemoryLoginThrottle(),
    };
    return deps;
  }

  it("同じ IP からの 50 回目の試行が正しいパスワードなら、その IP はロックされない", async () => {
    const deps = loginSetup();
    const ip = "203.0.113.7";
    for (let i = 0; i < 49; i += 1) {
      await new LoginUseCase(deps).execute({
        email: `user${i}@example.test`,
        password: "wrong",
        clientIp: ip,
      });
    }
    const ok = await new LoginUseCase(deps).execute({
      email: "operator@example.test",
      password: "correct horse battery staple",
      clientIp: ip,
    });
    expect(ok.ok).toBe(true);
    // 失敗は 49 回なので、同じ IP の次の試行はロックされない
    const next = await new LoginUseCase(deps).execute({
      email: "someone@example.test",
      password: "wrong",
      clientIp: ip,
    });
    expect(next).toMatchObject({ ok: false, error: { code: "INVALID_CREDENTIALS" } });
  });

  it("IP がロック中のとき、照合しなかったアドレス側の予約を戻すと、アドレスのロックも残らない", async () => {
    const deps = loginSetup();
    const lockedIp = "203.0.113.9";
    for (let i = 0; i < 50; i += 1) {
      await new LoginUseCase(deps).execute({
        email: `user${i}@example.test`,
        password: "wrong",
        clientIp: lockedIp,
      });
    }
    // 本人のアドレスで 4 回失敗（別の IP から）
    for (let i = 0; i < 4; i += 1) {
      await new LoginUseCase(deps).execute({
        email: "operator@example.test",
        password: "wrong",
        clientIp: "198.51.100.1",
      });
    }
    // ロック中の IP から 5 回目：照合されない（IP のロック）。アドレス側は数えない
    const blocked = await new LoginUseCase(deps).execute({
      email: "operator@example.test",
      password: "correct horse battery staple",
      clientIp: lockedIp,
    });
    expect(blocked).toMatchObject({ ok: false, error: { code: "LOGIN_LOCKED" } });
    // 別の IP からなら、本人は正しいパスワードでログインできる
    const ok = await new LoginUseCase(deps).execute({
      email: "operator@example.test",
      password: "correct horse battery staple",
      clientIp: "198.51.100.1",
    });
    expect(ok.ok).toBe(true);
  });
});
