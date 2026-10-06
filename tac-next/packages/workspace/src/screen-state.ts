export type ScreenState =
  | "LOADING"
  | "EMPTY"
  | "SUCCESS"
  | "PARTIAL"
  | "ERROR"
  | "OFFLINE"
  | "UNAUTHORIZED"
  | "FORBIDDEN";

export interface ScreenInput {
  readonly auth: "OK" | "UNAUTHENTICATED" | "FORBIDDEN";
  readonly online: boolean;
  readonly loading: boolean;
  readonly error?: boolean;
  /** 手元にあるデータの件数。まだ一度も取れていなければ undefined */
  readonly itemCount: number | undefined;
}

/** 主要画面の状態を1つに決める（State Matrix の行を選ぶ）。 */
export function screenState(i: ScreenInput): ScreenState {
  if (i.auth === "UNAUTHENTICATED") return "UNAUTHORIZED";
  if (i.auth === "FORBIDDEN") return "FORBIDDEN";
  const has = i.itemCount !== undefined;
  if (!i.online && !has) return "OFFLINE";
  if (i.loading && !has) return "LOADING";
  if (i.error) return has ? "PARTIAL" : "ERROR";
  if (i.itemCount === 0) return "EMPTY";
  return "SUCCESS";
}

export type ErrorKind =
  | "CALL_START_FAILED"
  | "CALL_START_UNKNOWN"
  | "CALL_BLOCKED_SUPPRESSED"
  | "CALL_BLOCKED_STOPPED"
  | "OUTCOME_SAVE_FAILED"
  | "REALTIME_LOST";

export interface ErrorCopy {
  /** 何が起きたか */
  readonly what: string;
  /** 何が安全か（何は起きていないか） */
  readonly safe: string;
  /** 次に何をすればよいか */
  readonly next: string;
}

const COPY: Readonly<Record<ErrorKind, ErrorCopy>> = {
  CALL_START_FAILED: {
    what: "発信できませんでした。",
    safe: "発信はされていません。",
    next: "もう一度試すか、管理者に連絡してください。",
  },
  CALL_START_UNKNOWN: {
    what: "発信されたかどうか確認できませんでした。",
    safe: "二重発信を防ぐため、自動では再発信しません。",
    next: "通話履歴で状態を確認してから操作してください。",
  },
  CALL_BLOCKED_SUPPRESSED: {
    what: "この相手は発信禁止のため、発信できません。",
    safe: "発信はされていません。",
    next: "解除が必要な場合は管理者に依頼してください。",
  },
  CALL_BLOCKED_STOPPED: {
    what: "組織の全発信が停止中のため、発信できません。",
    safe: "発信はされていません。",
    next: "再開は管理者が行います。",
  },
  OUTCOME_SAVE_FAILED: {
    what: "通話結果を保存できませんでした。",
    safe: "入力内容は画面に残っています。",
    next: "もう一度保存してください。",
  },
  REALTIME_LOST: {
    what: "通話画面の接続が切れました。",
    safe: "表示は最後に受信した時点のものです。AI の操作は止めています。",
    next: "再接続するか、通話を終了してください。",
  },
};

export function errorCopy(kind: ErrorKind): ErrorCopy {
  return COPY[kind];
}
