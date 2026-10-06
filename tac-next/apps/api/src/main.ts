import { startServer } from "./server.js";

// 起動の入口（`pnpm --filter @tac/api start`）。設定は環境変数から読む
const server = await startServer(process.env).catch((e: unknown) => {
  console.error(e instanceof Error ? e.message : e);
  process.exit(1);
});

for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.once(signal, () => {
    void server.close().then(() => process.exit(0));
  });
}
