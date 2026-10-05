# ADR-0007: 起動時に検証する設定（安全側の既定値）

- Status: Accepted
- Date: 2026-10-04

## Context
現行 TAC の設定は、import の時点で環境変数を読む。不正な数値があると起動時にクラッシュし、
安全装置（署名検証・発信時間帯・1日上限・名乗り）は既定で OFF になっている（監査の問題 #10）。

## Decision
`packages/config` に Zod のスキーマを置き、アプリの起動時に一度だけ検証する。
- 不正な値は「どの項目がなぜ不正か」を列挙して起動を止める。
- 安全装置は **既定で ON**（Webhook の署名検証・発信時間帯）。OFF にするには明示的な設定が必要。
- `local / test` では電話プロバイダは `mock` だけ。`staging / production` では `DATABASE_URL` と `SESSION_SECRET`（32 文字以上）が必須。
- シークレットは文字列化・JSON 化しても `[REDACTED]` になる（ログや例外に出さない）。

## Alternatives
- 各モジュールが個別に `process.env` を読む：検証漏れと既定値のばらつきが出る。却下。

## Consequences
- アプリのコードは `process.env` を直接読まず、検証済みの設定オブジェクトを受け取る（テストで差し替えやすい）。
