# ADR-0002: 技術スタック

- Status: Accepted（Phase ごとに再確認）
- Date: 2026-10-04
- 版数は 2026-10-04 時点で npm registry から確認した最新安定版

| 領域 | 採用 | 確認した版 | 理由 | 却下した候補 |
|---|---|---|---|---|
| 言語 | TypeScript (strict) | 7.0.2 | 型で外部データ境界を守る | — |
| ランタイム | Node.js | 22 LTS | Fly/Docker で標準 | Bun（本番実績で劣る） |
| パッケージ | pnpm workspace | 10.33 | モノレポの依存分離 | npm workspaces |
| API | Hono + @hono/zod-openapi | 4.13 | 軽量・Web 標準・OpenAPI 自動生成 | Express（型/OpenAPI が後付け）、Next.js Route Handler（UI と API の寿命を分けたい） |
| 検証 | Zod | 4.6 | 実行時検証と型を一元化 | — |
| DB | PostgreSQL 16+ | — | RLS・トランザクション・JSONB | — |
| ORM | Drizzle | 0.45 | SQL に近く RLS/生 SQL を書きやすい | Prisma（RLS とセッション変数の扱いが重い） |
| テスト DB | PGlite | — | Docker なしで本物の Postgres を CI/ローカルで | SQLite（方言差で嘘のグリーンになる） |
| キュー | Postgres ベース（graphile-worker 予定） | Phase 6 で確定 | 発信ジョブを業務データと同一トランザクションで投入できる（outbox 不要） | Redis + BullMQ（インフラが1つ増え、二重書き込み問題が出る） |
| テスト | Vitest + fast-check | 5.0 / 4.10 | 高速・ESM ネイティブ・プロパティベーステスト | Jest |
| E2E | Playwright | — | 環境に Chromium 同梱 | Cypress |
| Lint/Format | Biome | 2.5 | 1ツールで lint+format、速い | ESLint+Prettier（設定が二重） |
| UI | Next.js + React + Tailwind | 16.3 / 19.3 | Phase 8 で導入 | — |
| 観測 | OpenTelemetry | Phase 14 | ベンダー非依存 | — |

## Consequences
- Phase 0〜1 は domain / application のみなので、外部依存は Zod・Vitest・fast-check・Biome だけに絞る。
- 各 Phase の開始時に該当パッケージの版と公式ドキュメントを再確認する。
