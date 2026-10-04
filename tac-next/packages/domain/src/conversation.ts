import { err, ok, type Result } from "./result.js";

export const SALES_PHASES = [
  "DISCLOSURE",
  "IDENTIFICATION",
  "QUALIFICATION",
  "DISCOVERY",
  "OBJECTION",
  "INTERESTED",
  "SCHEDULING",
  "WRAP_UP",
  "COMPLETED",
] as const;

/** 営業より優先する状態。配列の並びがそのまま優先順位（先頭が最優先）。 */
export const SAFETY_PHASES = [
  "EMERGENCY",
  "LEGAL_BLOCK",
  "DO_NOT_CALL",
  "STOP_REQUESTED",
  "PRIVACY_REQUEST",
  "ABUSE",
  "HUMAN_REQUIRED",
] as const;

export type SalesPhase = (typeof SALES_PHASES)[number];
export type SafetyPhase = (typeof SAFETY_PHASES)[number];
export type ConversationPhase = SalesPhase | SafetyPhase | "HUMAN_HANDOFF";
export type Controller = "AI" | "HUMAN";

export interface ConversationState {
  readonly phase: ConversationPhase;
  readonly controller: Controller;
}

export type SafetyEffect =
  | "END_CONVERSATION"
  | "ADD_SUPPRESSION"
  | "REQUEST_HANDOFF"
  | "CREATE_PRIVACY_TASK"
  | "AUDIT";

const NEXT: Readonly<Record<ConversationPhase, readonly ConversationPhase[]>> = {
  // 名乗り（事業者名・勧誘目的の明示）を飛ばして本題に入れないよう、DISCLOSURE の次は本人確認だけ。
  DISCLOSURE: ["IDENTIFICATION"],
  IDENTIFICATION: ["QUALIFICATION", "WRAP_UP"],
  QUALIFICATION: ["DISCOVERY", "WRAP_UP"],
  DISCOVERY: ["OBJECTION", "INTERESTED", "WRAP_UP"],
  OBJECTION: ["DISCOVERY", "INTERESTED", "WRAP_UP"],
  INTERESTED: ["SCHEDULING"],
  SCHEDULING: ["WRAP_UP"],
  WRAP_UP: ["COMPLETED"],
  COMPLETED: [],
  // Safety から営業フェーズへは戻さない。終わらせるか、人へ渡すかだけ。
  EMERGENCY: ["COMPLETED"],
  LEGAL_BLOCK: ["COMPLETED"],
  DO_NOT_CALL: ["COMPLETED"],
  STOP_REQUESTED: ["COMPLETED"],
  PRIVACY_REQUEST: ["COMPLETED"],
  ABUSE: ["COMPLETED"],
  HUMAN_REQUIRED: ["HUMAN_HANDOFF", "COMPLETED"],
  HUMAN_HANDOFF: ["COMPLETED"],
};

const EFFECTS: Readonly<Record<SafetyPhase, readonly SafetyEffect[]>> = {
  EMERGENCY: ["REQUEST_HANDOFF", "AUDIT"],
  LEGAL_BLOCK: ["END_CONVERSATION", "AUDIT"],
  DO_NOT_CALL: ["END_CONVERSATION", "ADD_SUPPRESSION", "AUDIT"],
  // 「もう電話しないで」は拒否と同じく再勧誘の禁止対象なので抑止する。
  STOP_REQUESTED: ["END_CONVERSATION", "ADD_SUPPRESSION", "AUDIT"],
  PRIVACY_REQUEST: ["END_CONVERSATION", "ADD_SUPPRESSION", "CREATE_PRIVACY_TASK", "AUDIT"],
  ABUSE: ["END_CONVERSATION", "AUDIT"],
  HUMAN_REQUIRED: ["REQUEST_HANDOFF", "AUDIT"],
};

const HANDS_TO_HUMAN: ReadonlySet<SafetyPhase> = new Set(["EMERGENCY", "HUMAN_REQUIRED"]);

export function isSafetyPhase(phase: ConversationPhase): phase is SafetyPhase {
  return (SAFETY_PHASES as readonly string[]).includes(phase);
}

const rank = (s: SafetyPhase) => SAFETY_PHASES.indexOf(s);

export function highestPrioritySafety(detected: readonly SafetyPhase[]): SafetyPhase | undefined {
  return [...detected].sort((a, b) => rank(a) - rank(b))[0];
}

export function startConversation(): ConversationState {
  return { phase: "DISCLOSURE", controller: "AI" };
}

/** 営業フェーズ・引き継ぎ・終了への遷移。Safety へは enterSafety を使う。 */
export function transitionPhase(
  state: ConversationState,
  to: ConversationPhase,
): Result<ConversationState, "FORBIDDEN_TRANSITION"> {
  if (isSafetyPhase(to) || !NEXT[state.phase].includes(to)) return err("FORBIDDEN_TRANSITION");
  return ok({ ...state, phase: to });
}

/**
 * Safety 状態に入る。営業のどのフェーズからでも入れる。
 * すでにより優先度の高い Safety にいるときは何も変えない（弱い信号で上書きしない）。
 */
export function enterSafety(
  state: ConversationState,
  safety: SafetyPhase,
): { state: ConversationState; effects: readonly SafetyEffect[] } {
  if (state.phase === "COMPLETED") return { state, effects: [] };
  if (isSafetyPhase(state.phase) && rank(state.phase) <= rank(safety)) {
    return { state, effects: [] };
  }
  const controller: Controller = HANDS_TO_HUMAN.has(safety) ? "HUMAN" : state.controller;
  return { state: { phase: safety, controller }, effects: EFFECTS[safety] };
}

/** 人が会話を引き継ぐ。以後 AI は resumeAi されるまで話さない。 */
export function takeOver(state: ConversationState): ConversationState {
  return { ...state, controller: "HUMAN" };
}

/** 人が明示的に AI へ戻す。Safety 中・終了後は戻せない。 */
export function resumeAi(state: ConversationState): ConversationState {
  if (isSafetyPhase(state.phase) || state.phase === "COMPLETED") return state;
  return { ...state, controller: "AI" };
}

export function canAiSpeak(state: ConversationState): boolean {
  return state.controller === "AI" && !isSafetyPhase(state.phase) && state.phase !== "COMPLETED";
}
