import { defineConfig, devices } from "@playwright/test";

/*
 * E2E（SCREEN_SPEC §11）。API（APP_ENV=test・メモリ上の PGlite・電話は mock のシミュレーター）と、
 * ビルド済みの画面を起動して、ブラウザで操作する。本物の電話はかけない。
 */
const API_PORT = 18080;
const WEB_PORT = 13000;
export const DEMO_PASSWORD = "e2e-demo-password-123";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "pnpm --filter @tac/api start",
      url: `http://127.0.0.1:${API_PORT}/v1/me`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        APP_ENV: "test",
        PORT: String(API_PORT),
        TELEPHONY_PROVIDER: "mock",
        OUTBOUND_CALLS_ENABLED: "true",
        MOCK_WEBHOOK_SECRET: "e2e-webhook-secret-at-least-32-characters",
        DEV_SEED_PASSWORD: DEMO_PASSWORD,
      },
    },
    {
      command: `pnpm build && pnpm exec next start --port ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}/login`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: { API_ORIGIN: `http://127.0.0.1:${API_PORT}` },
    },
  ],
});
