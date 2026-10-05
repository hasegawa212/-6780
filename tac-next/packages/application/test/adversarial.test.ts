import { describe, expect, it } from "vitest";
import { CreateCallUseCase } from "../src/create-call.js";
import { RecordOutcomeUseCase } from "../src/record-outcome.js";
import { OPERATOR, ORG_A, phone, setup } from "./support.js";

/*
 * QA（2026-10-05）: 不変条件「抑止中の相手・止めている間は、新しい外部発信を絶対に生まない」
 * 「同じ相手へ同時に二重発信しない」を、競合・割り込みで破れるかを試す。
 */
describe("QA: 同じ相手への同時発信", () => {
  it("Operator A・Operator B・Worker が別々の冪等キーで同時に掛けても、外部発信は 1 件だけ", async () => {
    const { deps, callCommand } = setup();
    // 1日あたりの上限で止まる経路を外し、純粋に「同時発信」を試す
    const camp = deps.campaigns.rows.get("camp-1");
    if (!camp) throw new Error("setup");
    deps.campaigns.rows.set("camp-1", { ...camp, perNumberDailyLimit: 10 });
    const uc = new CreateCallUseCase(deps);
    const results = await Promise.all(
      ["op-a", "op-b", "worker"].map((k) => uc.execute(callCommand({ idempotencyKey: k }))),
    );
    expect(deps.telephony.requests).toHaveLength(1);
    expect(results.filter((r) => r.ok)).toHaveLength(1);
    for (const r of results.filter((x) => !x.ok)) {
      expect(r).toMatchObject({ ok: false, error: { code: "CONTACT_ALREADY_IN_CALL" } });
    }
  });

  it.each([2, 10, 100])("同じ冪等キーを %i 並列で送っても外部発信は 1 件", async (n) => {
    const { deps, callCommand } = setup();
    const uc = new CreateCallUseCase(deps);
    const results = await Promise.all(Array.from({ length: n }, () => uc.execute(callCommand())));
    expect(deps.telephony.requests).toHaveLength(1);
    const ids = new Set(results.filter((r) => r.ok).map((r) => (r.ok ? r.value.call.id : "")));
    expect(ids.size).toBe(1);
  });
});

describe("QA: 発信手続きの途中での割り込み（TOCTOU）", () => {
  it("判定の後・発信の前に DNC 登録が入ったら、外部発信しない", async () => {
    const { deps, callCommand } = setup();
    const insert = deps.calls.insert.bind(deps.calls);
    deps.calls.insert = async (call) => {
      await insert(call);
      // 別の担当者が今まさに「拒否」を記録した
      await deps.suppression.add({
        organizationId: ORG_A,
        phone: phone("090-0000-0001"),
        reason: "拒否",
        source: "race",
        actorId: OPERATOR,
      });
    };
    const r = await new CreateCallUseCase(deps).execute(callCommand());
    expect(r).toMatchObject({ ok: false, error: { code: "CONTACT_SUPPRESSED" } });
    expect(deps.telephony.requests).toHaveLength(0);
    expect([...deps.calls.rows.values()].map((c) => c.status)).toEqual(["CANCELED"]);
  });

  it("判定の後・発信の前に全発信停止が入ったら、外部発信しない", async () => {
    const { deps, callCommand } = setup();
    const insert = deps.calls.insert.bind(deps.calls);
    deps.calls.insert = async (call) => {
      await insert(call);
      deps.safety.stopAllOutbound();
    };
    const r = await new CreateCallUseCase(deps).execute(callCommand());
    expect(r).toMatchObject({ ok: false, error: { code: "OUTBOUND_STOPPED" } });
    expect(deps.telephony.requests).toHaveLength(0);
  });
});

describe("QA: 結果記録の再送・同時送信", () => {
  it("同じ通話の結果を同時に 2 回送っても、例外にならずフォローアップは 1 件", async () => {
    const { deps, callCommand } = setup();
    const placed = await new CreateCallUseCase(deps).execute(callCommand());
    if (!placed.ok) throw new Error("setup");
    const uc = new RecordOutcomeUseCase(deps);
    const cmd = {
      organizationId: ORG_A,
      actorId: OPERATOR,
      callId: placed.value.call.id,
      outcome: "検討",
    };
    const results = await Promise.all([uc.execute(cmd), uc.execute(cmd)]);
    expect(results.every((r) => r.ok)).toBe(true);
    expect(deps.followUps.rows.size).toBe(1);
  });
});

describe("QA: 不正な設定値", () => {
  it("相手のタイムゾーンが不正なら、例外で落ちずに発信を止める（fail closed）", async () => {
    const { deps, callCommand } = setup();
    const contact = deps.contacts.rows.get("c-1");
    if (!contact) throw new Error("setup");
    deps.contacts.rows.set("c-1", { ...contact, timeZone: "Mars/Olympus_Mons" });
    const r = await new CreateCallUseCase(deps).execute(callCommand());
    expect(r).toMatchObject({ ok: false, error: { code: "OUTSIDE_CALLING_WINDOW" } });
    expect(deps.telephony.requests).toHaveLength(0);
  });
});
