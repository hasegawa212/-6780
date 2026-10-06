import type { CallMode, ConversationState } from "@tac/domain";
import type { CallStartSettlement } from "./call-starter.js";
import type { ErrorKind } from "./screen-state.js";
import type { SuppressionUiState } from "./suppression-banner.js";

/**
 * API の応答を画面の表示へ対応付ける（Phase 9）。画面は表示を決めるだけで、発信の可否はサーバーが決める。
 */

/** `GET /v1/contacts/{id}` の suppression → 画面の抑止の状態。確定していない値はすべて UNKNOWN（発信させない） */
export function suppressionFromApi(value: string | undefined): SuppressionUiState {
  if (value === "NONE") return "NONE";
  if (value === "SUPPRESSED") return "SAVED";
  return "UNKNOWN";
}

/**
 * `POST /v1/calls` の応答から、発信の成否が確定したかを決める（CallStarter.settle に渡す）。
 * 504（プロバイダの応答なし）・応答そのものが無い・想定外の 5xx は、発信されたか分からない → UNKNOWN
 */
export function callStartSettlement(httpStatus: number | undefined): CallStartSettlement {
  if (httpStatus === undefined) return "UNKNOWN";
  if (httpStatus < 500 || httpStatus === 502) return "CONFIRMED";
  return "UNKNOWN";
}

/** 発信の失敗のエラーコード（無ければ通信の失敗）→ 画面の文言の種類 */
export function errorKindForCallError(code: string | undefined): ErrorKind {
  switch (code) {
    case "CONTACT_SUPPRESSED":
      return "CALL_BLOCKED_SUPPRESSED";
    case "OUTBOUND_STOPPED":
      return "CALL_BLOCKED_STOPPED";
    case "PROVIDER_TIMEOUT":
    case undefined:
      return "CALL_START_UNKNOWN";
    default:
      return "CALL_START_FAILED";
  }
}

const REASONS: Readonly<Record<string, string>> = {
  OUTBOUND_DISABLED_BY_CONFIG: "この環境では発信が無効になっています（設定）",
  OUTBOUND_STOPPED: "全発信が停止中です",
  ORGANIZATION_PAUSED: "組織の発信が一時停止中です",
  CAMPAIGN_PAUSED: "キャンペーンが一時停止中です",
  CONTACT_SUPPRESSED: "発信禁止の相手です",
  COUNTRY_NOT_ALLOWED: "発信できない国の番号です",
  OUTSIDE_CALLING_WINDOW: "発信できる時間帯の外です",
  DAILY_CAP_REACHED: "今日の発信の上限に達しました",
  NUMBER_DAILY_LIMIT_REACHED: "この番号への今日の発信回数の上限に達しました",
  DISCLOSURE_INCOMPLETE: "名乗りの情報が足りません",
  AI_VOICE_DISABLED_BY_CONFIG: "この環境では AI 音声が無効です（設定）",
  AI_VOICE_OUTBOUND_DISABLED: "組織で AI 音声の発信が許可されていません",
  CONSENT_REQUIRED: "AI 音声の発信には相手の同意が必要です",
  CONCURRENCY_LIMIT_REACHED: "同時に掛けられる件数の上限です",
  BUDGET_EXCEEDED: "予算の上限に達しました",
  CONTACT_ALREADY_IN_CALL: "この相手には別の担当者が通話中です",
};

/** 発信判定の拒否理由の説明。知らない理由はコードをそのまま見せない */
export function blockedReasonLabel(code: string): string {
  return REASONS[code] ?? "発信の条件を満たしていません";
}

/** 通話の担当（人が掛けた通話は最初から人、AI 音声は AI） */
export function conversationForMode(mode: CallMode): ConversationState {
  return mode === "HUMAN_DIALED"
    ? { phase: "DISCLOSURE", controller: "HUMAN" }
    : { phase: "DISCLOSURE", controller: "AI" };
}
