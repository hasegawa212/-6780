export type CallMode = "HUMAN_DIALED" | "AI_VOICE";

export type CallDenialCode =
  | "CONTACT_SUPPRESSED"
  | "COUNTRY_NOT_ALLOWED"
  | "OUTSIDE_CALLING_WINDOW"
  | "DAILY_CAP_REACHED"
  | "NUMBER_DAILY_LIMIT_REACHED"
  | "DISCLOSURE_INCOMPLETE"
  | "AI_VOICE_OUTBOUND_DISABLED"
  | "CONSENT_REQUIRED";

/** 発信の可否を決めるための事実。集めるのはユースケース、判定するのはここ。 */
export interface CallPolicyFacts {
  readonly suppressed: boolean;
  readonly countryAllowed: boolean;
  readonly withinCallingWindow: boolean;
  readonly dialedToday: number;
  /** null は上限なし */
  readonly dailyCap: number | null;
  readonly callsToNumberToday: number;
  readonly perNumberDailyLimit: number;
  /** 勧誘に先立って告げる項目（事業者名・担当者名・商品の種類） */
  readonly disclosure: {
    readonly companyName: string;
    readonly agentName: string;
    readonly product: string;
  };
  readonly mode: CallMode;
  readonly aiVoiceOutboundEnabled: boolean;
  readonly hasValidConsent: boolean;
}

export type CallPolicyDecision =
  | { readonly allowed: true }
  | {
      readonly allowed: false;
      readonly code: CallDenialCode;
      readonly reasons: readonly CallDenialCode[];
    };

/**
 * 発信してよいかを判定する。抑止を最初に評価し、拒否理由はすべて返す（先頭が主な理由）。
 * どれか1つでも不可なら発信しない。
 */
export function evaluateCallPolicy(f: CallPolicyFacts): CallPolicyDecision {
  const reasons: CallDenialCode[] = [];
  if (f.suppressed) reasons.push("CONTACT_SUPPRESSED");
  if (!f.countryAllowed) reasons.push("COUNTRY_NOT_ALLOWED");
  if (!f.withinCallingWindow) reasons.push("OUTSIDE_CALLING_WINDOW");
  if (f.dailyCap !== null && f.dialedToday >= f.dailyCap) reasons.push("DAILY_CAP_REACHED");
  if (f.callsToNumberToday >= f.perNumberDailyLimit) reasons.push("NUMBER_DAILY_LIMIT_REACHED");
  const { companyName, agentName, product } = f.disclosure;
  if (![companyName, agentName, product].every((v) => v.trim() !== "")) {
    reasons.push("DISCLOSURE_INCOMPLETE");
  }
  if (f.mode === "AI_VOICE") {
    // AI 音声で話す発信は、機能を有効にしたうえで、相手ごとの同意がある場合に限る（ADR-0003）
    if (!f.aiVoiceOutboundEnabled) reasons.push("AI_VOICE_OUTBOUND_DISABLED");
    else if (!f.hasValidConsent) reasons.push("CONSENT_REQUIRED");
  }
  const [code] = reasons;
  return code === undefined ? { allowed: true } : { allowed: false, code, reasons };
}
