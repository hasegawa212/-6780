import { existsSync } from "node:fs";
import { defineConfig } from "vitest/config";

// Critical Invariant Suite（docs/CRITICAL_INVARIANTS.md）。CI で必須。
// ここに挙げたテストが落ちたら、他が全部通っていてもマージしない。
// scripts/mutation-smoke.mjs もこの一覧だけで変異を検出できることを確かめる。
export const CRITICAL_TESTS = [
  // INV-1 抑止（DNC）: 抑止中の相手に新しい発信は生まれない
  "packages/domain/test/call-policy.test.ts",
  "packages/domain/test/utterance-safety.test.ts",
  "packages/domain/test/evals.test.ts",
  "packages/domain/test/tac-follow.test.ts", // 現行 TAC の「連絡停止」→ 抑止
  "packages/application/test/create-call.test.ts",
  "packages/application/test/call-queue.test.ts",
  "packages/application/test/record-outcome.test.ts",
  // INV-1 / INV-2 / INV-3 の DB 層（PGlite）：抑止の永続化・RLS・一意制約・組織ロック・権限
  "packages/db/test/use-cases.test.ts",
  "packages/db/test/tenant-isolation.test.ts",
  "packages/db/test/repositories.test.ts",
  "packages/db/test/migrate.test.ts",
  // INV-2 / INV-1 / INV-3 / INV-6 の API 層：認証・CSRF・ロール・発信 API・Webhook の署名（Phase 3・7・8）
  "apps/api/test/auth.test.ts",
  "apps/api/test/calls.test.ts",
  "apps/api/test/webhooks.test.ts",
  "packages/application/test/auth.test.ts",
  "packages/db/test/auth-webhooks.test.ts",
  // Webhook：重複・順序違い・遅延・同時到着で状態が後退しない（Phase 7）／シミュレーター（Phase 8）
  "packages/application/test/provider-events.test.ts",
  "packages/telephony/test/simulator.test.ts",
  // INV-3 1つの発信要求は1件の外部発信だけ / 同時発信・TOCTOU
  "packages/application/test/adversarial.test.ts",
  // INV-6 全発信停止・設定のゲート / 時間外は発信しない（ENFORCE_CALLING_WINDOW）
  "packages/config/test/config.test.ts",
  "packages/domain/test/calling-window.test.ts",
  // INV-4 状態機械（後戻りしない）・人の引き継ぎで AI が止まる
  "packages/domain/test/call-status.test.ts",
  "packages/domain/test/conversation.test.ts",
  // 画面側の安全ガード（表示だけ。最終判定はサーバー）
  "packages/workspace/test/suppression-banner.test.ts",
  "packages/workspace/test/call-starter.test.ts",
  "packages/workspace/test/outcome-flow.test.ts",
  "packages/workspace/test/call-indicator.test.ts",
];

// ファイル名の変更・分割で、黙って必須ゲートから外れることを防ぐ
const missing = CRITICAL_TESTS.filter((f) => !existsSync(new URL(f, import.meta.url)));
if (missing.length > 0) {
  throw new Error(
    `Critical Invariant Suite のテストファイルが見つかりません: ${missing.join(", ")}`,
  );
}

export default defineConfig({
  // PGlite の起動とマイグレーションに数秒かかるため、フックの上限を延ばす
  test: { environment: "node", include: CRITICAL_TESTS, hookTimeout: 60_000 },
});
