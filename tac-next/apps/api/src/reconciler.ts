import type { ReconcileResult } from "@tac/application";
import { redactForLog } from "./app.js";

/**
 * 確定しない発信の照合（ADR-0016）を定期的に回す。前の回が終わるまで次を始めない。
 * ログには種類ごとの件数だけを出す（通話 ID・電話番号は出さない）。
 */
export function createReconcileLoop(
  run: () => Promise<ReconcileResult>,
  log: (message: string) => void,
) {
  let running = false;
  return {
    async tick(): Promise<void> {
      if (running) return;
      running = true;
      try {
        const { results } = await run();
        if (results.length === 0) return;
        const counts = new Map<string, number>();
        for (const r of results) counts.set(r.kind, (counts.get(r.kind) ?? 0) + 1);
        log(
          `reconcile: ${[...counts]
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([k, n]) => `${k}=${n}`)
            .join(" ")}`,
        );
      } catch (e) {
        log(`reconcile failed: ${redactForLog(e).split("\n")[0]}`);
      } finally {
        running = false;
      }
    },
  };
}
