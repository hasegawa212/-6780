# ADR-0011: Agent の役割分担（BUILD / BREAK）とリポジトリを長期記憶にする運用

- Status: Accepted
- Date: 2026-10-06

## Context
開発は複数の AI セッション（Claude Code・Codex）と人で行い、会話は途切れたり圧縮されたりする。巨大なプロンプトを毎回貼る運用は再現性がなく、
同じ AI が自分の実装を合格判定すると見落としが残る（2026-10-05 の QA は開発者自身が実施しており、独立していない）。

## Decision
- 共通契約は `AGENTS.md`、Claude Code の追加分は `CLAUDE.md`。手順は `docs/AI_WORKFLOW.md`、BREAK 担当の手順は `docs/agents/`
- Claude Code は BUILD、Codex など別セッションは BREAK / VERIFY / 最終監査。最終判定（`PRODUCTION_READINESS_REVIEW.md`）は開発者が書かない
- 不変条件は `docs/CRITICAL_INVARIANTS.md` に層ごとの状態（PASS / UNKNOWN）で記録し、`pnpm test:critical` を CI で必須にする
- UI の業務ロジックは `packages/workspace`（画面に依存しない純粋関数）に置き、React コンポーネントに書かない

## Alternatives
- 1つの AI に開発と監査をさせる：独立性がない（不採用）
- プロンプトを docs にそのまま保存する：長すぎて読まれない。要点を手順書と契約に再構成した

## Consequences
- 新しいセッションは `CLAUDE.md`（または `AGENTS.md`）→ `PROGRESS.md` を読むだけで再開できる
- 状態の自己申告を避けるため、PASS は証拠（テスト・CI）のある層だけに付ける
