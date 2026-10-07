import {
  err,
  type NextAction,
  OUTCOME_PRESETS,
  type OutcomeCode,
  ok,
  outcomeFromLabel,
  planOutcome,
  type Result,
} from "@tac/domain";
import type { AppError, Deps } from "./deps.js";
import {
  type CallRecord,
  DuplicateOutcomeError,
  type FollowUpRecord,
  type OrganizationId,
  type OutcomeRecord,
  type UserId,
} from "./ports.js";

export interface RecordOutcomeCommand {
  readonly organizationId: OrganizationId;
  readonly actorId: UserId;
  readonly callId: string;
  /** 表示名（成約・検討…）または分類コード（WON・INTERESTED…） */
  readonly outcome: string;
  readonly callbackAt?: Date;
  readonly appointmentAt?: Date;
}

export interface RecordOutcomeResult {
  readonly outcome: OutcomeRecord;
  readonly followUp: FollowUpRecord | undefined;
  readonly suppressed: boolean;
  readonly nextAction: NextAction | undefined;
  readonly replayed: boolean;
}

function resolveCode(input: string): OutcomeCode | undefined {
  return outcomeFromLabel(input) ?? OUTCOME_PRESETS.find((p) => p.code === input.trim())?.code;
}

/**
 * 通話結果を記録し、抑止・フォローアップを作る。
 * 結果の保存・抑止・フォローアップは1つのトランザクションで行い、片方だけが残る状態を作らない。
 */
export class RecordOutcomeUseCase {
  constructor(private readonly deps: Deps) {}

  async execute(cmd: RecordOutcomeCommand): Promise<Result<RecordOutcomeResult, AppError>> {
    const { deps } = this;
    const code = resolveCode(cmd.outcome);
    if (!code) return err({ code: "INVALID_OUTCOME" });

    const call = await deps.calls.get(cmd.organizationId, cmd.callId);
    if (!call) return err({ code: "CALL_NOT_FOUND" });

    const existing = await deps.outcomes.get(cmd.organizationId, call.id);
    if (existing) {
      // 先に別の結果（不在など）を記録していても、顧客の拒否は必ず抑止として残す（IQA-01）。
      // 抑止を登録する経路がここしかないため、409 で断ると拒否した相手へ翌日また発信してしまう。
      if (existing.code !== code && suppressionOf(code) !== "NONE") {
        return this.escalateToSuppression(cmd, call, existing, code);
      }
      return replayOutcome(existing, code);
    }

    const [campaign, contact, attempt] = await Promise.all([
      deps.campaigns.get(cmd.organizationId, call.campaignId),
      deps.contacts.get(cmd.organizationId, call.contactId),
      deps.calls.countForContact(cmd.organizationId, call.contactId),
    ]);
    if (!campaign) return err({ code: "CAMPAIGN_NOT_FOUND" });

    const now = deps.clock.now();
    const planned = planOutcome(code, {
      now,
      window: {
        ...campaign.callingWindow,
        timeZone: contact?.timeZone ?? campaign.callingWindow.timeZone,
      },
      attempt,
      maxAttempts: campaign.maxAttempts,
      ...(cmd.callbackAt ? { callbackAt: cmd.callbackAt } : {}),
      ...(cmd.appointmentAt ? { appointmentAt: cmd.appointmentAt } : {}),
    });
    if (!planned.ok) return err({ code: planned.error });
    const plan = planned.value;

    const outcome: OutcomeRecord = {
      callId: call.id,
      organizationId: cmd.organizationId,
      code,
      recordedBy: cmd.actorId,
      recordedAt: now,
    };
    const followUp: FollowUpRecord | undefined = plan.followUp && {
      id: deps.ids.next(),
      organizationId: cmd.organizationId,
      contactId: call.contactId,
      kind: plan.followUp.kind,
      dueAt: plan.followUp.dueAt,
      status: "OPEN",
      sourceCallId: call.id,
    };
    const audit = (action: string, resource: string, after: unknown) =>
      deps.audit.append({
        organizationId: cmd.organizationId,
        actorId: cmd.actorId,
        action,
        resource,
        at: now,
        after,
      });
    const publish = (type: string, payload: Record<string, unknown>) =>
      deps.events.publish({
        type,
        version: 1,
        organizationId: cmd.organizationId,
        occurredAt: now,
        payload,
      });

    try {
      await deps.uow.run(async () => {
        await deps.outcomes.insert(outcome);
        await audit("outcome.recorded", `call:${call.id}`, { code });
        if (plan.suppress !== "NONE") {
          await deps.suppression.add({
            organizationId: cmd.organizationId,
            phone: call.to,
            reason: code,
            source: "outcome",
            actorId: cmd.actorId,
          });
          // 抑止した相手の予定はすべて取り消す（キューから外す）
          if (plan.suppress === "CONTACT") {
            await deps.followUps.cancelOpenForContact(cmd.organizationId, call.contactId);
          }
          await audit("suppression.added", `contact:${call.contactId}`, {
            scope: plan.suppress,
            reason: code,
          });
        }
        if (followUp) await deps.followUps.insert(followUp);
      });
    } catch (e) {
      // 同じ結果が同時に送られた（Webhook の再送・二重クリック）。先に保存された方を返す（QA-NX-04）
      if (e instanceof DuplicateOutcomeError) {
        const winner = await deps.outcomes.get(cmd.organizationId, call.id);
        if (winner) return replayOutcome(winner, code);
      }
      throw e;
    }

    await publish("OutcomeRecorded", { callId: call.id, code });
    if (plan.suppress !== "NONE") {
      await publish("SuppressionAdded", { contactId: call.contactId, scope: plan.suppress });
    }
    if (followUp)
      await publish("FollowUpCreated", { followUpId: followUp.id, kind: followUp.kind });

    return ok({
      outcome,
      followUp,
      suppressed: plan.suppress !== "NONE",
      nextAction: plan.nextAction,
      replayed: false,
    });
  }

  /**
   * 記録済みの結果はそのまま残し、抑止だけを追加する（結果の訂正としての「拒否」「番号違い」）。
   * 抑止の登録は冪等なので、同時に送られても二重にはならない。
   */
  private async escalateToSuppression(
    cmd: RecordOutcomeCommand,
    call: { id: string; contactId: string; to: CallRecord["to"] },
    existing: OutcomeRecord,
    code: OutcomeCode,
  ): Promise<Result<RecordOutcomeResult, AppError>> {
    const { deps } = this;
    const scope = suppressionOf(code);
    const now = deps.clock.now();
    await deps.uow.run(async () => {
      await deps.suppression.add({
        organizationId: cmd.organizationId,
        phone: call.to,
        reason: code,
        source: "outcome",
        actorId: cmd.actorId,
      });
      if (scope === "CONTACT") {
        await deps.followUps.cancelOpenForContact(cmd.organizationId, call.contactId);
      }
      await deps.audit.append({
        organizationId: cmd.organizationId,
        actorId: cmd.actorId,
        action: "suppression.added",
        resource: `contact:${call.contactId}`,
        at: now,
        after: { scope, reason: code, correctionOf: existing.code, callId: call.id },
      });
    });
    await deps.events.publish({
      type: "SuppressionAdded",
      version: 1,
      organizationId: cmd.organizationId,
      occurredAt: now,
      payload: { contactId: call.contactId, scope },
    });
    return ok({
      outcome: existing,
      followUp: undefined,
      suppressed: true,
      nextAction: "DO_NOT_CONTACT",
      replayed: false,
    });
  }
}

const suppressionOf = (code: OutcomeCode) =>
  OUTCOME_PRESETS.find((p) => p.code === code)?.requiresSuppression ?? "NONE";

/** 既に記録済みの結果。同じ内容なら再送として前回の結果を返し、違う内容なら拒否する。 */
function replayOutcome(
  existing: OutcomeRecord,
  code: OutcomeCode,
): Result<RecordOutcomeResult, AppError> {
  if (existing.code !== code) return err({ code: "OUTCOME_ALREADY_RECORDED" });
  return ok({
    outcome: existing,
    followUp: undefined,
    suppressed: OUTCOME_PRESETS.some((p) => p.code === code && p.requiresSuppression !== "NONE"),
    nextAction: undefined,
    replayed: true,
  });
}
