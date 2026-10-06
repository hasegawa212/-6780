# AI_WORKFLOW — Claude Code × Codex の実行手順書

巨大なプロンプトを毎回貼らずに、**リポジトリを読むだけで** 開発・監査を再開できるようにするための手順書。
役割と契約は [`AGENTS.md`](../AGENTS.md)、Claude Code 向けの追加は [`CLAUDE.md`](../CLAUDE.md)。

## 役割
| 担当 | 役割 | 使う場所 |
|---|---|---|
| Claude Code | **BUILD**：設計・TDD 実装・修正・ドキュメント | このリポジトリ |
| Codex（別セッションの AI でもよい） | **BREAK**：独立 QA・破壊テスト・セキュリティ監査・最終判定 | 同じリポジトリ・同じブランチ |
| Git / CI | **CONFIRM**：最終的な証拠 | GitHub Actions `tac-next` |
| OpenAI Realtime 等 | 完成したアプリの**中で**動く音声 AI（開発用 AI ではない） | Phase 12 以降 |

基本ループ：**Claude Code BUILD → Codex BREAK → Claude Code FIX → Codex VERIFY → CI CONFIRM**。
同じ AI に自分の実装を合格判定させない。

## 現在地（2026-10-06）
- 済み：既存 TAC の監査、設計書一式、Phase 0・1、アプリ層の縦切り（インメモリ）、QA 1 回目（Claude Code 自身による。独立ではない）、⑤ UI/UX の仕様と画面に依存しない表示ロジック、この AI 運用一式
- 次：**Phase 2（Database）から最初の縦切りを API まで貫く** → 縦切り完成時に **Codex で初回の独立監査**
- 判定：NO-GO（`QA_REPORT.md`。DB・認証・API・Webhook 受信・音声・AI ツールが未実装）

## 手順（上から順に）

| STEP | 誰が | 何をする | 使う指示 | 状態 |
|---|---|---|---|---|
| 0 | 人 | リポジトリを用意し Claude Code で開く | — | 済 |
| 1 | Claude Code | 既存 TAC の調査・設計・TDD 戦略・計画 | 初回起動プロンプト（41863 相当） | 済（`EXISTING_APP_AUDIT`・`ARCHITECTURE` ほか） |
| 2 | Claude Code | AI 運用の土台（CLAUDE.md・AGENTS.md・docs） | Repository AI OS プロンプト（31574 相当） | 済（このファイル群） |
| 3 | 人 | 成果物の確認（下の「必読ファイル」が揃っているか） | — | 済 |
| 4〜6 | Claude Code | 最初の縦切りを TDD で完成：Contact → 電話番号 → 抑止 → 発信要求 → Fake Telephony（呼出・応答・話し中・拒否・タイムアウト・切断・留守電・障害・Webhook の重複/遅延/順序違い）→ 通話のライフサイクル → 結果 → フォローアップ | 下の「普段の BUILD 指示」 | **進行中**（ドメイン・アプリ層は済。DB・API・Fake Telephony のシナリオが残り） |
| 7〜9 | Codex | 縦切りの独立監査（破壊テスト） | [`agents/QA_AUDIT.md`](agents/QA_AUDIT.md) | 未（縦切り完成後） |
| 10 | Claude Code | Finding を CRITICAL → HIGH → MEDIUM → LOW の順に、再現テスト → RED → 修正 → GREEN | 下の「Finding の修正指示」 | — |
| 11〜12 | Codex ⇄ Claude Code | 再検証と修正を PASS まで繰り返す（DNC・テナント分離・冪等性・全発信停止は妥協しない） | 下の「再検証の指示」 | — |
| 13〜16 | Claude Code | UI 実装（Design Tokens → App Shell → Dashboard → Lead List → Call Workspace → Outcome → Follow-up → …）。縦切りを優先。実画面で各ビューポート・キーボード・状態を確認 | `UX.md`・`DESIGN_SYSTEM.md`・`SCREEN_SPEC.md` を読ませる | 仕様と表示ロジックは済。実装は API ができてから |
| 17 | Codex | UI の独立監査（連打・DNC・引き継ぎ・切断・リロード・複数タブ・モバイル・キーボード・アクセシビリティ） | [`agents/QA_AUDIT.md`](agents/QA_AUDIT.md) の UI 節 | — |
| 18〜20 | Claude Code | 本物の電話：Domain → Fake → Contract Test → 本番アダプタ。**実装前に公式ドキュメントを確認**。Fake → Sandbox → 承認済みテスト番号 → 管理された実通話の順。実顧客にテスト発信しない | 下の「プロバイダ実装の指示」 | — |
| 21〜28 | Claude Code | Realtime AI 音声。営業エージェントの System Prompt（74216 相当）は **アプリ内の音声 AI 用**。Global Safety → Compliance → Organization → Campaign → 会話状態 → 動的な CRM 文脈 に分割し、prompt_version・model・policy_version・campaign_version を通話ごとに記録。Tool Gateway は許可したツールだけ（書き込みは Schema・認可・テナント・Policy・Audit を必ず通す）。AI Eval と引き継ぎ E2E | `AI_AGENT.md`・`VOICE.md`・`COMPLIANCE.md` を読ませる | — |
| 29 | Codex | プロンプトインジェクション監査（発話・CRM メモ・ナレッジ・ツール出力・取り込みデータ） | [`agents/QA_AUDIT.md`](agents/QA_AUDIT.md) の AI 節 | — |
| 30〜33 | Claude Code | 観測（ログ・メトリクス・トレース・アラート）、全発信停止を API・worker レベルで、コストの安全装置、セキュリティ強化 | `SECURITY.md`・`OBSERVABILITY.md` | — |
| 34〜35 | Codex（**別の新しいセッション**） | 本番前の最終監査 → GO / GO WITH CONDITIONS / NO-GO を `PRODUCTION_READINESS_REVIEW.md` に保存 | [`agents/PRODUCTION_READINESS_AUDIT.md`](agents/PRODUCTION_READINESS_AUDIT.md) | — |
| 36〜38 | Claude Code ⇄ Codex | NO-GO なら出さない。修正 → 再監査（前回の Finding と新しい回帰の両方） | — | — |
| 39〜41 | 人＋Claude Code | Staging でリハーサル（デプロイ → マイグレーション → スモーク → E2E → AI Eval → 障害試験 → ロールバック）、直前チェックリスト、段階的リリース（社内 → 承認済みテスト → 小さなパイロット → 限定本番 → 拡大）。**本番操作は人が承認** | `DEPLOYMENT.md`・`RUNBOOK.md` | — |
| 42〜43 | 人＋両方 | 本番監視と、障害時の Contain → 調査 → 根本原因 → 再現テスト → 修正 → 検証 → ポストモーテム | `RUNBOOK.md` | — |

## 普段の BUILD 指示（Claude Code に送る）
```text
CLAUDE.md、docs/PROGRESS.md、docs/IMPLEMENTATION_PLAN.md、関連する ADR と仕様を読んでください。
現在の最優先の未完了タスクを TDD（RED → GREEN → REFACTOR）で実装し、必要な回帰テストを追加してください。
完了後、pnpm check / test:critical / test:mutation / build を実行し、PROGRESS.md を更新してコミットしてください。
安全な範囲では次の関連タスクまで進めてかまいません。
実電話・本番データの変更・本番シークレットの変更・元に戻せない本番操作はしないでください。
```

## 普段の BREAK 指示（Codex に送る）
```text
AGENTS.md、docs/PROGRESS.md、docs/ARCHITECTURE.md、docs/TESTING.md、docs/SECURITY.md、docs/CRITICAL_INVARIANTS.md を読んでください。
docs/agents/QA_AUDIT.md の手順で、現在完成している縦切りを独立監査してください。
実装が正しいと仮定せず、Critical Invariant を破るテストを優先してください。
Finding は AGENTS.md の形式で、証拠と再現テストを付けて報告してください。docs/QA_REPORT.md は最初に読まず、監査後に突き合わせてください。
```

## Finding の修正指示（Claude Code に送る）
```text
Codex の監査結果（<場所>）を確認し、CRITICAL → HIGH → MEDIUM → LOW の順に対応してください。
各バグはまず回帰テストで再現し、RED を確認してから最小の修正をしてください。既存のテストを弱めてはいけません。
修正後に全検証を実行し、RISK_REGISTER.md と PROGRESS.md を更新してください。
```

## 再検証の指示（Codex に送る）
```text
前回の Finding（<場所>）を再検証してください。修正されたと仮定せず、元の再現手順と回帰テストを確認してください。
修正による新しい回帰がないかも調べてください。
```

## プロバイダ実装の指示（Claude Code に送る）
```text
本番の電話プロバイダ（または AI プロバイダ）を実装する前に、最新の公式ドキュメントで
認証・発信の作成・SIP・メディア・Webhook（署名・再送）・エラー・再試行・冪等性・レート制限・セキュリティを確認してください。
記憶から API を推測して実装しないでください。確認できなかった点は UNKNOWN として ADR に残してください。
```

## Phase 別の必読
| Phase | 追加で読む |
|---|---|
| すべて | `PROGRESS` → `IMPLEMENTATION_PLAN` → `ARCHITECTURE` → `DOMAIN` → `DECISIONS` |
| 2 DB・3 認証 | `SECURITY`・`DATABASE`・`CRITICAL_INVARIANTS` |
| 9・10 UI | `UX`・`DESIGN_SYSTEM`・`SCREEN_SPEC` |
| 11 電話・12 音声・13 引き継ぎ | `VOICE`・`AI_AGENT`・`COMPLIANCE` |
| 15 観測・16 セキュリティ | `SECURITY`・`RISK_REGISTER`・`COMPLIANCE`・`OBSERVABILITY` |
| 18 デプロイ | `DEPLOYMENT`・`RUNBOOK`・`PRODUCTION_READINESS_REVIEW` |
