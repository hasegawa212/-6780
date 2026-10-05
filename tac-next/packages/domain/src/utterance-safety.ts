import {
  type ConversationState,
  enterSafety,
  highestPrioritySafety,
  type SafetyEffect,
  type SafetyPhase,
} from "./conversation.js";

/*
 * 顧客の発話から Safety 状態を検知する（AI が応答する前に毎回通す）。
 * 取りこぼし（拒否者への勧誘継続）より過検知（1 件の会話が終わる）のほうが安全なので、
 * 迷ったら検知する側に倒す。ただし「〜で結構です」（肯定）は除く。
 * 移植元: hasegawa212/- の sales-engagement-platform（2026-10-05 に tac-next へ統合, ADR-0008）。
 */

// はっきりした「連絡しないで」→ DO_NOT_CALL
const DO_NOT_CALL: readonly RegExp[] = [
  /電話(を)?(し|かけ)(て(こ|く)ない|ない)で/,
  /(電話|連絡)(して|し)?(こないで|くるな|しないで)/,
  /かけ(て)?(こ|く)ないで/,
  /二度と(電話|連絡|かけ)/,
  /連絡(は)?(不要|いらない|要らない|しないで)/,
  /(リスト|名簿)から(消|外|削除)/,
  /番号を(消|削除)/,
  /迷惑/,
  /(営業|勧誘)(電話)?(は)?(お断り|断る|やめて)/,
  /stop\s+calling/i,
  /(do\s+not|don'?t)\s+call/i,
  /remove\s+(me|my\s+number)/i,
];

// 契約しない旨の意思表示 → STOP_REQUESTED（宅建業法施行規則16条の11 の再勧誘禁止を想定〔要法務確認〕）
const STOP_REQUESTED: readonly RegExp[] = [
  /(?<![でデ])(結構|けっこう|ケッコウ)(です|デス|だ|でございます)/,
  /もう(いい|大丈夫)(です)?$/,
  /興味(が|は)?(ない|ありません|無い)/,
  /必要(が|は)?(ない|ありません|無い)/,
  /(いら|要ら)ない|いりません|要りません/,
  /お断り(します|です)/,
  /(やめて|止めて)(ください|下さい)?$/,
  /not\s+interested/i,
];

// クレーム → COMPLAINT（AI は受け止めず人へ渡す）
const COMPLAINT: readonly RegExp[] = [
  /しつこい/,
  /クレーム|苦情/,
  /消費(者|生活)センター|国民生活センター/,
  /警察|弁護士|訴え/,
  /(責任者|上司|上の人)を出(せ|して)/,
  /ふざけ(る|ん)な/,
];

const matches = (rules: readonly RegExp[], text: string) => rules.some((re) => re.test(text));

/** 検知した Safety 状態（優先度順ではない。優先度の判定は highestPrioritySafety に任せる）。 */
export function detectSafetySignals(utterance: string): SafetyPhase[] {
  const t = utterance.normalize("NFKC").trim();
  if (t === "") return [];
  const found: SafetyPhase[] = [];
  if (matches(DO_NOT_CALL, t)) found.push("DO_NOT_CALL");
  else if (matches(STOP_REQUESTED, t)) found.push("STOP_REQUESTED");
  if (matches(COMPLAINT, t)) found.push("COMPLAINT");
  return found;
}

/** 発話を会話状態に反映する。Safety を検知したら enterSafety の規則（優先度・効果）に従う。 */
export function applyCustomerUtterance(
  state: ConversationState,
  utterance: string,
): { state: ConversationState; effects: readonly SafetyEffect[] } {
  const strongest = highestPrioritySafety(detectSafetySignals(utterance));
  return strongest ? enterSafety(state, strongest) : { state, effects: [] };
}
