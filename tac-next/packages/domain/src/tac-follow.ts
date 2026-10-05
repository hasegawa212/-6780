import type { FollowUpKind, NextAction, Suppression } from "./outcome.js";
import { err, ok, type Result } from "./result.js";

export interface FollowCategoryMapping {
  readonly nextAction: NextAction;
  readonly followUpKind?: FollowUpKind;
  readonly suppression: Suppression;
}

/**
 * 現行 TAC のフォロー台帳の分類（telegram-ai-bot/tac/followup.py の CATEGORIES）を次世代の語彙へ写す。
 * 知らないラベルは推測しない。推測を誤ると、拒否した人が「掛けてよい人」に化けうるため。
 * 「連絡予定」「対応済み」は現行 UI の集計名で、台帳の分類ではない（EXISTING_APP_AUDIT.md）。
 */
const CATEGORIES: Readonly<Record<string, FollowCategoryMapping>> = {
  再調整希望: { nextAction: "CALL_LATER", followUpKind: "CALLBACK", suppression: "NONE" },
  日程返答待ち: { nextAction: "FOLLOW_UP", followUpKind: "FOLLOW_UP", suppression: "NONE" },
  不在: { nextAction: "CALL_LATER", followUpKind: "RETRY", suppression: "NONE" },
  要確認: { nextAction: "HUMAN_REVIEW", suppression: "NONE" },
  // 現行では DNC に入らず別経路から掛かりえた（監査 R13）。次世代では必ず抑止する。
  連絡停止: { nextAction: "DO_NOT_CONTACT", suppression: "CONTACT" },
};

export function followCategoryFromLabel(
  label: string,
): Result<FollowCategoryMapping, "UNKNOWN_FOLLOW_CATEGORY"> {
  const key = label.normalize("NFKC").trim();
  const mapping = Object.hasOwn(CATEGORIES, key) ? CATEGORIES[key] : undefined;
  return mapping ? ok(mapping) : err("UNKNOWN_FOLLOW_CATEGORY");
}
