import { describe, expect, it } from "vitest";
import { CreateCallUseCase } from "../src/create-call.js";
import type { CreateProviderCallRequest, ProviderCall } from "../src/ports.js";
import { ApplyProviderEventUseCase } from "../src/provider-events.js";
import { jst, OPERATOR, ORG_A, setup } from "./support.js";

/*
 * 独立 QA（IQA, 2026-10-06）の再現テスト（アプリ層・インメモリ）。修正されるまで RED。
 */

describe("IQA-03: プロバイダが受け付けた後に応答が失われた（タイムアウト以外の例外）と、FAILED 扱いになり二重発信できる", () => {
  it("発信が実際に行われたか分からない失敗では通話を確定させず、同じ相手への 2 件目の外部発信を生まない", async () => {
    const { deps, callCommand } = setup();
    const camp = deps.campaigns.rows.get("camp-1");
    if (!camp) throw new Error("setup");
    deps.campaigns.rows.set("camp-1", { ...camp, perNumberDailyLimit: 3 });
    // プロバイダは発信を受け付けた（回線は鳴っている）が、応答の受信中に接続が切れた
    const placed: CreateProviderCallRequest[] = [];
    let first = true;
    deps.telephony.createCall = async (req: CreateProviderCallRequest): Promise<ProviderCall> => {
      placed.push(req);
      if (first) {
        first = false;
        throw Object.assign(new Error("socket hang up"), { code: "ECONNRESET" });
      }
      return { provider: "recording", providerCallId: `PC-${placed.length}`, status: "DIALING" };
    };
    const uc = new CreateCallUseCase(deps);
    const r1 = await uc.execute(callCommand({ idempotencyKey: "k-1" }));
    expect(r1.ok).toBe(false);
    // 担当者が「失敗」を見て、もう一度ボタンを押す（画面は新しい冪等キーを作る）
    const r2 = await uc.execute(callCommand({ idempotencyKey: "k-2" }));
    expect(r2).toMatchObject({ ok: false });
    expect(placed).toHaveLength(1);

    // 1 件目の回線の Webhook（鳴っている）が後から届いたら、記録に反映される（捨てられない）
    const [call1] = [...deps.calls.rows.values()];
    const applied = await new ApplyProviderEventUseCase(deps).execute({
      provider: "recording",
      eventId: "evt-1",
      providerCallId: "PC-1",
      callId: call1?.id,
      status: "RINGING",
      occurredAt: deps.clock.now(),
      payload: {},
    });
    expect(applied.kind).toBe("APPLIED");
  });
});

describe("IQA-04: 直前の再確認の後に、監査・イベント配信の待ちが残っている（判定と発信の間の窓）", () => {
  it("CallRequested の配信中に DNC が登録されたら、外部発信しない", async () => {
    const { deps, callCommand } = setup();
    const publish = deps.events.publish.bind(deps.events);
    deps.events.publish = async (event) => {
      await publish(event);
      if (event.type === "CallRequested") {
        // 別の担当者が同じ番号の「拒否」を記録した（RecordOutcome は組織ロックを取らない）
        const contact = deps.contacts.rows.get("c-1");
        if (!contact) throw new Error("setup");
        await deps.suppression.add({
          organizationId: ORG_A,
          phone: contact.phone,
          reason: "DO_NOT_CALL",
          source: "outcome",
          actorId: OPERATOR,
        });
      }
    };
    const r = await new CreateCallUseCase(deps).execute(callCommand());
    expect(deps.telephony.requests).toHaveLength(0);
    expect(r.ok).toBe(false);
  });

  it("監査ログの書き込み中に全発信停止が入ったら、外部発信しない", async () => {
    const { deps, callCommand } = setup();
    const append = deps.audit.append.bind(deps.audit);
    deps.audit.append = async (entry) => {
      await append(entry);
      if (entry.action === "call.requested") deps.safety.stopAllOutbound();
    };
    await new CreateCallUseCase(deps).execute(callCommand());
    expect(deps.telephony.requests).toHaveLength(0);
  });
});

describe("IQA-04b: 直前の再確認は抑止と全発信停止だけで、キャンペーン・組織の一時停止を見ない", () => {
  it("判定の後・発信の前にキャンペーンを一時停止したら、外部発信しない", async () => {
    const { deps, callCommand } = setup();
    const publish = deps.events.publish.bind(deps.events);
    deps.events.publish = async (event) => {
      await publish(event);
      if (event.type === "CallRequested") {
        const camp = deps.campaigns.rows.get("camp-1");
        if (!camp) throw new Error("setup");
        deps.campaigns.rows.set("camp-1", { ...camp, paused: true });
      }
    };
    await new CreateCallUseCase(deps).execute(callCommand());
    expect(deps.telephony.requests).toHaveLength(0);
  });
});

describe("IQA-08: プロバイダのタイムアウト後、Webhook が来ないと通話が REQUESTED のまま残り、同時通話数の枠を永久に占有する", () => {
  it("タイムアウトから 1 時間たっても確定しない通話のせいで、別の相手に発信できなくなることはない", async () => {
    const { deps, clock, callCommand } = setup();
    const org = deps.organizations.rows.get(ORG_A);
    if (!org) throw new Error("setup");
    deps.organizations.rows.set(ORG_A, { ...org, maxConcurrentCalls: 1 });
    deps.telephony.mode = "timeout";
    const r1 = await new CreateCallUseCase(deps).execute(callCommand({ idempotencyKey: "k-1" }));
    expect(r1).toMatchObject({ ok: false, error: { code: "PROVIDER_TIMEOUT" } });
    // プロバイダには届いていなかった（Webhook は永遠に来ない）。照合（reconcile）の仕組みもない
    deps.telephony.mode = "ok";
    clock.set(jst("2026-10-05T11:00:00"));
    const r2 = await new CreateCallUseCase(deps).execute(
      callCommand({ contactId: "c-2", idempotencyKey: "k-2" }),
    );
    expect(r2.ok).toBe(true);
  });
});

describe("IQA-11: 通話を特定できなかった Webhook（UNKNOWN_CALL）を処理済みにしてしまい、再送されても二度と反映されない", () => {
  it("プロバイダの ID が記録される前に届いた（通話 ID なしの）イベントは、記録の後の再送で反映される", async () => {
    const { deps, callCommand } = setup();
    deps.telephony.mode = "timeout"; // 応答が届かず、providerCallId はまだ記録されていない
    await new CreateCallUseCase(deps).execute(callCommand());
    const [call1] = [...deps.calls.rows.values()];
    if (!call1) throw new Error("setup");
    const uc = new ApplyProviderEventUseCase(deps);
    const event = {
      provider: "recording",
      eventId: "evt-early-1",
      providerCallId: "PC-LATE",
      callId: undefined, // 実プロバイダは発信時のメタデータを返さないことがある
      status: "RINGING" as const,
      occurredAt: deps.clock.now(),
      payload: {},
    };
    expect((await uc.execute(event)).kind).toBe("UNKNOWN_CALL");
    // 後から（照合・遅れて届いた応答で）プロバイダの ID が記録された
    await deps.calls.attachProvider(ORG_A, call1.id, "recording", "PC-LATE");
    // プロバイダは 2xx 以外・または一定時間内の再送をする想定。同じイベントの再送は反映されるべき
    expect((await uc.execute(event)).kind).toBe("APPLIED");
  });
});
