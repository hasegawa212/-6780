"use client";

import type { OutcomeCode } from "@tac/domain";
import { errorCopy, nextStepFor, type OutcomeButton, outcomeButtons } from "@tac/workspace";
import { useId, useState } from "react";
import { ErrorNotice } from "./notice";

export interface OutcomeSubmission {
  code: OutcomeCode;
  /** 折り返し・アポの日時（ISO 8601） */
  at?: string;
}

const TONE: Record<OutcomeButton["tone"], string> = {
  neutral: "border-border-strong",
  positive: "border-success",
  danger: "border-danger text-danger",
};

/**
 * OutcomeBar：現行の5ボタン＋「その他」、選んだ後の次にやること（`outcomeButtons`・`nextStepFor`）。
 * 保存はサーバーの確定を待ち、失敗しても選択と入力を残す（楽観的 UI にしない）。
 */
export function OutcomeBar({ onSubmit }: { onSubmit: (s: OutcomeSubmission) => Promise<void> }) {
  const [selected, setSelected] = useState<OutcomeButton>();
  const [at, setAt] = useState("");
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const dateId = useId();
  const buttons = outcomeButtons();
  const step = selected ? nextStepFor(selected.code) : undefined;
  const needsDate = step?.kind === "PICK_DATETIME" || step?.kind === "APPOINTMENT";
  const canSave = selected !== undefined && !saving && (!needsDate || at !== "");

  const save = async () => {
    if (!selected || !canSave) return;
    setSaving(true);
    setFailed(false);
    try {
      await onSubmit({
        code: selected.code,
        ...(needsDate ? { at: new Date(at).toISOString() } : {}),
      });
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };

  const button = (b: OutcomeButton) => (
    <button
      key={b.code}
      type="button"
      aria-pressed={selected?.code === b.code}
      onClick={() => setSelected(b)}
      className={`min-h-11 rounded-lg border-2 px-4 font-bold ${TONE[b.tone]} aria-pressed:bg-surface-muted aria-pressed:ring-2 aria-pressed:ring-focus-ring`}
    >
      {b.label}
    </button>
  );

  return (
    <section aria-labelledby="outcome-heading" className="space-y-3">
      <h2 id="outcome-heading" className="text-lg font-bold">
        通話の結果
      </h2>
      <div className="flex flex-wrap gap-2">
        {buttons.filter((b) => b.group === "primary").map(button)}
      </div>
      <details>
        <summary className="cursor-pointer text-text-secondary">その他の結果</summary>
        <div className="mt-2 flex flex-wrap gap-2">
          {buttons.filter((b) => b.group === "more").map(button)}
        </div>
      </details>

      {needsDate ? (
        <div>
          <label htmlFor={dateId} className="block font-bold">
            {step?.kind === "APPOINTMENT" ? "アポの日時（必須）" : "折り返しの日時（必須）"}
          </label>
          <input
            id={dateId}
            type="datetime-local"
            required
            value={at}
            onChange={(e) => setAt(e.target.value)}
            className="min-h-11 rounded border border-border-strong bg-surface px-2"
          />
        </div>
      ) : null}
      {step?.kind === "CONFIRM_SUPPRESSION" ? (
        <p role="note" className="rounded-lg border-2 border-danger px-4 py-2 text-danger">
          保存すると、この相手は発信禁止になります。今後この相手には発信できません。
        </p>
      ) : null}

      {failed ? <ErrorNotice copy={errorCopy("OUTCOME_SAVE_FAILED")} /> : null}
      <button
        type="button"
        disabled={!canSave}
        onClick={save}
        className="min-h-11 rounded-lg bg-accent px-6 font-bold text-white disabled:opacity-50"
      >
        {saving
          ? "保存しています…"
          : step?.kind === "CONFIRM_SUPPRESSION"
            ? "発信禁止にして保存"
            : "結果を保存"}
      </button>
    </section>
  );
}
