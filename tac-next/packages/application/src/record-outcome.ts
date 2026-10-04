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
import type { FollowUpRecord, OrganizationId, OutcomeRecord, UserId } from "./ports.js";

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
      if (existing.code !== code) return err({ code: "OUTCOME_ALREADY_RECORDED" });
      return ok({
        outcome: existing,
        followUp: undefined,
        suppressed: false,
        nextAction: undefined,
        replayed: true,
      });
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
}
