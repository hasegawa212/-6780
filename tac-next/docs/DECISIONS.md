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

## 依頼文の用語との対応
- `TelephonyGateway`（依頼文）＝ `TelephonyProvider`（本実装のポート名、`packages/application/src/ports.ts`）
- `FakeTelephonyProvider`（依頼文）＝ 現在の `MockTelephonyProvider` を Phase 8 でシミュレーターに拡張したもの
