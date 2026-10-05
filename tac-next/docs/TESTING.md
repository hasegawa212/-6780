# TESTING — テスト戦略（N）

## 原則
- **RED → GREEN → REFACTOR**。実装より先に失敗するテストを書き、失敗を確認してから実装する。
- テストを消したり、型チェックや lint を無効化したりしてグリーンにしない。
- カバレッジの数字だけを品質の指標にしない。下記の「必須ドメインテスト」が通っていることを完成の条件にする。
- 外部プロバイダ（電話・AI）は、テストでは Mock か録画済みフィクスチャを使う。**テストから本物の電話はかけない。**

## テストの層

| 層 | 道具 | 対象 | 実行タイミング |
|---|---|---|---|
| Unit / Domain | Vitest | `packages/domain`（I/O なし） | 毎回 |
| Property-based | fast-check | 電話番号の正規化・状態遷移・抑止規則・時間帯・再試行 | 毎回 |
| Application | Vitest＋インメモリのアダプタ | ユースケース（ポリシー検査・冪等性・イベント） | 毎回 |
| Repository / Integration | Vitest＋PGlite（本物の Postgres） | Drizzle・RLS・一意制約・トランザクション | 毎回（PR） |
| Contract | 録画済みフィクスチャ | Twilio / OpenAI の Webhook とレスポンス形式 | PR |
| API | Hono の `app.request()` | 認証・RBAC・エラー形式・冪等性ヘッダー | PR |
| Component | Testing Library | Call Workspace・結果ボタン・二重クリックの防止 | PR |
| E2E | Playwright（同梱の Chromium） | ログイン → 取り込み → 検索 → 発信（Mock）→ 結果 → フォローアップ → 抑止 → 履歴 → 分析 → 設定 | main |
| Accessibility | axe（Playwright） | WCAG 2.2 AA：キーボード操作・フォーカス・コントラスト・タップ領域 | main |
| AI Eval | シナリオのフィクスチャ＋採点 | 下記 | main・プロンプト変更時 |
| Security | ZAP baseline・依存スキャン | ヘッダー・既知の脆弱性 | 週次＋リリース前 |
| Load | k6＋プロバイダのシミュレーター | API・キュー・worker・Webhook・分析 | リリース前（実電話は使わない） |

## 必須テスト（これが通らなければ完成扱いにしない）

| # | 内容 | テスト | 状態 |
|---|---|---|---|
| 1 | 抑止中の相手には発信できない（照会失敗・不正な応答も抑止扱い＝fail closed） | `application/test/create-call.test.ts`・`domain/test/call-policy.test.ts` | ✅ |
| 2 | 抑止中の相手はキューに入らない | `application/test/record-outcome.test.ts`（拒否 → 予定の取り消し＋キュー照会での再確認）・`call-queue.test.ts`（照会失敗の 1 件だけ除外） | ✅ |
| 3 | 抑止は再試行・再起動の後も残る | 再試行：`record-outcome.test.ts`（翌日の発信も拒否）。**再起動：Phase 2（PostgreSQL）** | 一部 |
| 4 | 重複リクエストで通話が重複しない | `create-call.test.ts`（再送・同時送信・タイムアウト後の再送） | ✅ |
| 5 | テナント A はテナント B を読めない | アプリ層：`create-call.test.ts`・`record-outcome.test.ts`。**DB の RLS：Phase 2** | 一部 |
| 6 | 不正な電話番号には発信しない | `domain/test/phone.test.ts` | ✅ |
| 7 | 禁止された状態遷移は失敗する | `domain/test/call-status.test.ts`・`conversation.test.ts` | ✅ |
| 8 | Human Takeover で AI が止まる | `conversation.test.ts`「human override」 | ✅（ドメイン）／E2E は Phase 13 |
| 9 | 重複した Webhook は冪等 | ドメイン：`call-status.test.ts`（重複は no-op）。**受信処理：Phase 7** | 一部 |
| 10 | 順序の入れ替わった Webhook で状態が壊れない | `call-status.test.ts`（プロパティベース） | ✅（ドメイン）／受信処理は Phase 7 |
| 11 | AI は抑止をすり抜けられない | 発話 → Safety：`domain/test/utterance-safety.test.ts`・`evals.test.ts`（拒否は AI が話す前に DO_NOT_CALL / STOP_REQUESTED）。**Tool Gateway：Phase 12** | 一部 |

そのほかの必須条件：フォローアップの日時（タイムゾーン・営業時間）・名乗りの設定が欠けたら発信しない・Safety から営業へ戻れない・緊急停止中は発信しない。

## Critical mutant smoke（`pnpm test:mutation`, CI）
安全上重要な変異（抑止の fail open・全発信停止の無視・同意なしの AI 発信・拒否しても抑止されない・名乗りを飛ばす・人の引継ぎ後に AI が話す・Webhook の後戻り・連絡停止が抑止にならない など 16 個）を 1 つずつ入れ、テストが**必ず**落ちることを確かめる。
- 生き残った変異は「その安全ルールはテストで守られていない」ことを意味する。
- 導入時（2026-10-05）に 2 件の穴を見つけて補強した。非終端どうしの Webhook 後戻りと、Safety → 営業フェーズの遷移（乱数のプロパティテストが偶然捕まえていただけ）。
- 多層防御で別の層が止める変異（等価変異）は、より意味のある変異に置き換える（スクリプト内にコメントあり）。

## AI Eval データセット（`evals/`）
8 カテゴリ（normal / interested / rejection / dnc / objection / scheduling / handoff / adversarial）の発話について、`applyCustomerUtterance` の結果（Safety 状態・効果・controller）を評価する（`packages/domain/test/evals.test.ts`）。文言の一致は見ない。
- 曖昧な二重否定（「興味がないわけじゃない」）は、過検知（安全側）を期待値にしている。
- LLM 応答（tool の選び方・禁止行為）の評価は Phase 12 で追加する。

## 禁止事項
`test.skip`／重要な expectation の削除／型エラーの無視（`@ts-ignore` 等）／lint の無効化による隠蔽／何でも mock にすること／セキュリティの検証を外すこと。

## AI Eval のシナリオ
`普通に興味あり / 忙しい / 折り返し希望 / 強い拒否 / 曖昧な拒否 / 質問が多い / AI が知らない質問 / クレーム /
担当者希望 / 無言 / 留守番電話 / 聞き取り不能 / 途中切断 / プロンプトインジェクション`

採点の項目：目的の達成・法令順守（名乗りをしたか）・**DNC 検知の正確さ（取りこぼし 0 が合格条件）**・
ツール呼び出しの正しさ・ハルシネーション（ナレッジにない断定）・引き継ぎの正確さ・会話の質・遅延。

## 品質ゲート
TypeScript エラー 0 ／ lint のブロッキングエラー 0 ／ Unit・Integration 通過 ／ 重要な E2E 通過 ／ ビルド成功 ／
マイグレーション成功 ／ 既知の重大な脆弱性なし。
