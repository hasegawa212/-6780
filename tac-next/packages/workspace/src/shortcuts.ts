import type { Controller } from "@tac/domain";

export type ShortcutAction =
  | "OPEN_COMMAND_PALETTE"
  | "FOCUS_SEARCH"
  | "CLOSE_OVERLAY"
  | "TOGGLE_MUTE"
  | "TAKE_OVER"
  | "FOCUS_NOTE"
  | "OPEN_FOLLOW_UP";

export interface KeyInput {
  readonly key: string;
  readonly ctrlKey: boolean;
  readonly metaKey: boolean;
  readonly altKey: boolean;
  readonly shiftKey: boolean;
  /** 日本語入力の変換中 */
  readonly isComposing: boolean;
  /** input / textarea / contenteditable にフォーカスがある */
  readonly targetEditable: boolean;
  readonly context: { readonly callActive: boolean; readonly controller: Controller };
}

/**
 * キー入力をショートカットに対応づける。
 * - 1文字ショートカットは、文字入力中・変換中・修飾キー付きでは発動しない
 * - 通話終了には1文字ショートカットを割り当てない（誤って切らないため。ボタンは常に見える位置）
 */
export function resolveShortcut(e: KeyInput): ShortcutAction | undefined {
  if (e.isComposing) return undefined;
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;

  if ((e.ctrlKey || e.metaKey) && !e.altKey && key === "k") return "OPEN_COMMAND_PALETTE";
  if (key === "Escape") return "CLOSE_OVERLAY";
  if (e.ctrlKey || e.metaKey || e.altKey || e.targetEditable) return undefined;

  if (key === "/") return "FOCUS_SEARCH";
  if (!e.context.callActive) return undefined;
  switch (key) {
    case "m":
      return "TOGGLE_MUTE";
    case "t":
      return e.context.controller === "AI" ? "TAKE_OVER" : undefined;
    case "n":
      return "FOCUS_NOTE";
    case "f":
      return "OPEN_FOLLOW_UP";
    default:
      return undefined;
  }
}
