# CLAUDE.md — tac-next（Claude Code 用の常設指示）

すべての Agent に共通の契約は [`AGENTS.md`](AGENTS.md)。このファイルは Claude Code（**BUILD 担当**）向けの追加分だけを書く。
詳細はリンク先に置き、このファイルは短く保つ。

## Mission
株式会社 Martial Arts の TAC 自動テレアポ（参照実装: `../telegram-ai-bot/tac/`、本番 `https://tac-martial-arts.fly.dev/tac/app`）を監査し、
**AI + Human Hybrid Sales Engagement Platform** として本番品質で作り直す。

## Priority
**Safety > Compliance > Correctness > Reliability > Operator UX > Conversion > AI Autonomy**

## セッション開始時（会話履歴に頼らない）
1. [`docs/PROGRESS.md`](docs/PROGRESS.md)：現在地・次のタスク・Blocker
2. [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)：Phase と依存関係
3. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)・[`docs/DOMAIN.md`](docs/DOMAIN.md)・[`docs/DECISIONS.md`](docs/DECISIONS.md)（ADR）
4. 作業する Phase に応じて追加で読む（[`docs/AI_WORKFLOW.md`](docs/AI_WORKFLOW.md) の「Phase 別の必読」）
5. **最優先の未完了タスク**から再開する

## Required Workflow
DISCOVER → AUDIT → MODEL → DESIGN → SPECIFY → TEST → IMPLEMENT → VERIFY → HARDEN → DEPLOY

重要なドメインの振る舞いは必ず **RED → GREEN → REFACTOR**（失敗するテストを先に書き、失敗を確認してから実装）。

## Never
- 抑止（DNC）・時間帯・上限・予算・全発信停止を迂回するコードを書かない
- テストを弱めて（削除・期待値の緩和・`skip`・型/lint の無効化）グリーンにしない
- セキュリティチェックを外してテストを通さない
- シークレットをコミット・ログ出力しない（`.env.example` はダミー値のみ）
- テストから本物の電話をかけない（local / test は `TELEPHONY_PROVIDER=mock` 以外で起動しない）
- 実装していないものを完了と書かない（`AGENTS.md` の Completion Honesty）
- プロバイダの API を記憶で作らない。実装前に公式ドキュメントを確認し、確認できなければ UNKNOWN と書く

## 承認なしに実行しないこと
実在の顧客への発信／本番 DB の削除／元に戻せない本番マイグレーション／大量の課金処理／本番シークレットの変更／実在の顧客への AI 自動発信／`fly deploy`。

## Commands（`tac-next/` で実行）
```bash
pnpm install
pnpm check           # lint + typecheck + test（全テスト）
pnpm test:critical   # Critical Invariant Suite（docs/CRITICAL_INVARIANTS.md）
pnpm test:mutation   # 安全ルールを反転させるとテストが落ちることの確認
TEST_DATABASE_URL=postgres://… pnpm test:postgres  # 実 PostgreSQL での並行性（CI では postgres:16 で必須）
pnpm test:e2e        # 画面の E2E（Playwright＋axe。API と画面を自動で起動、電話は mock）
pnpm build
```

## Completion（タスクの終わり方）
テスト追加 → `pnpm check` → `pnpm test:critical` → `pnpm test:mutation` → `pnpm build` → 関連ドキュメント更新 → `docs/PROGRESS.md` 更新 → 小さく論理的なコミット。
CI の結果を最終的な証拠にする（「通りました」という自己申告だけで完了にしない）。

## 構成の要点
- `packages/domain`：純粋関数（I/O なし）。`packages/application`：ユースケースとポート（インメモリ実装は `testing/`）
- `packages/workspace`：画面に依存しない表示ロジック（UI はここを使い、React に業務ロジックを書かない）
- `packages/telephony`：プロバイダのアダプタ（現在は Mock＝電話シミュレーターのみ）／`packages/config`：Zod で検証する設定（危険な機能は既定 OFF）
- `apps/api`：HTTP（Hono）。入出力の検証・認可・応答の形だけで、業務ロジックはユースケースに置く（ADR-0013）。起動は `pnpm --filter @tac/api start`
- `apps/web`：画面（Next.js、webpack で動かす。ADR-0014）。業務の判断は `packages/workspace` に置き、React は表示の組み立てだけ
- `packages/db`：ポートの PostgreSQL 実装（Drizzle）。SQL マイグレーションが正・RLS・`TenantScope`（ADR-0012）。DB に触るコードは必ず `TenantScope` を通す
- コードのコメント・UI 文言・ドキュメントは日本語。コミットは `feat(tac-next): …` 形式
