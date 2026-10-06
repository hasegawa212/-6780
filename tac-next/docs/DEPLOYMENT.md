# DEPLOYMENT — デプロイ

**状態：NOT IMPLEMENTED**（Phase 18。本番操作はすべて人の承認が必要）。構成案は [`ARCHITECTURE.md` §G-3](ARCHITECTURE.md)（Fly.io・東京）。

## 環境
| 環境 | 電話 | データ | 備考 |
|---|---|---|---|
| local / test | `TELEPHONY_PROVIDER=mock` のみ（それ以外は起動しない） | 架空 | 実電話は物理的にかからない |
| staging | Sandbox → 承認済みのテスト番号だけ | 架空 | 本番と同じ手順のリハーサル |
| production | 本物（明示的に `OUTBOUND_CALLS_ENABLED=true`） | 本番 | 段階的に開放 |

危険な機能は既定 OFF：`OUTBOUND_CALLS_ENABLED`・`AUTO_DIAL_ENABLED`・`AI_VOICE_ENABLED`・`RECORDING_ENABLED`（`packages/config`）。`APP_ENV=production` だけでは発信は始まらない。

## リリースの手順（予定）
staging：デプロイ → マイグレーション → 架空データ → スモーク → 重要な E2E → AI Eval → 障害試験 → ロールバックの確認
本番：社内 → 承認済みテスト → 小さなパイロット → 限定本番 → 監視 → 段階的に拡大。
直前チェックリストは [`agents/PRODUCTION_READINESS_AUDIT.md`](agents/PRODUCTION_READINESS_AUDIT.md) の「リリース直前の質問」。
