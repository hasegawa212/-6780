import { describe, expect, it } from "vitest";
import { CreateCallUseCase } from "../src/create-call.js";
import type { OrganizationId } from "../src/ports.js";
import {
  NOT_PLACED_AFTER_MS,
  RECONCILE_MIN_AGE_MS,
  ReconcileUncertainCallsUseCase,
} from "../src/reconcile-calls.js";
import { InMemoryUncertainCallFinder } from "../src/testing/in-memory.js";
import { ORG_A, ORG_B, setup } from "./support.js";

/*
 * 確定しない発信の照合（ADR-0016、IQA-08 の根本対策）。
 * 発信の応答が届かず REQUESTED のまま・プロバイダの ID も無い通話を、プロバイダの通話一覧と突き合わせる。
 * - 一覧に 1 件だけ見つかった → その ID を付けて状態を進める（Webhook と同じ CAS）
 * - 十分な時間が経っても見つからない → 発信されなかったとして FAILED（番号のふさがりを解く）
 * - 判断できない（複数ある・一覧が引けない・照合できないプロバイダ）→ 何も変えない（fail closed）
 */

const MIN = 60_000;

/** 応答が届かなかった発信を 1 件作る（REQUESTED のまま） */
async function uncertainCall(contactId = "c-1", org: OrganizationId = ORG_A) {
  const ctx = setup();
  ctx.deps.telephony.mode = "timeout";
  const created = ctx.clock.now();
  const r = await new CreateCallUseCase(ctx.deps).execute(
    ctx.callCommand({
      contactId,
      organizationId: org,
      campaignId: org === ORG_A ? "camp-1" : "camp-b",
    }),
  );
  expect(r.ok).toBe(false);
  const [row] = [...ctx.deps.calls.rows.values()];
  if (!row) throw new Error("setup: no call");
  expect(row.status).toBe("REQUESTED");
  const finder = new InMemoryUncertainCallFinder(ctx.deps.calls);
  const uc = new ReconcileUncertainCallsUseCase(ctx.deps, finder);
  const at = (ms: number) => ctx.clock.set(new Date(created.getTime() + ms));
  const call = async () => ctx.deps.calls.get(row.organizationId, row.id);
  return { ...ctx, row, created, uc, at, call };
}

describe("ReconcileUncertainCallsUseCase", () => {
  it("一覧に 1 件だけ見つかれば、その ID を付けて状態を進める", async () => {
    const s = await uncertainCall();
    s.deps.telephony.listed = [
      {
        providerCallId: "CA-found",
        status: "RINGING",
        createdAt: new Date(s.created.getTime() + 1000),
      },
    ];
    s.at(5 * MIN);
    const r = await s.uc.execute();
    expect(r.results).toEqual([{ callId: s.row.id, kind: "MATCHED" }]);
    expect(await s.call()).toMatchObject({
      status: "RINGING",
      provider: "recording",
      providerCallId: "CA-found",
    });
    // 問い合わせは「この発信元からこの相手へ、作成時刻の少し前以降」
    expect(s.deps.telephony.queries[0]).toMatchObject({ to: s.row.to, from: s.row.from });
    expect(s.deps.telephony.queries[0]?.createdAfter.getTime()).toBeLessThan(s.created.getTime());
    expect(s.deps.audit.entries.map((e) => e.action)).toContain("call.reconciled");
    expect(s.deps.events.types()).toContain("CallStatusChanged");
  });

  it("見つかった通話の状態が分からなければ DIALING として扱う（推測で先に進めない）", async () => {
    const s = await uncertainCall();
    s.deps.telephony.listed = [
      {
        providerCallId: "CA-x",
        status: undefined,
        createdAt: new Date(s.created.getTime() + 1000),
      },
    ];
    s.at(5 * MIN);
    await s.uc.execute();
    expect(await s.call()).toMatchObject({ status: "DIALING", providerCallId: "CA-x" });
  });

  it("まだ時間が経っていない通話は照合しない（Webhook が遅れて届くのを待つ）", async () => {
    const s = await uncertainCall();
    s.at(RECONCILE_MIN_AGE_MS - 1000);
    expect((await s.uc.execute()).results).toEqual([]);
    expect(s.deps.telephony.queries).toHaveLength(0);
  });

  it("見つからなくても、時間が浅いうちは何も変えない（番号はふさがったまま）", async () => {
    const s = await uncertainCall();
    s.at(5 * MIN);
    expect((await s.uc.execute()).results).toEqual([{ callId: s.row.id, kind: "PENDING" }]);
    expect((await s.call())?.status).toBe("REQUESTED");
  });

  it("十分な時間が経っても一覧に無ければ、発信されなかったとして FAILED にし、同じ相手に発信できるようになる", async () => {
    const s = await uncertainCall();
    // 1 日に同じ番号へ 2 回まで（「回線上 1 件」の制約だけを確かめるため）
    const camp = s.deps.campaigns.rows.get("camp-1");
    if (camp) s.deps.campaigns.rows.set("camp-1", { ...camp, perNumberDailyLimit: 2 });
    s.deps.telephony.mode = "ok";
    s.at(NOT_PLACED_AFTER_MS + MIN);
    // 照合の前は、確定しない通話が番号をふさいでいるので掛け直せない
    const blocked = await new CreateCallUseCase(s.deps).execute(
      s.callCommand({ idempotencyKey: "key-blocked" }),
    );
    expect(blocked.ok).toBe(false);
    if (!blocked.ok) expect(blocked.error.reasons).toContain("CONTACT_ALREADY_IN_CALL");
    expect(s.deps.telephony.requests).toHaveLength(1);
    expect((await s.uc.execute()).results).toEqual([{ callId: s.row.id, kind: "NOT_PLACED" }]);
    expect((await s.call())?.status).toBe("FAILED");
    expect(s.deps.audit.entries.find((e) => e.action === "call.reconciled")?.after).toMatchObject({
      result: "NOT_PLACED",
    });
    // 番号のふさがりが解けた（別のキーで発信できる）
    const again = await new CreateCallUseCase(s.deps).execute(
      s.callCommand({ idempotencyKey: "key-2" }),
    );
    expect(again.ok).toBe(true);
  });

  it("候補が 2 件以上なら、どれか分からないので何も変えない", async () => {
    const s = await uncertainCall();
    const t = new Date(s.created.getTime() + 1000);
    s.deps.telephony.listed = [
      { providerCallId: "CA-1", status: "RINGING", createdAt: t },
      { providerCallId: "CA-2", status: "RINGING", createdAt: t },
    ];
    s.at(NOT_PLACED_AFTER_MS + MIN);
    expect((await s.uc.execute()).results).toEqual([{ callId: s.row.id, kind: "AMBIGUOUS" }]);
    expect(await s.call()).toMatchObject({ status: "REQUESTED", providerCallId: undefined });
  });

  it("別の通話に付いている ID（他の組織が同じ番号に掛けた等）は候補にしない", async () => {
    const s = await uncertainCall();
    // ORG_B の通話がすでに CA-other を持っている
    s.deps.calls.rows.set("other", {
      ...s.row,
      id: "other",
      organizationId: ORG_B,
      status: "IN_PROGRESS",
      provider: "recording",
      providerCallId: "CA-other",
      idempotencyKey: "other",
    });
    s.deps.telephony.listed = [
      {
        providerCallId: "CA-other",
        status: "IN_PROGRESS",
        createdAt: new Date(s.created.getTime() + 1000),
      },
    ];
    s.at(5 * MIN);
    expect((await s.uc.execute()).results).toEqual([{ callId: s.row.id, kind: "PENDING" }]);
    expect((await s.call())?.providerCallId).toBeUndefined();
  });

  it("作成からかけ離れた時刻の通話は候補にしない", async () => {
    const s = await uncertainCall();
    s.deps.telephony.listed = [
      {
        providerCallId: "CA-late",
        status: "RINGING",
        createdAt: new Date(s.created.getTime() + 30 * MIN),
      },
    ];
    s.at(NOT_PLACED_AFTER_MS + 20 * MIN);
    expect((await s.uc.execute()).results).toEqual([{ callId: s.row.id, kind: "NOT_PLACED" }]);
  });

  it("一覧を引けなければ、時間が経っていても何も変えない（fail closed）", async () => {
    const s = await uncertainCall();
    s.deps.telephony.findMode = "error";
    s.at(NOT_PLACED_AFTER_MS + MIN);
    expect((await s.uc.execute()).results).toEqual([{ callId: s.row.id, kind: "ERROR" }]);
    expect((await s.call())?.status).toBe("REQUESTED");
  });

  it("一覧を引けないプロバイダでは照合しない（FAILED にしない）", async () => {
    const s = await uncertainCall();
    const noList = {
      name: "nolist",
      createCall: s.deps.telephony.createCall.bind(s.deps.telephony),
      endCall: async () => {},
      transferCall: async () => {},
      getCall: s.deps.telephony.getCall.bind(s.deps.telephony),
    };
    const uc = new ReconcileUncertainCallsUseCase(
      { ...s.deps, telephony: noList },
      new InMemoryUncertainCallFinder(s.deps.calls),
    );
    s.at(NOT_PLACED_AFTER_MS + MIN);
    expect((await uc.execute()).results).toEqual([]);
    expect((await s.call())?.status).toBe("REQUESTED");
  });

  it("照合の間に Webhook が先に ID を付けたら、それを上書きしない", async () => {
    const s = await uncertainCall();
    s.deps.telephony.listed = [
      {
        providerCallId: "CA-list",
        status: "RINGING",
        createdAt: new Date(s.created.getTime() + 1000),
      },
    ];
    // 一覧を引いている最中に、Webhook が別の ID で通話を進めた
    const original = s.deps.telephony.findCalls.bind(s.deps.telephony);
    s.deps.telephony.findCalls = async (q) => {
      await s.deps.calls.attachProvider(s.row.organizationId, s.row.id, "recording", "CA-hook");
      await s.deps.calls.transitionStatus(s.row.organizationId, s.row.id, "REQUESTED", "DIALING");
      return original(q);
    };
    s.at(5 * MIN);
    await s.uc.execute();
    expect((await s.call())?.providerCallId).toBe("CA-hook");
  });
});
