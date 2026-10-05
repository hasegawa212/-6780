# ADR-0008: 並行していた次世代実装を tac-next に統合する

- Status: Accepted
- Date: 2026-10-05

## Context
同じ目的の次世代実装が 2 つあった。
- `hasegawa212/-6780/tac-next`（本リポジトリ）
- `hasegawa212/-/sales-engagement-platform`

並行して進めると、設計・テスト・運用知識が二重になる。オーナーは tac-next への統合を選んだ（2026-10-05）。

## Decision
tac-next を正とし、sales-engagement-platform のうち tac-next に無かった強みだけを tac-next の語彙・規約で移植する。
1. **抑止の照会を fail closed にする**（`isContactable`）。移植中に、照会結果が `true` 以外の truthy 値なら発信されてしまう穴を見つけ、発信とキューの両方で塞いだ。
2. **発話から Safety を検知する**（`detectSafetySignals` / `applyCustomerUtterance`）。既存の `enterSafety` の優先度と効果に接続した。
3. **現行 TAC のフォロー 5 分類の写像**（`followCategoryFromLabel`）。
4. **Critical mutant smoke**（`pnpm test:mutation`, CI）。導入時に、テストで守られていなかった 2 点を見つけて補強した。
   - 非終端どうしの Webhook 後戻り
   - Safety → 営業フェーズ（それまでは乱数のプロパティテストが偶然捕まえていただけ）
5. **AI 評価データセット `evals/`**（8 カテゴリ、発話 → Safety の決定的な層を評価）。
6. **`feature/sakura-max` の監査**（EXISTING_APP_AUDIT.md の F）。

移植しなかったもの:
- HTTP 骨格（Fastify）。tac-next は Hono（ADR-0002）なので Phase 3 で作る。
- PII redaction。Phase 15 で observability パッケージとして移植する。
- 状態機械・ガード。tac-next 側のほうが網羅的なので、そちらを維持する。

## Alternatives
- sales-engagement-platform に統合する：アプリ層が無く、本番 TAC と別リポジトリになるため却下。
- 両方を続ける：重複投資になるため却下。

## Consequences
- sales-engagement-platform は凍結し、README から tac-next を指す。
- 以後の作業はすべて tac-next で行う。
