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
  test: { environment: "node", include: CRITICAL_TESTS },
});
