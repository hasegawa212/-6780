import { defineConfig } from "vitest/config";

// 本物の PostgreSQL での並行性テスト（packages/db/test-postgres）。TEST_DATABASE_URL が必須。
// 通常の `pnpm test` には含めない（DB サーバーが要るため）。CI では postgres:16 のサービスで必ず実行する。
export default defineConfig({
  test: {
    environment: "node",
    include: ["packages/*/test-postgres/**/*.test.ts"],
    // 各ファイルが同じ DB にマイグレーションを流すので、ファイル同士は直列にする
    // （空の DB に同時に流すと schema_migrations の作成で衝突する。ファイル内の並行性テストはそのまま）
    fileParallelism: false,
    hookTimeout: 60_000,
    testTimeout: 30_000,
  },
});
