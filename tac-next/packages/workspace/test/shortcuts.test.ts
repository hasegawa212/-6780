import { describe, expect, it } from "vitest";
import { type KeyInput, resolveShortcut } from "../src/shortcuts.js";

const key = (k: string, extra: Partial<KeyInput> = {}): KeyInput => ({
  key: k,
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  shiftKey: false,
  isComposing: false,
  targetEditable: false,
  context: { callActive: true, controller: "AI" },
  ...extra,
});

describe("resolveShortcut: 通話中のショートカット", () => {
  it("M=ミュート / T=引き継ぎ / N=メモ / F=フォローアップ", () => {
    expect(resolveShortcut(key("m"))).toBe("TOGGLE_MUTE");
    expect(resolveShortcut(key("t"))).toBe("TAKE_OVER");
    expect(resolveShortcut(key("n"))).toBe("FOCUS_NOTE");
    expect(resolveShortcut(key("f"))).toBe("OPEN_FOLLOW_UP");
  });

  it("大文字（Caps Lock）でも同じ動作", () => {
    expect(resolveShortcut(key("M"))).toBe("TOGGLE_MUTE");
  });

  it("文字入力中は1文字ショートカットを発動しない", () => {
    expect(resolveShortcut(key("m", { targetEditable: true }))).toBeUndefined();
    expect(resolveShortcut(key("t", { targetEditable: true }))).toBeUndefined();
  });

  it("日本語の変換中（IME）は発動しない", () => {
    expect(resolveShortcut(key("t", { isComposing: true }))).toBeUndefined();
  });

  it("修飾キー付きは1文字ショートカットとみなさない（ブラウザの操作を奪わない）", () => {
    expect(resolveShortcut(key("t", { ctrlKey: true }))).toBeUndefined();
    expect(resolveShortcut(key("m", { metaKey: true }))).toBeUndefined();
  });

  it("通話していないときは、通話用ショートカットは効かない", () => {
    expect(
      resolveShortcut(key("m", { context: { callActive: false, controller: "AI" } })),
    ).toBeUndefined();
  });

  it("すでに人が対応中なら、T は何もしない", () => {
    expect(
      resolveShortcut(key("t", { context: { callActive: true, controller: "HUMAN" } })),
    ).toBeUndefined();
  });

  it("通話終了には1文字ショートカットを割り当てない（誤って切らない）", () => {
    for (const k of "abcdefghijklmnopqrstuvwxyz") {
      expect(resolveShortcut(key(k))).not.toBe("END_CALL");
    }
  });
});

describe("resolveShortcut: どこでも使えるもの", () => {
  it("Ctrl+K / ⌘K でコマンドパレット（入力中でも開ける）", () => {
    expect(resolveShortcut(key("k", { ctrlKey: true, targetEditable: true }))).toBe(
      "OPEN_COMMAND_PALETTE",
    );
    expect(resolveShortcut(key("k", { metaKey: true }))).toBe("OPEN_COMMAND_PALETTE");
  });

  it("/ で検索欄へ（入力中は文字として扱う）", () => {
    expect(resolveShortcut(key("/"))).toBe("FOCUS_SEARCH");
    expect(resolveShortcut(key("/", { targetEditable: true }))).toBeUndefined();
  });

  it("Escape はダイアログを閉じる（入力中でも）", () => {
    expect(resolveShortcut(key("Escape", { targetEditable: true }))).toBe("CLOSE_OVERLAY");
  });
});
