# CRITICAL_INVARIANTS — 決して破れない不変条件

この表の状態は **証拠のある範囲だけ** を PASS にする。層ごとに分けて書き、まだその層が無いものは UNKNOWN（推測で PASS にしない）。
対応するテストは `pnpm test:critical`（`vitest.critical.config.ts`）に入っており、CI で必須。
`pnpm test:mutation` は、ここに挙げた安全ルールを1つずつ反転させ、テストが必ず落ちることを確かめる（現在 33/33。Critical Suite だけで検出できることを確かめる）。

最終確認: 2026-10-06（`pnpm check` 386/386・`pnpm test:critical` 301/301・mutation 33/33）

## INV-1 抑止中の相手に、新しい発信は決して生まれない
判定できないとき（照会の失敗・不正な応答・未確定）は発信しない（fail closed）。

| 層 | 状態 | 証拠 |
|---|---|---|
| Domain（発信ガード） | PASS | `domain/test/call-policy.test.ts` |
| Application（発信・キュー・結果） | PASS | `create-call.test.ts`（抑止・照会失敗・不正な応答）／`call-queue.test.ts`／`record-outcome.test.ts`（拒否→予定取り消し） |
| 判定後〜発信前の割り込み（TOCTOU） | PASS | `adversarial.test.ts`「判定の後・発信の前に DNC 登録が入ったら…」 |
| 会話（発話→抑止） | PASS（ルール） | `utterance-safety.test.ts`・`evals.test.ts`（DNC recall 19/19） |
| 画面（発信ボタンの可否） | PASS（表示のみ） | `workspace/test/suppression-banner.test.ts`（保存失敗・未確定でも発信させない） |
| DB（再起動後も残る・同時登録） | UNKNOWN | Phase 2 未着手 |
| API / worker / 再試行ジョブ / AI ツール | UNKNOWN | 未実装 |

## INV-2 テナント A はテナント B のデータにアクセスできない
| 層 | 状態 | 証拠 |
|---|---|---|
| Application | PASS（インメモリ） | `create-call.test.ts`「cannot use another tenant's contact or campaign」・`record-outcome.test.ts` |
| DB（RLS）・API・認証 | UNKNOWN | Phase 2・3 未着手 |

## INV-3 1つの論理的な発信要求から、外部発信は1件だけ
| 層 | 状態 | 証拠 |
|---|---|---|
| Application | PASS（インメモリ） | `create-call.test.ts`（再送・同時送信・タイムアウト後の再送）／`adversarial.test.ts`（A・B・Worker が別キーで同時発信→1件） |
| 画面（連打・タイムアウト時のキー保持） | PASS（表示のみ） | `workspace/test/call-starter.test.ts` |
| DB（一意制約・部分一意インデックス） | UNKNOWN | Phase 2 未着手 |
| プロバイダが受け付けたのに応答が届かないケース | PARTIAL | アプリ層は REQUESTED のまま再送で二重にしない。実プロバイダでは UNKNOWN（Phase 11） |

## INV-4 人が引き継いだら、AI は話すこともツールを実行することもやめる
| 層 | 状態 | 証拠 |
|---|---|---|
| Domain | PASS | `conversation.test.ts`（human override・`canAiSpeak`） |
| 画面の表示 | PASS（表示のみ） | `call-indicator.test.ts`（引き継ぎ後に「AI が話しています」と出さない） |
| 音声ゲートウェイ・Tool Gateway・E2E | UNKNOWN | Phase 12・13 未着手 |

## INV-5 信頼できない内容がシステムの方針を上書きしない
| 層 | 状態 | 証拠 |
|---|---|---|
| 発話の安全検知（ルール） | PASS（ルール） | `evals.test.ts` の adversarial ケース（指示無視・DNC 削除要求 等） |
| LLM・CRM メモ・ナレッジ・ツール出力 | UNKNOWN | AI 未実装（Phase 12） |

## INV-6 全発信停止・設定の発信ゲートが有効なら、どの経路からも発信できない
| 層 | 状態 | 証拠 |
|---|---|---|
| Application（発信・判定後の割り込み） | PASS | `create-call.test.ts`「STOP ALL OUTBOUND…」・`adversarial.test.ts` |
| 設定のゲート（既定 OFF） | PASS | `config.test.ts`（既定値・依存関係・staging/production で mock のまま発信 ON を拒否）・`call-policy.test.ts`・`create-call.test.ts`（`OUTBOUND_DISABLED_BY_CONFIG`・`AI_VOICE_DISABLED_BY_CONFIG`） |
| 自動発信・録音のゲート | UNKNOWN | 設定は検証済みだが、使う側（自動発信の worker・録音）が未実装。実装時に `features.autoDial` / `features.recording` を必須入力にする |
| 時間外は発信しない | PASS | `calling-window.test.ts`（境界・夏時間・不正なタイムゾーンは時間外）・`create-call.test.ts` |
| API / worker / 再試行 / スケジュール / AI ツール | UNKNOWN | 未実装 |

## 不変条件を追加・変更するとき
1. この文書に行を追加し、層ごとの状態を書く
2. 対応するテストを `vitest.critical.config.ts` に入れる
3. 守っているコード行の反転を `scripts/mutation-smoke.mjs` に追加し、KILLED になることを確認する
4. `AGENTS.md` の Critical Invariants と `PROGRESS.md` の要約を合わせる
