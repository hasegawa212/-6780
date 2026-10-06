import type { CallStatus } from "@tac/domain";
import { advanceCallStatus } from "./call-status-update.js";
import type { Deps } from "./deps.js";

/** プロバイダの状態通知（Webhook を検証・正規化した後の形） */
export interface ProviderEventCommand {
  readonly provider: string;
  /** プロバイダが付けたイベントの ID（重複排除のキー） */
  readonly eventId: string;
  readonly providerCallId: string | undefined;
  /** 発信時にプロバイダへ渡した通話 ID（プロバイダの ID が未記録の通話を特定するため） */
  readonly callId: string | undefined;
  readonly status: CallStatus;
  readonly occurredAt: Date;
  /** 受け取った生データ（監査・再処理のために保存する） */
  readonly payload: unknown;
}

export type ProviderEventResult =
  | { readonly kind: "APPLIED"; readonly callId: string; readonly status: CallStatus }
  /** 重複・後戻りのため状態は変えなかった（記録だけ） */
  | { readonly kind: "STALE"; readonly callId: string; readonly status: CallStatus }
  | { readonly kind: "DUPLICATE" }
  | { readonly kind: "UNKNOWN_CALL" };

/**
 * Webhook の状態通知を通話に反映する（Phase 7）。
 * 受信箱で同じイベントを1回だけ処理し、状態は advanceCallStatus（compare-and-set）で進める。
 * 処理に失敗したら受信箱の処理権を手放し、プロバイダの再送で処理し直せるようにする。
 */
export class ApplyProviderEventUseCase {
  constructor(private readonly deps: Deps) {}

  async execute(cmd: ProviderEventCommand): Promise<ProviderEventResult> {
    const { deps } = this;
    const claimed = await deps.providerEvents.begin({
      provider: cmd.provider,
      eventId: cmd.eventId,
      receivedAt: deps.clock.now(),
      payload: cmd.payload,
    });
    if (!claimed) return { kind: "DUPLICATE" };
    try {
      // 状態の変更とイベントの記録は1つのトランザクション（片方だけ残さない）
      const result = await deps.uow.run(() => this.apply(cmd));
      await deps.providerEvents.complete(cmd.provider, cmd.eventId, deps.clock.now());
      return result;
    } catch (e) {
      await deps.providerEvents.release(cmd.provider, cmd.eventId).catch(() => undefined);
      throw e;
    }
  }

  private async apply(cmd: ProviderEventCommand): Promise<ProviderEventResult> {
    const { deps } = this;
    const located = await deps.callLocator.locate(cmd.provider, cmd.providerCallId, cmd.callId);
    if (!located) return { kind: "UNKNOWN_CALL" };
    const { organizationId, callId } = located;

    if (cmd.providerCallId !== undefined) {
      await deps.calls.attachProvider(organizationId, callId, cmd.provider, cmd.providerCallId);
    }
    const advanced = await advanceCallStatus(deps.calls, organizationId, callId, cmd.status);
    if (!advanced.call) return { kind: "UNKNOWN_CALL" };

    await deps.callEvents.append({
      organizationId,
      callId,
      provider: cmd.provider,
      eventId: cmd.eventId,
      status: cmd.status,
      applied: advanced.applied,
      occurredAt: cmd.occurredAt,
      receivedAt: deps.clock.now(),
    });
    if (advanced.applied) {
      await deps.events.publish({
        type: "CallStatusChanged",
        version: 1,
        organizationId,
        occurredAt: cmd.occurredAt,
        payload: { callId, status: advanced.call.status },
      });
    }
    return {
      kind: advanced.applied ? "APPLIED" : "STALE",
      callId,
      status: advanced.call.status,
    };
  }
}
