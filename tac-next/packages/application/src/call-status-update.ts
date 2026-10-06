import { type CallStatus, reconcileProviderStatus } from "@tac/domain";
import type { CallRecord, CallRepository, OrganizationId } from "./ports.js";

const MAX_ATTEMPTS = 8;

export class CallStatusContentionError extends Error {
  constructor() {
    super("call status kept changing while applying an update");
    this.name = "CallStatusContentionError";
  }
}

/**
 * 通話の状態を incoming へ進める（進めてよいときだけ）。読み取り → 判定（reconcileProviderStatus）→ compare-and-set を、
 * 他の更新に割り込まれたらやり直す。古い読み取りで新しい状態を上書きしない（終端状態は後退しない）。
 */
export async function advanceCallStatus(
  calls: CallRepository,
  organizationId: OrganizationId,
  callId: string,
  incoming: CallStatus,
): Promise<{ call: CallRecord | undefined; applied: boolean }> {
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
    const current = await calls.get(organizationId, callId);
    if (!current) return { call: undefined, applied: false };
    const next = reconcileProviderStatus(current.status, incoming);
    if (!next.applied) return { call: current, applied: false };
    if (await calls.transitionStatus(organizationId, callId, current.status, next.status)) {
      return { call: { ...current, status: next.status }, applied: true };
    }
  }
  throw new CallStatusContentionError();
}
