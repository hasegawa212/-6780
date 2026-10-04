/** 電話回線としての通話の状態（会話の中身は conversation.ts）。 */
export const CALL_STATUSES = [
  "REQUESTED",
  "DIALING",
  "RINGING",
  "IN_PROGRESS",
  "ENDED",
  "FAILED",
  "NO_ANSWER",
  "BUSY",
  "CANCELED",
] as const;
export type CallStatus = (typeof CALL_STATUSES)[number];

const NEXT: Readonly<Record<CallStatus, readonly CallStatus[]>> = {
  REQUESTED: ["DIALING", "FAILED", "CANCELED"],
  DIALING: ["RINGING", "FAILED"],
  RINGING: ["IN_PROGRESS", "NO_ANSWER", "BUSY", "FAILED"],
  IN_PROGRESS: ["ENDED", "FAILED"],
  ENDED: [],
  FAILED: [],
  NO_ANSWER: [],
  BUSY: [],
  CANCELED: [],
};

export function isTerminalCall(status: CallStatus): boolean {
  return NEXT[status].length === 0;
}

export function canTransitionCall(from: CallStatus, to: CallStatus): boolean {
  return NEXT[from].includes(to);
}

function isReachable(from: CallStatus, to: CallStatus): boolean {
  const seen = new Set<CallStatus>();
  const stack: CallStatus[] = [...NEXT[from]];
  while (stack.length > 0) {
    const s = stack.pop() as CallStatus;
    if (s === to) return true;
    if (seen.has(s)) continue;
    seen.add(s);
    stack.push(...NEXT[s]);
  }
  return false;
}

/**
 * プロバイダの Webhook が伝えてきた状態を、現在の状態に反映する。
 * Webhook は重複も順序の入れ替えも起きる（at-least-once）ので、
 * - 重複・後戻りになる古いイベントは無視する（applied=false）
 * - 途中のイベントが抜けていても、先の状態へ到達できるなら進める
 * 終端状態は二度と動かさない。
 */
export function reconcileProviderStatus(
  current: CallStatus,
  incoming: CallStatus,
): { status: CallStatus; applied: boolean } {
  if (current === incoming || isTerminalCall(current)) return { status: current, applied: false };
  if (isReachable(current, incoming)) return { status: incoming, applied: true };
  return { status: current, applied: false };
}
