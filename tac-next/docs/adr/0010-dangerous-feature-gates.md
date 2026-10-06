# ADR-0010: 危険な機能は設定のゲートで既定 OFF にする

- Status: Accepted
- Date: 2026-10-06

## Context
`APP_ENV=production` や `NODE_ENV=production` だけを安全装置にすると、設定ミス1つで本番発信・自動発信・AI 音声・録音が始まる。
現行 TAC では「安全装置の既定が OFF」が事故の温床だった（EXISTING_APP_AUDIT A 章）。組織の全発信停止スイッチ（ADR-0006）は実行時の操作で、デプロイ時の意図を表せない。

## Decision
`packages/config` に4つのゲートを置き、**すべて既定 false**：`OUTBOUND_CALLS_ENABLED`・`AUTO_DIAL_ENABLED`・`AI_VOICE_ENABLED`・`RECORDING_ENABLED`。
- 自動発信・AI 音声は `OUTBOUND_CALLS_ENABLED=true` のときだけ有効にできる（それ以外は起動エラー）
- production で発信 ON なのに `TELEPHONY_PROVIDER=mock` は起動エラー（発信したつもりで誰にもかかっていない状態を防ぐ）
- ゲートは発信判定 `evaluateCallPolicy` の**必須の入力**（`CallPolicyFacts.deployment`）にし、`CreateCallUseCase` は `Deps.features` から渡す。組み立て側が包み忘れて効かなくなる、ということが型の上で起きない
- 拒否理由は組織の全発信停止（`OUTBOUND_STOPPED`）と区別する：`OUTBOUND_DISABLED_BY_CONFIG`（先頭）・`AI_VOICE_DISABLED_BY_CONFIG`。停止スイッチを解除しても発信できない原因が設定だと分かる
- `AUTO_DIAL_ENABLED`・`RECORDING_ENABLED` は、使う側（自動発信の worker・録音）がまだ無い。実装するときに同じく必須の入力にする（それまでは CRITICAL_INVARIANTS で UNKNOWN）
- 同じ PR のレビューで、最初の案（`withDeploymentGate` で SafetyControls を包む）は包み忘れに弱く、理由も全発信停止と区別できないと指摘されたため、この形に改めた

## Alternatives
- APP_ENV だけで判定する：設定ミスに弱い（不採用）
- ゲートを DB の組織設定だけに置く：デプロイ単位で止められない。DB 障害時の挙動が曖昧（組織設定は ADR-0006 として併用）

## Consequences
- 本番で発信を始めるには、意図して `OUTBOUND_CALLS_ENABLED=true` を設定する必要がある（Runbook の「最初の一手」で、これを false にすれば全発信が止まる）
- ゲートと既定値は mutation smoke で保護する（既定を true にする・ゲートを消す変異は KILLED）
