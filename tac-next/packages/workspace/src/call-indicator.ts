import { type CallStatus, type ConversationState, canAiSpeak, isTerminalCall } from "@tac/domain";

export type Tone = "neutral" | "info" | "success" | "warning" | "danger";
export type Speaker = "AI" | "CUSTOMER" | "HUMAN";
export type RealtimeConnection = "CONNECTED" | "RECONNECTING" | "LOST";

export interface CallIndicatorInput {
  readonly status: CallStatus;
  readonly conversation: ConversationState;
  readonly connection: RealtimeConnection;
  /** 直近に話している人（リアルタイムイベント由来。無ければ未定） */
  readonly speaker?: Speaker;
}

export interface Indicator {
  /** 色に頼らず状態が分かる文字ラベル */
  readonly label: string;
  /** 色の補助になる形（アイコン名。描画はUI側） */
  readonly icon: string;
  readonly tone: Tone;
  /** 通話がまだ生きているか */
  readonly live: boolean;
  /** 画面全体で目立たせる（トーストに任せない）べき状態か */
  readonly blocking: boolean;
  /** スクリーンリーダー向けの読み上げ文 */
  readonly announce: string;
}

const view = (
  label: string,
  icon: string,
  tone: Tone,
  live: boolean,
  blocking = false,
): Indicator => ({ label, icon, tone, live, blocking, announce: `通話状態：${label}` });

const TERMINAL: Readonly<Record<string, Indicator>> = {
  ENDED: view("通話終了", "phone-off", "neutral", false),
  FAILED: view("発信失敗", "alert-triangle", "danger", false),
  NO_ANSWER: view("応答なし", "phone-missed", "neutral", false),
  BUSY: view("話し中", "phone-busy", "neutral", false),
  CANCELED: view("発信取り消し", "x-circle", "neutral", false),
};

/** 通話ヘッダーに出す状態。文字・アイコン・色を必ずセットで返す。 */
export function callIndicator(i: CallIndicatorInput): Indicator {
  if (isTerminalCall(i.status)) {
    return TERMINAL[i.status] ?? view(i.status, "circle", "neutral", false);
  }
  // 通話中に画面との接続が切れたら、何よりも先に知らせる
  if (i.connection === "LOST") return view("接続が切れました", "wifi-off", "danger", true, true);
  if (i.connection === "RECONNECTING") return view("再接続しています", "refresh", "warning", true);

  switch (i.status) {
    case "REQUESTED":
      return view("発信準備中", "phone-outgoing", "info", true);
    case "DIALING":
      return view("発信中", "phone-outgoing", "info", true);
    case "RINGING":
      return view("呼び出し中", "phone-ringing", "info", true);
    default:
      break;
  }
  // IN_PROGRESS
  if (i.conversation.controller === "HUMAN")
    return view("あなたが対応中", "headset", "success", true);
  if (i.speaker === "AI" && canAiSpeak(i.conversation)) {
    return view("AI が話しています", "bot", "info", true);
  }
  if (i.speaker === "CUSTOMER") return view("お客様が話しています", "user", "info", true);
  return view("お客様とつながりました", "phone-call", "success", true);
}

export type AiActivity =
  | "LISTENING"
  | "THINKING"
  | "SPEAKING"
  | "SEARCHING_KNOWLEDGE"
  | "CHECKING_CALENDAR"
  | "PREPARING_HANDOFF"
  | "WAITING";

const AI_ACTIVITY_LABEL: Readonly<Record<AiActivity, string>> = {
  LISTENING: "聞いています",
  THINKING: "考えています",
  SPEAKING: "話しています",
  SEARCHING_KNOWLEDGE: "資料を確認しています",
  CHECKING_CALENDAR: "空き日程を確認しています",
  PREPARING_HANDOFF: "担当者への引き継ぎを準備しています",
  WAITING: "待機しています",
};

/** AI の活動表示。人が引き継いだ後は、遅れて届いたイベントがあっても「停止」と出す。 */
export function aiActivityView(
  activity: AiActivity,
  conversation: ConversationState,
): { label: string; stopped: boolean } {
  if (conversation.controller === "HUMAN") {
    return { label: "停止中（あなたが対応中）", stopped: true };
  }
  return { label: AI_ACTIVITY_LABEL[activity], stopped: false };
}

const LOW_CONFIDENCE = 0.6;

/** 確信度は低いときだけ知らせる。不明な値は低いものとして扱う（安全側）。 */
export function aiConfidenceNotice(confidence: number | undefined): string | undefined {
  if (confidence === undefined || !(confidence >= LOW_CONFIDENCE)) {
    return "人の確認をおすすめします";
  }
  return undefined;
}
