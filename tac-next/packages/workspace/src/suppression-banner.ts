/**
 * 画面から見た抑止（発信禁止）の状態。
 * UNKNOWN は「サーバーから確定した値をまだ受け取っていない」。発信可否が分からないので発信させない。
 */
export type SuppressionUiState = "NONE" | "SAVING" | "SAVED" | "SAVE_FAILED" | "UNKNOWN";

export interface Banner {
  readonly title: string;
  readonly body: string;
  readonly tone: "danger" | "warning";
  readonly action?: string;
}

export function suppressionBanner(state: SuppressionUiState): Banner | undefined {
  switch (state) {
    case "SAVED":
      return { title: "発信禁止", body: "今後、この相手には発信できません。", tone: "danger" };
    case "SAVING":
      return {
        title: "発信禁止を保存しています",
        body: "保存が終わるまで発信できません。",
        tone: "warning",
      };
    case "SAVE_FAILED":
      return {
        title: "発信禁止を保存できませんでした",
        body: "確認が終わるまで、この相手への発信は止めています。",
        tone: "danger",
        action: "もう一度保存する",
      };
    case "UNKNOWN":
      return {
        title: "発信禁止の状態を確認できません",
        body: "確認できるまで、この相手への発信は止めています。",
        tone: "warning",
      };
    case "NONE":
      return undefined;
  }
}

/**
 * 発信ボタンを出してよいか。抑止が NONE と確定しているときだけ。
 * これは表示の判断にすぎず、最終的な発信可否はサーバー（create-call）が必ず判定する。
 */
export function canOfferCall(i: { suppression: SuppressionUiState; activeCall: boolean }): boolean {
  return i.suppression === "NONE" && !i.activeCall;
}
