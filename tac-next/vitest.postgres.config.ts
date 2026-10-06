import { defineConfig } from "vitest/config";

// 本物の PostgreSQL での並行性テスト（packages/db/test-postgres）。TEST_DATABASE_URL が必須。
// 通常の `pnpm test` には含めない（DB サーバーが要るため）。CI では postgres:16 のサービスで必ず実行する。
export default defineConfig({
  test: {
    environment: "node",
    include: ["packages/*/test-postgres/**/*.test.ts"],
    hookTimeout: 60_000,
    testTimeout: 30_000,
  },
});
