import type { NextAction } from "./outcome.js";

export const SCORE_FACTORS = [
  "eligibility",
  "fit",
  "contactability",
  "intent",
  "freshness",
  "engagement",
] as const;
export type ScoreFactor = (typeof SCORE_FACTORS)[number];
export type ScoreFactors = Partial<Record<ScoreFactor, number>>;

/** 現行 sales-rank の3軸（審査適性・属性の質・会える確度）を主軸にした初期の重み。 */
export const DEFAULT_WEIGHTS: Readonly<Record<ScoreFactor, number>> = {
  eligibility: 0.25,
  fit: 0.2,
  contactability: 0.2,
  intent: 0.2,
  freshness: 0.1,
  engagement: 0.05,
};

const LABEL: Readonly<Record<ScoreFactor, string>> = {
  eligibility: "審査適性",
  fit: "属性の質",
  contactability: "会える確度",
  intent: "温度感",
  freshness: "接触の新しさ",
  engagement: "反応の実績",
};

export interface Contribution {
  readonly factor: ScoreFactor;
  readonly score: number;
  readonly weight: number;
  /** priority への寄与（全要素の合計が priority になる） */
  readonly contribution: number;
}

export interface LeadScore {
  readonly priority: number;
  readonly contributions: readonly Contribution[];
  /** 「なぜおすすめか」。寄与の大きい順 */
  readonly explanation: readonly string[];
  readonly missing: readonly ScoreFactor[];
}

const round6 = (n: number) => Math.round(n * 1e6) / 1e6;

/** 説明可能な優先度。各要素は 0〜1、結果は 0〜100。未取得の要素は 0 として扱い missing に列挙する。 */
export function scoreLead(
  factors: ScoreFactors,
  weights: Readonly<Record<ScoreFactor, number>> = DEFAULT_WEIGHTS,
): LeadScore {
  const totalWeight = SCORE_FACTORS.reduce((a, f) => a + weights[f], 0);
  if (!(totalWeight > 0)) throw new RangeError("weights must sum to a positive number");

  const missing: ScoreFactor[] = [];
  const contributions = SCORE_FACTORS.map((factor) => {
    const raw = factors[factor];
    if (raw === undefined) missing.push(factor);
    const score = raw ?? 0;
    if (!(score >= 0 && score <= 1)) throw new RangeError(`${factor} must be within 0..1`);
    const weight = weights[factor] / totalWeight;
    return { factor, score, weight, contribution: round6(100 * weight * score) };
  }).sort((a, b) => b.contribution - a.contribution);

  const priority = round6(contributions.reduce((a, c) => a + c.contribution, 0));
  const explanation = contributions
    .filter((c) => c.contribution > 0)
    .slice(0, 3)
    .map(
      (c) =>
        `${LABEL[c.factor]}が${c.score >= 0.7 ? "高い" : "中程度"}（${Math.round(c.score * 100)}点）`,
    );
  return { priority, contributions, explanation, missing };
}

export interface NextActionInput {
  readonly suppressed: boolean;
  readonly followUpDue: boolean;
  readonly priority: number;
  readonly withinCallingWindow: boolean;
  readonly noAnswerAttemptsExhausted: boolean;
}

export interface NextActionDecision {
  readonly action: Exclude<NextAction, "NONE">;
  readonly reason: string;
  readonly confidence: number;
  readonly decidedBy: "rule" | "ai" | "human";
}

const LOW_PRIORITY = 40;

/** ルールによる次アクション。抑止中は何があっても DO_NOT_CONTACT（スコアより優先）。 */
export function decideNextAction(i: NextActionInput): NextActionDecision {
  const rule = (action: NextActionDecision["action"], reason: string, confidence: number) => ({
    action,
    reason,
    confidence,
    decidedBy: "rule" as const,
  });
  if (i.suppressed) return rule("DO_NOT_CONTACT", "発信禁止（抑止）に登録されています", 1);
  if (i.followUpDue) return rule("FOLLOW_UP", "約束したフォローアップの期日です", 0.9);
  if (i.noAnswerAttemptsExhausted) {
    return rule("HUMAN_REVIEW", "不在の再試行が上限に達しました。連絡方法を見直してください", 0.8);
  }
  if (i.priority < LOW_PRIORITY) return rule("CALL_LATER", "優先度が低いため、後回しにします", 0.5);
  if (!i.withinCallingWindow) return rule("CALL_LATER", "発信できる時間帯の外です", 0.9);
  return rule("CALL_NOW", "優先度が高く、今は発信できる時間帯です", Math.min(i.priority / 100, 1));
}
