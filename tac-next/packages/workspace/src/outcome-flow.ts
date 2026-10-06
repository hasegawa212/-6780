import { OUTCOME_PRESETS, type OutcomeCode, type Suppression } from "@tac/domain";

export interface OutcomeButton {
  readonly code: OutcomeCode;
  readonly label: string;
  readonly tone: "neutral" | "positive" | "danger";
  /** primary = 常に見せる（現行 TAC の5ボタン）、more = 「その他」に畳む */
  readonly group: "primary" | "more";
}

const PRIMARY_COUNT = 5;

/** 結果ボタン。現行 TAC の5ボタンを先頭に保ち、拡張分は畳む（Progressive Disclosure）。 */
export function outcomeButtons(): readonly OutcomeButton[] {
  return OUTCOME_PRESETS.map((p, i) => ({
    code: p.code,
    label: p.label,
    tone:
      p.requiresSuppression !== "NONE"
        ? "danger"
        : p.category === "POSITIVE"
          ? "positive"
          : "neutral",
    group: i < PRIMARY_COUNT ? "primary" : "more",
  }));
}

export type NextStep =
  | { readonly kind: "NONE"; readonly required: false }
  | { readonly kind: "PICK_DATETIME"; readonly required: true }
  | { readonly kind: "APPOINTMENT"; readonly required: true }
  | { readonly kind: "FOLLOW_UP"; readonly required: false }
  | {
      readonly kind: "CONFIRM_SUPPRESSION";
      readonly required: true;
      readonly scope: Exclude<Suppression, "NONE">;
    };

/** 結果を選んだ直後に出す「次にやること」。抑止を伴う結果は、分類が増えても必ず確認を挟む。 */
export function nextStepFor(code: OutcomeCode): NextStep {
  const preset = OUTCOME_PRESETS.find((p) => p.code === code);
  if (preset && preset.requiresSuppression !== "NONE") {
    return { kind: "CONFIRM_SUPPRESSION", required: true, scope: preset.requiresSuppression };
  }
  switch (code) {
    case "CALLBACK_REQUESTED":
      return { kind: "PICK_DATETIME", required: true };
    case "APPOINTMENT_SET":
      return { kind: "APPOINTMENT", required: true };
    case "INTERESTED":
      return { kind: "FOLLOW_UP", required: false };
    default:
      // 不在・留守電の再試行はサーバーが自動で予定する（planOutcome）。成約は追加入力なし
      return { kind: "NONE", required: false };
  }
}
