import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["packages/*/test/**/*.test.ts", "apps/*/test/**/*.test.ts"],
    environment: "node",
    // PGlite の起動・マイグレーションは並列実行の負荷で数秒かかる（期待値ではなく時間の上限だけを延ばす）
    testTimeout: 30_000,
    hookTimeout: 60_000,
  },
});
