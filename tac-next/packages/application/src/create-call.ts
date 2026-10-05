import {
  type CallMode,
  err,
  evaluateCallPolicy,
  isAllowedCountry,
  isWithinCallingWindow,
  ok,
  type Result,
  reconcileProviderStatus,
} from "@tac/domain";
import type { AppError, Deps } from "./deps.js";
import {
  ActiveCallExistsError,
  type CallRecord,
  DuplicateIdempotencyKeyError,
  type OrganizationId,
  ProviderTimeoutError,
  type UserId,
} from "./ports.js";
import { isContactable } from "./suppression-check.js";

export interface CreateCallCommand {
  readonly organizationId: OrganizationId;
  readonly actorId: UserId;
  readonly contactId: string;
  readonly campaignId: string;
  readonly idempotencyKey: string;
  readonly mode: CallMode;
  /** 名乗りで告げる担当者名 */
  readonly agentName: string;
}

export interface CreateCallResult {
  readonly call: CallRecord;
  /** 同じ冪等キーの再送で、前回の結果を返しただけなら true（発信はしていない） */
  readonly replayed: boolean;
}

const DAY_MS = 24 * 60 * 60 * 1000;
const MAX_IDEMPOTENCY_KEY_LENGTH = 255;

const fingerprint = (c: CreateCallCommand) => `${c.contactId}|${c.campaignId}|${c.mode}`;

/**
 * 発信のユースケース。順番に意味がある：
 * 冪等キーの確認 → 相手とキャンペーンの取得（テナントで絞る）→ 発信可否ポリシー（抑止を最優先）
 * → 通話を REQUESTED で保存（冪等キーを確保）→ プロバイダへ発信 → 状態を反映。
 * プロバイダを呼ぶのは、冪等キーの確保に成功した1リクエストだけ。
 */
export class CreateCallUseCase {
  constructor(private readonly deps: Deps) {}

  async execute(cmd: CreateCallCommand): Promise<Result<CreateCallResult, AppError>> {
    const key = cmd.idempotencyKey.trim();
    if (key === "" || key.length > MAX_IDEMPOTENCY_KEY_LENGTH) {
      return err({ code: "INVALID_IDEMPOTENCY_KEY" });
    }
    const replay = await this.replay(cmd, key);
    if (replay) return replay;

    const { deps } = this;
    const [org, contact, campaign] = await Promise.all([
      deps.organizations.get(cmd.organizationId),
      deps.contacts.get(cmd.organizationId, cmd.contactId),
      deps.campaigns.get(cmd.organizationId, cmd.campaignId),
    ]);
    if (!org) return err({ code: "ORGANIZATION_NOT_FOUND" });
    if (!contact) return err({ code: "CONTACT_NOT_FOUND" });
    if (!campaign) return err({ code: "CAMPAIGN_NOT_FOUND" });

    const now = deps.clock.now();
    const since = new Date(now.getTime() - DAY_MS);
    const window = {
      ...campaign.callingWindow,
      timeZone: contact.timeZone ?? campaign.callingWindow.timeZone,
    };
    const [
      contactable,
      dialedToday,
      callsToNumberToday,
      hasValidConsent,
      outboundStopped,
      activeCalls,
      budgetRemaining,
    ] = await Promise.all([
      isContactable(deps.suppression, cmd.organizationId, contact.phone),
      deps.calls.countDialedSince(cmd.organizationId, since),
      deps.calls.countToNumberSince(cmd.organizationId, contact.phone, since),
      cmd.mode === "AI_VOICE"
        ? deps.consents.hasValidConsent(cmd.organizationId, contact.id, "AI_VOICE_OUTBOUND", now)
        : Promise.resolve(false),
      deps.safety.isOutboundStopped(),
      deps.calls.countActive(cmd.organizationId),
      deps.budget.remaining(cmd.organizationId, campaign.id),
    ]);
    const decision = evaluateCallPolicy({
      suppressed: !contactable.allowed,
      countryAllowed: isAllowedCountry(contact.phone, campaign.allowedCountryCodes),
      withinCallingWindow: isWithinCallingWindow(now, window),
      dialedToday,
      dailyCap: campaign.dailyCap,
      callsToNumberToday,
      perNumberDailyLimit: campaign.perNumberDailyLimit,
      disclosure: {
        companyName: org.companyName,
        agentName: cmd.agentName,
        product: campaign.product,
      },
      mode: cmd.mode,
      aiVoiceOutboundEnabled: org.aiVoiceOutboundEnabled,
      hasValidConsent,
      outboundStopped,
      organizationPaused: org.paused,
      campaignPaused: campaign.paused,
      activeCalls,
      maxConcurrentCalls: org.maxConcurrentCalls,
      budgetRemaining,
    });
    if (!decision.allowed) {
      // 同じキーの別リクエストが先に発信を済ませていた場合は、拒否ではなく前回の結果を返す
      const raced = await this.replay(cmd, key);
      if (raced) return raced;
      return this.blocked(cmd, contact.id, campaign.id, decision.reasons, {
        ...(contactable.unavailable ? { suppressionUnavailable: true } : {}),
      });
    }

    const call: CallRecord = {
      id: deps.ids.next(),
      organizationId: cmd.organizationId,
      contactId: contact.id,
      campaignId: campaign.id,
      to: contact.phone,
      from: campaign.callerId,
      mode: cmd.mode,
      status: "REQUESTED",
      idempotencyKey: key,
      requestFingerprint: fingerprint(cmd),
      requestedBy: cmd.actorId,
      agentName: cmd.agentName.trim(),
      provider: undefined,
      providerCallId: undefined,
      createdAt: now,
    };
    try {
      await deps.calls.insert(call);
    } catch (e) {
      if (e instanceof DuplicateIdempotencyKeyError) {
        const raced = await this.replay(cmd, key);
        if (raced) return raced;
      }
      // 別の担当者・ワーカーが同じ番号に今まさに掛けている（QA-NX-01）
      if (e instanceof ActiveCallExistsError) {
        return this.blocked(cmd, contact.id, campaign.id, ["CONTACT_ALREADY_IN_CALL"], {});
      }
      throw e;
    }

    // 判定から発信までの間に、抑止の登録・全発信停止が入っていないかを外部発信の直前に確かめ直す
    // （QA-NX-02 / 03: 判定時点では掛けてよくても、今はもう掛けてはいけないかもしれない）
    const [stillContactable, stoppedNow] = await Promise.all([
      isContactable(deps.suppression, cmd.organizationId, contact.phone),
      deps.safety.isOutboundStopped().catch(() => true),
    ]);
    const lateReasons = [
      ...(stoppedNow ? ["OUTBOUND_STOPPED"] : []),
      ...(stillContactable.allowed ? [] : ["CONTACT_SUPPRESSED"]),
    ];
    if (lateReasons.length > 0) {
      await deps.calls.update({ ...call, status: "CANCELED" });
      return this.blocked(cmd, contact.id, campaign.id, lateReasons, {
        callId: call.id,
        stage: "pre-dial",
        ...(stillContactable.unavailable ? { suppressionUnavailable: true } : {}),
      });
    }
    await deps.audit.append({
      organizationId: cmd.organizationId,
      actorId: cmd.actorId,
      action: "call.requested",
      resource: `call:${call.id}`,
      at: now,
      after: { contactId: contact.id, campaignId: campaign.id, mode: cmd.mode },
    });
    await this.publish(cmd.organizationId, "CallRequested", {
      callId: call.id,
      contactId: contact.id,
    });

    try {
      const placed = await deps.telephony.createCall({
        idempotencyKey: key,
        to: call.to,
        from: call.from,
        callId: call.id,
        disclosureText: `こちらは${org.companyName}の${call.agentName}です。${campaign.product}のご案内でお電話いたしました。`,
      });
      const updated: CallRecord = {
        ...call,
        provider: placed.provider,
        providerCallId: placed.providerCallId,
        status: reconcileProviderStatus(call.status, placed.status).status,
      };
      await deps.calls.update(updated);
      await this.publish(cmd.organizationId, "CallDialing", { callId: call.id });
      return ok({ call: updated, replayed: false });
    } catch (e) {
      if (e instanceof ProviderTimeoutError) {
        // 発信されたかどうか分からない。REQUESTED のまま残し、Webhook で確定させる。
        // 同じキーでの再送は replay になるので、ここから二重発信は起きない。
        return err({ code: "PROVIDER_TIMEOUT" });
      }
      await deps.calls.update({ ...call, status: "FAILED" });
      await this.publish(cmd.organizationId, "CallFailed", { callId: call.id, stage: "create" });
      return err({ code: "PROVIDER_ERROR" });
    }
  }

  private async blocked(
    cmd: CreateCallCommand,
    contactId: string,
    campaignId: string,
    reasons: readonly string[],
    extra: Record<string, unknown>,
  ): Promise<Result<CreateCallResult, AppError>> {
    await this.deps.audit.append({
      organizationId: cmd.organizationId,
      actorId: cmd.actorId,
      action: "call.blocked",
      resource: `contact:${contactId}`,
      at: this.deps.clock.now(),
      after: { reasons, campaignId, ...extra },
    });
    await this.publish(cmd.organizationId, "CallBlocked", { contactId, campaignId, reasons });
    const [code = "CALL_BLOCKED"] = reasons;
    return err({ code, reasons });
  }

  private async replay(
    cmd: CreateCallCommand,
    key: string,
  ): Promise<Result<CreateCallResult, AppError> | undefined> {
    const existing = await this.deps.calls.findByIdempotencyKey(cmd.organizationId, key);
    if (!existing) return undefined;
    if (existing.requestFingerprint !== fingerprint(cmd))
      return err({ code: "IDEMPOTENCY_KEY_REUSED" });
    return ok({ call: existing, replayed: true });
  }

  private publish(organizationId: OrganizationId, type: string, payload: Record<string, unknown>) {
    return this.deps.events.publish({
      type,
      version: 1,
      organizationId,
      occurredAt: this.deps.clock.now(),
      payload,
    });
  }
}
