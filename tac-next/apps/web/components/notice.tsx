import type { Banner, ErrorCopy } from "@tac/workspace";

/** 発信禁止のバナー（`suppressionBanner`）。危険は role=alert で読み上げる */
export function SuppressionBannerView({ banner }: { banner: Banner }) {
  const tone =
    banner.tone === "danger" ? "border-danger text-danger" : "border-warning text-warning";
  return (
    <div
      role={banner.tone === "danger" ? "alert" : "status"}
      data-testid="suppression-banner"
      className={`rounded-lg border-2 bg-surface-muted px-4 py-3 ${tone}`}
    >
      <p className="font-bold">
        <span aria-hidden="true">⛔ </span>
        {banner.title}
      </p>
      <p className="text-text-primary">{banner.body}</p>
    </div>
  );
}

/** 何が起きたか・何が安全か・次に何をするか（`errorCopy`） */
export function ErrorNotice({ copy, detail }: { copy: ErrorCopy; detail?: string | undefined }) {
  return (
    <div
      role="alert"
      data-testid="error-notice"
      className="rounded-lg border-2 border-danger px-4 py-3"
    >
      <p className="font-bold text-danger">
        <span aria-hidden="true">⚠ </span>
        {copy.what}
      </p>
      {detail ? <p>{detail}</p> : null}
      <p>{copy.safe}</p>
      <p className="text-text-secondary">{copy.next}</p>
    </div>
  );
}
