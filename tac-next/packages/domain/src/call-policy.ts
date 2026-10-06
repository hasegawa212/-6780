export type CallMode = "HUMAN_DIALED" | "AI_VOICE";

export type CallDenialCode =
  | "OUTBOUND_DISABLED_BY_CONFIG"
  | "AI_VOICE_DISABLED_BY_CONFIG"
  | "OUTBOUND_STOPPED"
  | "ORGANIZATION_PAUSED"
  | "CAMPAIGN_PAUSED"
  | "CONTACT_SUPPRESSED"
  | "COUNTRY_NOT_ALLOWED"
  | "OUTSIDE_CALLING_WINDOW"
  | "DAILY_CAP_REACHED"
  | "NUMBER_DAILY_LIMIT_REACHED"
  | "DISCLOSURE_INCOMPLETE"
  | "AI_VOICE_OUTBOUND_DISABLED"
  | "CONSENT_REQUIRED"
  | "CONCURRENCY_LIMIT_REACHED"
  | "BUDGET_EXCEEDED";

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
  // ---- 本番の安全装置（ADR-0006） ----
  /** 全発信停止（STOP ALL OUTBOUND CALLS） */
  readonly outboundStopped: boolean;
  readonly organizationPaused: boolean;
  readonly campaignPaused: boolean;
  /** いま回線に乗っている（発信依頼〜通話中の）通話の数 */
  readonly activeCalls: number;
  readonly maxConcurrentCalls: number;
  /** 予算の残り（円）。null は予算を設定していない */
  readonly budgetRemaining: number | null;
  /**
   * デプロイ時の設定ゲート（ADR-0010、`packages/config` の features）。必須の入力にして、
   * 組み立て側が包み忘れても効かなくなることがないようにしている。
   */
  readonly deployment: {
    readonly outboundCallsEnabled: boolean;
    readonly aiVoiceEnabled: boolean;
  };
}

export type CallPolicyDecision =
  | { readonly allowed: true }
  | {
      readonly allowed: false;
      readonly code: CallDenialCode;
      readonly reasons: readonly CallDenialCode[];
    };

/**
 * 発信してよいかを判定する。停止系 → 抑止 → その他の順に評価し、拒否理由はすべて返す（先頭が主な理由）。
 * どれか1つでも不可なら発信しない。
 */
export function evaluateCallPolicy(f: CallPolicyFacts): CallPolicyDecision {
  const reasons: CallDenialCode[] = [];
  // 停止系は抑止より先に評価する（止めているときは何よりも「止めている」ことが主な理由）
  // 設定のゲートを先頭に置き、組織の停止スイッチと区別できる理由にする（解除しても発信できない原因を取り違えない）
  if (!f.deployment.outboundCallsEnabled) reasons.push("OUTBOUND_DISABLED_BY_CONFIG");
  if (f.outboundStopped) reasons.push("OUTBOUND_STOPPED");
  if (f.organizationPaused) reasons.push("ORGANIZATION_PAUSED");
  if (f.campaignPaused) reasons.push("CAMPAIGN_PAUSED");
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
    // AI 音声で話す発信は、設定と組織の両方で有効にしたうえで、相手ごとの同意がある場合に限る（ADR-0003・0010）
    if (!f.deployment.aiVoiceEnabled) reasons.push("AI_VOICE_DISABLED_BY_CONFIG");
    else if (!f.aiVoiceOutboundEnabled) reasons.push("AI_VOICE_OUTBOUND_DISABLED");
    else if (!f.hasValidConsent) reasons.push("CONSENT_REQUIRED");
  }
  if (f.activeCalls >= f.maxConcurrentCalls) reasons.push("CONCURRENCY_LIMIT_REACHED");
  if (f.budgetRemaining !== null && f.budgetRemaining <= 0) reasons.push("BUDGET_EXCEEDED");
  const [code] = reasons;
  return code === undefined ? { allowed: true } : { allowed: false, code, reasons };
}
