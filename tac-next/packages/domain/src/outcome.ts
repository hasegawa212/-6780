import { type CallingWindowPolicy, nextWindowStart } from "./calling-window.js";
import { err, ok, type Result } from "./result.js";

export type OutcomeCategory = "POSITIVE" | "NEUTRAL" | "NO_CONTACT" | "NEGATIVE" | "INVALID";
export type Suppression = "NONE" | "CONTACT" | "NUMBER";
export type NextAction =
  | "NONE"
  | "CALL_NOW"
  | "CALL_LATER"
  | "FOLLOW_UP"
  | "SEND_MESSAGE"
  | "SCHEDULE"
  | "HUMAN_REVIEW"
  | "DO_NOT_CONTACT";

export interface OutcomePreset {
  readonly code: string;
  readonly label: string;
  readonly category: OutcomeCategory;
  readonly requiresFollowUp: boolean;
  readonly requiresSuppression: Suppression;
}

/** 現行アプリの5ボタン（先頭5つ）＋拡張分。組織ごとに増やせるよう、表示名と分類コードを分けている。 */
export const OUTCOME_PRESETS = [
  {
    code: "WON",
    label: "成約",
    category: "POSITIVE",
    requiresFollowUp: false,
    requiresSuppression: "NONE",
  },
  {
    code: "INTERESTED",
    label: "検討",
    category: "POSITIVE",
    requiresFollowUp: true,
    requiresSuppression: "NONE",
  },
  {
    code: "CALLBACK_REQUESTED",
    label: "折り返し",
    category: "NEUTRAL",
    requiresFollowUp: true,
    requiresSuppression: "NONE",
  },
  {
    code: "NO_ANSWER",
    label: "不在",
    category: "NO_CONTACT",
    requiresFollowUp: true,
    requiresSuppression: "NONE",
  },
  {
    code: "DO_NOT_CALL",
    label: "拒否",
    category: "NEGATIVE",
    requiresFollowUp: false,
    requiresSuppression: "CONTACT",
  },
  {
    code: "APPOINTMENT_SET",
    label: "アポ確定",
    category: "POSITIVE",
    requiresFollowUp: true,
    requiresSuppression: "NONE",
  },
  {
    code: "WRONG_NUMBER",
    label: "番号違い",
    category: "INVALID",
    requiresFollowUp: false,
    requiresSuppression: "NUMBER",
  },
  {
    code: "VOICEMAIL",
    label: "留守電",
    category: "NO_CONTACT",
    requiresFollowUp: true,
    requiresSuppression: "NONE",
  },
] as const satisfies readonly OutcomePreset[];

export type OutcomeCode = (typeof OUTCOME_PRESETS)[number]["code"];

export function outcomeFromLabel(label: string): OutcomeCode | undefined {
  return OUTCOME_PRESETS.find((p) => p.label === label.trim())?.code;
}

export type FollowUpKind = "FOLLOW_UP" | "CALLBACK" | "RETRY" | "APPOINTMENT";

export interface OutcomePlan {
  readonly suppress: Suppression;
  readonly followUp: { readonly kind: FollowUpKind; readonly dueAt: Date } | undefined;
  readonly nextAction: NextAction;
}

export interface OutcomePlanInput {
  readonly now: Date;
  readonly window: CallingWindowPolicy;
  /** この相手への何回目の発信か（1 始まり） */
  readonly attempt: number;
  readonly maxAttempts: number;
  readonly callbackAt?: Date;
  readonly appointmentAt?: Date;
}

export type OutcomePlanError =
  | "CALLBACK_TIME_REQUIRED"
  | "CALLBACK_TIME_IN_PAST"
  | "APPOINTMENT_TIME_REQUIRED";

const DAY_MS = 24 * 60 * 60 * 1000;
const INTERESTED_FOLLOW_UP_DAYS = 3;

const plan = (
  nextAction: NextAction,
  followUp?: OutcomePlan["followUp"],
  suppress: Suppression = "NONE",
): Result<OutcomePlan, never> => ok({ suppress, followUp, nextAction });

/** 結果から「抑止・フォローアップ・次アクション」を決める（純粋関数。保存はユースケースが行う）。 */
export function planOutcome(
  code: OutcomeCode,
  input: OutcomePlanInput,
): Result<OutcomePlan, OutcomePlanError> {
  const later = (days: number) =>
    nextWindowStart(new Date(input.now.getTime() + days * DAY_MS), input.window);

  switch (code) {
    case "WON":
      return plan("NONE");
    case "DO_NOT_CALL":
      return plan("DO_NOT_CONTACT", undefined, "CONTACT");
    case "WRONG_NUMBER":
      return plan("HUMAN_REVIEW", undefined, "NUMBER");
    case "INTERESTED":
      return plan("FOLLOW_UP", { kind: "FOLLOW_UP", dueAt: later(INTERESTED_FOLLOW_UP_DAYS) });
    case "CALLBACK_REQUESTED":
      if (!input.callbackAt) return err("CALLBACK_TIME_REQUIRED");
      if (input.callbackAt.getTime() <= input.now.getTime()) return err("CALLBACK_TIME_IN_PAST");
      // 本人が指定した日時なので、そのまま期日にする（発信時の時間帯ガードは別途かかる）
      return plan("CALL_LATER", { kind: "CALLBACK", dueAt: input.callbackAt });
    case "APPOINTMENT_SET":
      if (!input.appointmentAt) return err("APPOINTMENT_TIME_REQUIRED");
      return plan("SCHEDULE", { kind: "APPOINTMENT", dueAt: input.appointmentAt });
    case "NO_ANSWER":
    case "VOICEMAIL":
      // 不在の再試行は回数に上限を設け、間隔は1日以上あける（掛けすぎの防止）
      if (input.attempt >= input.maxAttempts) return plan("HUMAN_REVIEW");
      return plan("CALL_LATER", { kind: "RETRY", dueAt: later(1) });
  }
}
