# ADR-0001: Modular Monolith + Event-driven boundaries

- Status: Accepted
- Date: 2026-10-04

## Context
現行 TAC は Flask 1プロセス + JSONL ファイル + プロセス内メモリ（ラウンドロビン位置・会話状態）で動く。
単一マシン前提（fly.toml で 1 machine 固定）で、水平スケールすると状態が壊れる。
一方、運用チームは小規模（担当者数名）で、マイクロサービスの運用コストは払えない。

## Decision
TypeScript の pnpm workspace による **Modular Monolith** を採用する。

```
apps/api      HTTP API（Hono）。認証・入力検証・ユースケース呼び出しのみ
apps/worker   キュー消費（発信ジョブ・フォローアップ・Webhook 処理）
apps/web      オペレーター UI（Next.js、Phase 8 で追加）
packages/domain       純粋なドメイン（I/O なし）。電話番号・状態機械・抑止・結果分類・スコア
packages/application  ユースケース + ポート（Repository / TelephonyProvider / Clock / EventBus）
packages/telephony    TelephonyProvider アダプタ（Mock / Twilio / OpenAI SIP）
packages/db           Drizzle スキーマ・マイグレーション・Repository 実装
```

モジュール間はポート（interface）とドメインイベントで結合し、将来プロセス分離できる境界を保つ。
状態はすべて PostgreSQL に置き、プロセス内メモリに業務状態を持たない。

## Alternatives
- マイクロサービス: 境界が固まる前に分けると分散トランザクションと運用負荷が先に来る。却下。
- 現行 Python を拡張: テスト資産はあるが、型安全・マルチテナント・DB 移行を同時にやると実質書き直し。
  現行は「仕様の参照実装」として残し、新規は TypeScript で作る。

## Consequences
- ドメイン層は I/O を持たないので、ユニットテストが速く決定的になる。
- アプリ層のテストはインメモリのアダプタで回し、DB 結合テストは PGlite / PostgreSQL で別に回す。
- 依存方向の違反（domain → db など）は lint とレビューで防ぐ。
