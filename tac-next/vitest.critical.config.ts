import { defineConfig } from "vitest/config";

// Critical Invariant Suite（docs/CRITICAL_INVARIANTS.md）。CI で必須。
// ここに挙げたテストが落ちたら、他が全部通っていてもマージしない。
export default defineConfig({
  test: {
    environment: "node",
    include: [
      // 抑止（DNC）: 抑止中の相手に新しい発信は生まれない
      "packages/domain/test/call-policy.test.ts",
      "packages/domain/test/utterance-safety.test.ts",
      "packages/domain/test/evals.test.ts",
      "packages/application/test/create-call.test.ts",
      "packages/application/test/call-queue.test.ts",
      "packages/application/test/record-outcome.test.ts",
      // 1つの発信要求は1件の外部発信だけ / 同時発信・TOCTOU
      "packages/application/test/adversarial.test.ts",
      // 全発信停止・設定のゲート
      "packages/application/test/deployment-gate.test.ts",
      "packages/config/test/config.test.ts",
      // 状態機械（後戻りしない）・人の引き継ぎで AI が止まる
      "packages/domain/test/call-status.test.ts",
      "packages/domain/test/conversation.test.ts",
      // 画面側の安全ガード（表示だけ。最終判定はサーバー）
      "packages/workspace/test/suppression-banner.test.ts",
      "packages/workspace/test/call-starter.test.ts",
    ],
  },
});
