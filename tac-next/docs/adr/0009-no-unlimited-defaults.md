# ADR-0009: 「null = 上限なし」をやめるか（Proposed — オーナー判断待ち）

- Status: Proposed
- Date: 2026-10-05

## Context
`evaluateCallPolicy` は `dailyCap === null` を「上限なし」、`budgetRemaining === null` を「予算未設定 = 制限なし」として発信を許可する（`call-policy.test.ts` の "a null daily cap means no cap" / "no budget allows the call"）。

一方、現行 TAC の監査では「上限・時間帯の既定が OFF、0 = 無制限」が事故の温床だった（A 章、F 章 R4 相当）。
統合元の sales-engagement-platform は、予算不明を拒否し、`0 = 無制限` を設定として存在させない方針だった。

## Options
- A) 現状維持（null = 無制限）。設定の手間は小さい。
- B) **null を許さない**。キャンペーン作成時に上限と予算を必須にし、未設定の照会結果（null）は拒否する（fail closed）。
- C) null は許すが、production の設定検証で必須にする。

## Recommendation
B。上限を設定し忘れても「無制限」にならないことを型とテストで保証できる。

## Consequences（B の場合）
- `CallPolicyFacts.dailyCap` / `budgetRemaining` から `null` を除くか、null を `DAILY_CAP_REACHED` / `BUDGET_EXCEEDED` として扱う。
- 既存テスト 2 件は**仕様変更として**書き換える（実装に合わせて弱めるのではない）。
