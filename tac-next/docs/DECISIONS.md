# DECISIONS — 技術判断の一覧

各 ADR は Context / Decision / Alternatives / Consequences の形で `docs/adr/` にある。

| # | 判断 | 状態 | 要点 |
|---|---|---|---|
| [0001](adr/0001-modular-monolith.md) | Modular Monolith + Event-driven boundaries | Accepted | apps（api / worker / web）＋ packages（domain / application / telephony / db …）。業務状態はすべて PostgreSQL に置く |
| [0002](adr/0002-stack.md) | 技術スタック | Accepted | TypeScript 7・Node 22・pnpm・Hono・Zod 4・PostgreSQL＋Drizzle・PGlite・Vitest 5＋fast-check・Biome・Next.js 16（版は npm で確認済み） |
| [0003](adr/0003-ai-outbound-policy.md) | AI 音声による発信は既定 OFF・同意必須 | Accepted | 既定は人がタップして発信。予測発信はしない。拒否の検知は会話より優先 |
| [0004](adr/0004-telephony-abstraction.md) | Telephony Provider の抽象化 | Accepted | Mock / Twilio / OpenAI SIP をアダプタで差し替え。local / test では Mock 以外を作れない |
| [0005](adr/0005-call-state-vs-controller.md) | 回線状態と「話している主体」を分ける | Accepted | Call（回線）・Conversation（フェーズ＋controller）・CallJob（キュー）の3つ |
| [0006](adr/0006-production-safety-controls.md) | 本番の安全装置 | Accepted | 全発信停止・組織/キャンペーンの一時停止・同時通話数・予算を発信ガードの先頭で評価 |
| [0007](adr/0007-validated-config.md) | 起動時に検証する設定 | Accepted | Zod で検証・安全装置は既定 ON・シークレットは `[REDACTED]` |
| [0008](adr/0008-consolidate-sales-engagement-platform.md) | 並行実装を tac-next に統合 | Accepted | sales-engagement-platform の強み（抑止 fail closed・発話の Safety 検知・フォロー分類の写像・mutation smoke・evals・sakura-max 監査）を移植 |
| [0009](adr/0009-no-unlimited-defaults.md) | 「null = 上限なし」をやめるか | **Proposed** | 日次上限・予算の null を拒否扱いにする案。オーナー判断待ち |
| [0010](adr/0010-dangerous-feature-gates.md) | 危険な機能は設定のゲートで既定 OFF | Accepted | 発信・自動発信・AI 音声・録音は明示的に true にしたときだけ。ゲート OFF は全発信停止と同じ |
| [0011](adr/0011-agent-operating-model.md) | Agent の役割分担とリポジトリを長期記憶にする運用 | Accepted | Claude Code = BUILD、Codex = BREAK / 最終監査。AGENTS.md・CLAUDE.md・AI_WORKFLOW・CRITICAL_INVARIANTS・`test:critical` |
| [0012](adr/0012-database-tenant-context.md) | DB のテナント文脈・組織ロック・マイグレーションの正 | Accepted | トランザクションごとに `SET LOCAL ROLE tac_app`＋`app.org_id`、UnitOfWork は AsyncLocalStorage で共有、`runExclusive` = advisory lock、権限で抑止・監査ログを守る、SQL マイグレーションが正、並行性は実 PG で検証 |
| [0013](adr/0013-auth-and-webhooks.md) | 認証（Cookie セッション）と Webhook の受信・状態の compare-and-set | Accepted | scrypt・HMAC で保存するセッション/CSRF・SECURITY DEFINER 関数でテナント前の照会・受信箱で重複排除・状態は CAS・mock の受け口は local / test だけ |
| [0014](adr/0014-web-app.md) | 画面（apps/web）の構成 | Accepted | Next.js を webpack で（extensionAlias）・rewrites で同一オリジン・業務の判断は packages/workspace・CSRF は sessionStorage・状態はポーリング |
| [0015](adr/0015-twilio-adapter.md) | Twilio アダプタ | Accepted | 会議ブリッジで担当者が先（失敗したらお客様に発信しない）・Twilio に冪等キーが無いので再送しない・確定しない失敗は REQUESTED のまま・状態通知は署名を常に検証（URL は PUBLIC_BASE_URL から）・録音しない |
| [0016](adr/0016-reconcile-uncertain-calls.md) | 確定しない発信の照合 | Accepted | REQUESTED のまま ID の無い通話をプロバイダの通話一覧と突き合わせる・1 件なら ID を付けて進める・15 分見つからなければ FAILED で番号を解放・複数／一覧が引けないなら変えない・別の通話の ID は除く |

## 依頼文の用語との対応
- `TelephonyGateway`（依頼文）＝ `TelephonyProvider`（本実装のポート名、`packages/application/src/ports.ts`）
- `FakeTelephonyProvider`（依頼文）＝ 現在の `MockTelephonyProvider` を Phase 8 でシミュレーターに拡張したもの
