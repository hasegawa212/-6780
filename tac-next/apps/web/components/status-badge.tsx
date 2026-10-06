import type { Indicator, Tone } from "@tac/workspace";

const TONE: Record<Tone, string> = {
  neutral: "border-border-strong text-text-primary",
  info: "border-info text-info",
  success: "border-success text-success",
  warning: "border-warning text-warning",
  danger: "border-danger text-danger",
};

// アイコンは文字の記号で代用（色だけに頼らず、文字ラベルと必ずセットで出す。DESIGN_SYSTEM §1）
const ICON: Record<string, string> = {
  "phone-outgoing": "↗",
  "phone-ringing": "☎",
  "phone-call": "☎",
  headset: "🎧",
  "phone-off": "■",
  "phone-missed": "✕",
  "phone-busy": "⏸",
  "alert-triangle": "⚠",
  "x-circle": "✕",
  "wifi-off": "⚠",
  refresh: "↻",
  bot: "AI",
  user: "●",
};

/** CallStatusBadge（`callIndicator` の結果をそのまま描く） */
export function StatusBadge({ indicator }: { indicator: Indicator }) {
  return (
    <div
      role="status"
      aria-live={indicator.blocking ? "assertive" : "polite"}
      aria-label={indicator.announce}
      data-testid="call-status"
      className={`inline-flex items-center gap-2 rounded-lg border-2 px-3 py-2 font-bold ${TONE[indicator.tone]}`}
    >
      <span aria-hidden="true">{ICON[indicator.icon] ?? "●"}</span>
      <span>{indicator.label}</span>
    </div>
  );
}
