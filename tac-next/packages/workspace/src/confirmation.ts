export type ConfirmationLevel = "IMMEDIATE" | "UNDO" | "CONFIRM" | "STRONG_CONFIRM";

export type GuardedAction =
  | "START_CALL"
  | "END_CALL"
  | "SAVE_NOTE"
  | "SKIP_LEAD"
  | "MARK_DNC"
  | "TRANSFER"
  | "ENGAGE_KILL_SWITCH"
  | "REMOVE_DNC"
  | "RELEASE_KILL_SWITCH"
  | "ENABLE_AUTO_DIAL";

interface Rule {
  readonly level: ConfirmationLevel;
  readonly title: string;
  readonly consequence: string;
}

/**
 * 重さに応じて確認の強さを変える（何でもダイアログにしない）。
 * - 通話終了は緊急停止を兼ねるので即時
 * - 取り返しのつく操作は確認ではなく「取り消し」で対応
 * - 発信を再び可能にする操作（禁止の解除・全停止の解除・自動発信 ON）は、取り消しに頼らず強い確認
 */
const RULES: Readonly<Record<GuardedAction, Rule>> = {
  START_CALL: { level: "IMMEDIATE", title: "発信", consequence: "この相手に発信します。" },
  END_CALL: { level: "IMMEDIATE", title: "通話を終了", consequence: "通話を切ります。" },
  SAVE_NOTE: { level: "IMMEDIATE", title: "メモを保存", consequence: "メモを保存します。" },
  SKIP_LEAD: { level: "UNDO", title: "スキップ", consequence: "この相手を後回しにします。" },
  MARK_DNC: {
    level: "CONFIRM",
    title: "発信禁止に登録",
    consequence: "この相手には、今後、発信できなくなります。",
  },
  TRANSFER: { level: "CONFIRM", title: "転送", consequence: "通話を転送先へ渡します。" },
  ENGAGE_KILL_SWITCH: {
    level: "CONFIRM",
    title: "全発信を停止",
    consequence: "組織のすべての発信（手動・自動・再試行）が止まります。通話中の通話は切れません。",
  },
  REMOVE_DNC: {
    level: "STRONG_CONFIRM",
    title: "発信禁止を解除",
    consequence: "この相手に再び発信できるようになります。解除は監査記録に残ります。",
  },
  RELEASE_KILL_SWITCH: {
    level: "STRONG_CONFIRM",
    title: "全発信の停止を解除",
    consequence: "組織の発信が再び可能になります。",
  },
  ENABLE_AUTO_DIAL: {
    level: "STRONG_CONFIRM",
    title: "自動発信を ON にする",
    consequence: "人が1件ずつ押さなくても、キューから続けて発信されます。",
  },
};

export function confirmationFor(action: GuardedAction): ConfirmationLevel {
  return RULES[action].level;
}

export interface ConfirmationSpec {
  readonly level: ConfirmationLevel;
  readonly title: string;
  readonly target: string;
  readonly consequence: string;
  /** 強い確認では、対象名をそのまま入力させる */
  readonly typeToConfirm?: string;
}

/** 確認ダイアログの中身：何を（title）・誰に（target）・どうなるか（consequence）。 */
export function describeConfirmation(action: GuardedAction, target: string): ConfirmationSpec {
  const r = RULES[action];
  const spec = { level: r.level, title: r.title, target, consequence: r.consequence };
  return r.level === "STRONG_CONFIRM" ? { ...spec, typeToConfirm: target } : spec;
}
