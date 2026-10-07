# CRITICAL_INVARIANTS — 決して破れない不変条件

この表の状態は **証拠のある範囲だけ** を PASS にする。層ごとに分けて書き、まだその層が無いものは UNKNOWN（推測で PASS にしない）。
対応するテストは `pnpm test:critical`（`vitest.critical.config.ts`）に入っており、CI で必須。
`pnpm test:mutation` は、ここに挙げた安全ルールを1つずつ反転させ、テストが必ず落ちることを確かめる（現在 75 件。Critical Suite だけで検出できることを確かめる。実 PostgreSQL の同時実行でしか検出できないもの＝ログイン試行の予約の FOR UPDATE は `test:postgres` が守る）。

最終確認: 2026-10-06（`pnpm check` 440/440・`pnpm test:critical` 355/355・mutation 42/42。`pnpm test:postgres` 9/9（CI、PR #133））

## INV-1 抑止中の相手に、新しい発信は決して生まれない
判定できないとき（照会の失敗・不正な応答・未確定）は発信しない（fail closed）。

| 層 | 状態 | 証拠 |
|---|---|---|
| Domain（発信ガード） | PASS | `domain/test/call-policy.test.ts` |
| Application（発信・キュー・結果） | PASS | `create-call.test.ts`（抑止・照会失敗・不正な応答）／`call-queue.test.ts`／`record-outcome.test.ts`（拒否→予定取り消し） |
| 判定後〜発信前の割り込み（TOCTOU） | PASS | `adversarial.test.ts`「判定の後・発信の前に DNC 登録が入ったら…」 |
| 会話（発話→抑止） | PASS（ルール） | `utterance-safety.test.ts`・`evals.test.ts`（DNC recall 19/19） |
| 画面（発信ボタンの可否） | PASS（表示ロジック＋E2E） | `workspace/test/suppression-banner.test.ts`・`call-view.test.ts`（照会失敗は UNKNOWN で発信させない）／E2E 2（拒否の後はボタンが消え、API に直接送っても 422） |
| DB（再起動後も残る・同時登録・削除できない） | PASS（PGlite） | `db/test/use-cases.test.ts`（閉じて開き直しても拒否・拒否→抑止の永続化・途中失敗でロールバック）／`repositories.test.ts`（二重登録で1件・E.164 以外は保存不可）／`tenant-isolation.test.ts`（アプリのロールは抑止を UPDATE / DELETE できない） |
| API（`POST /v1/calls`・結果） | PASS（PGlite） | `apps/api/test/calls.test.ts`（抑止中は 422・「拒否」の記録の後は 422） |
| 結果の訂正（先に「不在」等を記録した後の「拒否」） | PASS（PGlite、独立 QA IQA-01 の修正） | `apps/api/test/iqa-independent.test.ts`（抑止になり、翌日の発信は 422・外部発信 1 件のまま） |
| 発信直前の再確認と発信の間の窓 | PASS（アプリ層、IQA-04・04b の修正） | `application/test/iqa-independent.test.ts`（監査・イベント配信の最中の DNC・全発信停止・キャンペーンの一時停止で発信しない） |
| 電話番号の表記ゆれ（`+81 (0)90…`） | PASS（ドメイン、IQA-09）／取り込み経路は UNKNOWN（Phase 4） | `domain/test/iqa-phone.test.ts` |
| worker / 再試行ジョブ / AI ツール | UNKNOWN | 未実装 |

## INV-2 テナント A はテナント B のデータにアクセスできない
| 層 | 状態 | 証拠 |
|---|---|---|
| Application | PASS（インメモリ） | `create-call.test.ts`「cannot use another tenant's contact or campaign」・`record-outcome.test.ts` |
| DB（RLS・複合外部キー） | PASS（PGlite・実 PG の接続プール） | `db/test/tenant-isolation.test.ts`（リポジトリ経由・生 SQL 経由・未設定なら0行・WITH CHECK・複合 FK・1トランザクション1組織）／`db/test-postgres/concurrency.test.ts` |
| API・認証（セッション・CSRF・ロール） | PASS（PGlite） | `apps/api/test/auth.test.ts`・`calls.test.ts`（別テナントの ID は 404・本文の organizationId は 400・VIEWER は 403）／`application/test/auth.test.ts`／`db/test/auth-webhooks.test.ts`（認証の表はアプリから読めない） |
| ログイン試行の制限 | PASS（アプリ層・PGlite・API・実 PG16 の同時実行） | `application/test/auth.test.ts`・`db/test/auth-webhooks.test.ts`・`apps/api/test/auth.test.ts`・`test-postgres`。独立 QA（IQA-10）で「同時の試行が上限を超えて照合される」ことが見つかり、照合の前に枠を予約する形に修正（`iqa-independent.test.ts`・`test-postgres/iqa-login-throttle.test.ts`） |
| ユーザーの作成（運用 CLI） | PASS | `apps/api/test/admin.test.ts`・`cli.test.ts` |
| 招待・パスワード再設定 | NOT IMPLEMENTED | Phase 3 の続き |

## INV-3 1つの論理的な発信要求から、外部発信は1件だけ
| 層 | 状態 | 証拠 |
|---|---|---|
| Application | PASS（インメモリ） | `create-call.test.ts`（再送・同時送信・タイムアウト後の再送）／`adversarial.test.ts`（A・B・Worker が別キーで同時発信→1件） |
| 画面（連打・タイムアウト時のキー保持） | PASS（表示ロジック＋E2E） | `workspace/test/call-starter.test.ts`・`call-view.test.ts`（504 は同じキーを保持）／E2E 3（5 連打で POST は 1 件） |
| DB（一意制約・部分一意インデックス） | PASS（PGlite・実 PG の同時実行） | `db/test/repositories.test.ts`（冪等キー・回線上は番号ごとに1件・優先順位）・`use-cases.test.ts`（20 並列で1件）／`test-postgres`（30 並列・別キー 10 並列） |
| 組織の上限（1日上限・同時通話数）を同時要求で超えない | PASS（アプリ層・ロック保持＝PGlite・同時実行での直列化＝実 PG、CI） | `adversarial.test.ts`「Phase 2: 組織単位の上限…」・`use-cases.test.ts`（`pg_locks`）・`test-postgres` |
| API（`Idempotency-Key`） | PASS（PGlite） | `apps/api/test/calls.test.ts`（再送は 200・違う内容は 409・10 並列で 1 件） |
| 組織をまたいだ同じ `Idempotency-Key` | PASS（PGlite、IQA-02 の修正） | `apps/api/test/iqa-independent.test.ts`（プロバイダへのキーは通話 ID。組織 A・B が同じキーでもそれぞれ 1 件） |
| Webhook（重複・順序違い・遅延・同時到着） | PASS（アプリ層・PGlite）／同時到着の CAS は実 PG（CI） | `provider-events.test.ts`・`apps/api/test/webhooks.test.ts`・`db/test/auth-webhooks.test.ts`・`test-postgres` |
| プロバイダが受け付けたのに応答が届かないケース | PARTIAL | タイムアウト・接続断など「発信されたか分からない」失敗は REQUESTED のまま（IQA-03）。確定しない通話は 15 分で同時通話数から外す（IQA-08）。特定できない Webhook は再送で処理し直す（IQA-11）。**プロバイダへの照合（reconcile）は未実装**、実プロバイダは UNKNOWN（Phase 11） |

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
| DB（`system_controls`） | PASS（PGlite） | `use-cases.test.ts`（停止中は発信しない）・`repositories.test.ts`（行が無ければ停止中 = fail closed）・`tenant-isolation.test.ts`（アプリから書き換え不可） |
| 設定のゲート（既定 OFF） | PASS | `config.test.ts`（既定値・依存関係・staging/production で mock のまま発信 ON を拒否）・`call-policy.test.ts`・`create-call.test.ts`（`OUTBOUND_DISABLED_BY_CONFIG`・`AI_VOICE_DISABLED_BY_CONFIG`） |
| 自動発信・録音のゲート | UNKNOWN | 設定は検証済みだが、使う側（自動発信の worker・録音）が未実装。実装時に `features.autoDial` / `features.recording` を必須入力にする |
| 時間外は発信しない | PASS | `calling-window.test.ts`（境界・夏時間・不正なタイムゾーンは時間外）・`create-call.test.ts` |
| API | PASS（PGlite） | `apps/api/test/calls.test.ts`（全発信停止・設定のゲート OFF は 422） |
| worker / 再試行 / スケジュール / AI ツール | UNKNOWN | 未実装 |

## 不変条件を追加・変更するとき
1. この文書に行を追加し、層ごとの状態を書く
2. 対応するテストを `vitest.critical.config.ts` に入れる
3. 守っているコード行の反転を `scripts/mutation-smoke.mjs` に追加し、KILLED になることを確認する
4. `AGENTS.md` の Critical Invariants と `PROGRESS.md` の要約を合わせる
