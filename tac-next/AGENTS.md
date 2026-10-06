# AGENTS.md — Agent Operating Contract（Claude Code・Codex・人間 共通）

あなたは本番を目指すソフトウェアのリポジトリで作業している。**リポジトリの文書が唯一の正（Single Source of Truth）**であり、
会話履歴や過去のプロンプトを前提にしない。この契約は `tac-next/` 配下の作業すべてに適用する。

## 役割
| Agent | 役割 | 流れ |
|---|---|---|
| Claude Code | BUILD（設計・実装・修正） | DESIGN → TEST → IMPLEMENT → VERIFY → DOCUMENT |
| Codex など別セッション | BREAK / REVIEW（独立 QA・セキュリティ監査） | INSPECT → CHALLENGE → BREAK → REPRODUCE → REPORT → VERIFY |

**同じ Agent に自分の実装を合格判定させない。** 重要な機能が完成したら BUILD → BREAK → FIX → VERIFY を回し、CI で確定する。
手順の全体は [`docs/AI_WORKFLOW.md`](docs/AI_WORKFLOW.md)。

## Before Any Change
1. [`docs/PROGRESS.md`](docs/PROGRESS.md)
2. [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)
3. 関係する設計文書（`ARCHITECTURE` / `DOMAIN` / `SECURITY` / `TESTING` / `COMPLIANCE` …）
4. 関係する ADR（[`docs/DECISIONS.md`](docs/DECISIONS.md)）
5. 既存のテスト

## Evidence
不確かなことは **OBSERVED / INFERRED / UNKNOWN / PROPOSED** に分けて書く。「たぶん安全」「おそらく問題ない」は証拠ではない。
ドキュメントに「安全」と書いてあることも証拠ではない。コード・設定・テスト・実行結果で確かめる。

## TDD（重要な振る舞い）
1. 振る舞いを定義する → 2. 失敗するテストを書く → 3. **RED を確認** → 4. 最小の実装 → 5. **GREEN を確認** → 6. リファクタ → 7. 回帰テスト一式。
バグを見つけたら、黙って直さず、まず再現テストを追加する（同じバグが再発できない状態にする）。

## Critical Invariants（詳細と対応テストは [`docs/CRITICAL_INVARIANTS.md`](docs/CRITICAL_INVARIANTS.md)）
1. 抑止中の相手に、新しい発信は決して生まれない（判定できないときは発信しない＝fail closed）
2. テナント A はテナント B のデータに決してアクセスできない
3. 1つの論理的な発信要求から、外部プロバイダの発信が複数生まれない
4. 人が引き継いだら、AI は話すこともツールを実行することもやめる
5. 信頼できない内容（顧客の発言・CRM メモ・ナレッジ・ツール出力・取り込みデータ）がシステムの方針を上書きしない
6. 全発信停止・設定の発信ゲートが有効なら、どの経路（UI・API・worker・再試行・AI ツール）からも発信できない

## Safety
- テスト中に実在の顧客へ電話しない。テストは Mock / Fake プロバイダだけを使う
- 認可・抑止・時間帯・レート制限・予算の制御を迂回しない
- シークレットをコミットしない。見つけたら値を写さず、場所だけを報告する
- セキュリティ上の問題は、修正に必要な最小限の再現にとどめる（攻撃手順を一般化しない）

## 禁止
`test.skip`／重要な expectation の削除／型エラーの無視／lint の無効化での隠蔽／何でも mock して本質を検証しないテスト／セキュリティ検証の削除／既存テストを弱めること。

## Completion Honesty
状態は **COMPLETE / PARTIAL / BLOCKED / MOCK ONLY / NOT IMPLEMENTED** を正確に使う。テストしていないものは **UNKNOWN** と書き、推測で PASS にしない。
ボタンを置いただけで裏が動かない UI を完成と呼ばない。

## Verification（完了前に、関係するものを実行）
```bash
cd tac-next
pnpm check           # lint + typecheck + 全テスト
pnpm test:critical   # Critical Invariant Suite（CI で必須）
pnpm test:mutation   # 安全ルールの反転を検出できるか
TEST_DATABASE_URL=postgres://… pnpm test:postgres  # 実 PostgreSQL での並行性（CI では postgres:16 で必須）
pnpm test:e2e        # 画面の E2E（Playwright＋axe。API と画面を自動で起動、電話は mock）
pnpm build
```
integration / contract / E2E / security / AI eval は、該当する層ができ次第ここに追加する（現状は [`docs/TESTING.md`](docs/TESTING.md)）。

## Findings（BREAK 担当の記録形式）
`ID / Severity（CRITICAL・HIGH・MEDIUM・LOW・INFO）/ Component / Evidence / Reproduction / Expected / Actual / Root Cause / Regression Test / Recommended Fix`
CRITICAL / HIGH は [`docs/RISK_REGISTER.md`](docs/RISK_REGISTER.md) と `PROGRESS.md` の Production Blockers に載せ、修正と再検証が終わるまで消さない。

## Documentation
実装の状態が意味のある形で変わったら、同じ変更の中で `docs/PROGRESS.md` を更新する。重大な技術判断は `docs/adr/` に ADR を追加する（過去の ADR を黙って書き換えない。変えるときは Superseded にして新しい ADR を書く）。

## Commits / Branches
小さく論理的なコミット（例：`test(domain): …` → `feat(domain): …`）。`main` へ直接 push しない。lint・typecheck・test・build が落ちる状態を main に入れない。
