# 独立 QA / Red-team 監査レポート — tac-next（hasegawa212/-6780 main @ 9b9902f）

- 監査日: 2026-10-06
- 監査者: 独立 QA（BREAK 担当。実装には関与していない）
- 対象: `hasegawa212/-6780` main（9b9902f）の複製（detached worktree）。監査者は push・commit をしていない
- 電話: MockTelephonyProvider / RecordingTelephony / テスト内の Fake だけを使用。番号は架空（`+8190000000xx` 等）のみ
- 証拠の区分: OBSERVED（実行して確認）/ INFERRED（コードから推論）/ UNKNOWN（未検証）
- `docs/QA_REPORT.md` は自分の監査が終わった後に突き合わせた（末尾）

---

## 1. Baseline

| コマンド | 結果（OBSERVED） |
|---|---|
| `pnpm check`（lint + typecheck + 全テスト） | PASS。Biome 142 files OK、`tsc -b` OK、**40 files / 615 tests passed** |
| `pnpm test:critical` | PASS。**30 files / 517 tests passed** |
| `pnpm test:postgres` | PASS。ローカルに使い捨ての PostgreSQL 16 クラスタを立てて実行（`TEST_DATABASE_URL=postgres://tac@127.0.0.1:55432/tacqa`）→ **16/16 passed**。監査後にクラスタは停止・削除済み。警告: `pg` の「実行中のクライアントへの client.query() は非推奨（pg@9 で削除）」が出る |
| `pnpm test:mutation` | **未実行**（ソースを一時的に書き換えるため・時間の都合） |
| `pnpm test:e2e` / `pnpm build` | 未実行（今回の焦点は API / DB / application / telephony） |

監査用の回帰テスト追加後（OBSERVED）: `pnpm test` → **17 failed / 615 passed（632）**。既存の 615 件はすべて PASS のまま、失敗 17 件はすべて今回追加した IQA テスト。`pnpm test:critical` は既存の一覧だけなので 517/517 PASS のまま（IQA テストは Critical Suite に入れていない）。

---

## 2. Findings

| ID | Sev | Component | 概要 | Evidence |
|---|---|---|---|---|
| IQA-01 | **HIGH** | `packages/application/src/record-outcome.ts:58-59, 166-172`／`apps/api/src/app.ts:395-427` | 先に別の結果（例「不在」）を記録した通話には、後から「拒否（DNC）」を記録できない（409）。API には抑止を登録する経路が他になく、**翌日、拒否した顧客へ新しい外部発信が行われる** | CONFIRMED |
| IQA-02 | **HIGH** | `packages/application/src/create-call.ts:145-151`／`packages/telephony/src/index.ts:105-108`／`packages/db/src/repositories.ts:302-321`＋制約 `calls_provider_call_id_uq` | クライアントの `Idempotency-Key` を組織で区別せずにそのままプロバイダへ渡す。**組織 A と B が同じキーを使うと、B の発信は A の通話として扱われ**（B には掛からない）、500、B の通話は REQUESTED のまま残りその番号を塞ぐ | CONFIRMED |
| IQA-03 | MEDIUM | `create-call.ts:152-161` | `ProviderTimeoutError` 以外の例外（接続リセット等、受け付け済みかもしれない失敗）を「確定した失敗」とみなし FAILED にする。担当者が押し直すと**同じ相手へ 2 件目の外部発信**。1 件目の Webhook は終端扱いで捨てられる | CONFIRMED（アプリ層） |
| IQA-04 | MEDIUM | `create-call.ts:114-145` | 直前の再確認（抑止・全発信停止）の後に、監査ログの書き込みとイベント配信の `await` が残っている。その間に入った DNC 登録・全発信停止を無視して発信する（前回 QA で「Phase 2 で塞ぐ」とされた残りの TOCTOU が未解消） | CONFIRMED（アプリ層） |
| IQA-04b | LOW | `create-call.ts:114-121` | 直前の再確認はキャンペーン・組織の一時停止を見ない | CONFIRMED（アプリ層） |
| IQA-05 | LOW | `migrations/0003_auth_and_webhooks.sql:181-`（`locate_provider_call` の第2分岐）／`provider-events.ts:56-63` | Webhook の `providerCallId` が記録済みの値と違っても、`callId` だけで通話を特定して状態を進める（別回線・別通話のイベントで終話にできる） | CONFIRMED |
| IQA-06 | LOW | `apps/api/src/app.ts:141`（`cursorSchema`） | NUL 文字を含むカーソルが検証を通り、DB エラー → 500（応答は汎用で漏えいはなし） | CONFIRMED |
| IQA-07 | MEDIUM | `apps/api/src/app.ts:228` | 想定外の DB 失敗時に `console.error(e)` が Drizzle の `Failed query … params:` をそのまま出力し、**完全な E.164 番号**（と冪等キー・担当者 ID）がサーバーログに残る | CONFIRMED |
| IQA-08 | MEDIUM | `create-call.ts:153-157`／`repositories.ts:52, 351` | プロバイダのタイムアウト後、Webhook が来ないと通話が REQUESTED のまま残り、同時通話数の枠と番号を**永久に**占有する（照合・期限切れの仕組みがない）。タイムアウトが枠の数だけ起きると組織全体が発信不能 | CONFIRMED（アプリ層） |
| IQA-09 | MEDIUM（潜在） | `packages/domain/src/phone.ts:24-32`／`0001_core_schema.sql` の CHECK | `toE164` が国番号の後ろの国内プレフィックス 0 を落とさない（`+81 (0)90-…` → `+8109…`）。DB の CHECK も通るので、同じ番号が 2 通りの E.164 になり、**片方で登録した DNC がもう片方に効かない**。現時点で本番経路から `toE164` は呼ばれていない（取り込み Phase 4 で顕在化） | CONFIRMED（ドメイン） |
| IQA-10 | MEDIUM | `packages/application/src/auth.ts:145-163` | ログイン試行の制限が「ロック確認 → 照合 → 失敗の記録」の check-then-act。同時に送れば上限 5 回を超えて照合される（20 並列で 20 回、実 PG で 30 並列で 30 回） | CONFIRMED（PGlite＋実 PG16） |
| IQA-11 | LOW | `provider-events.ts:44-46`／`0003` `webhook_complete` | 通話を特定できなかった（UNKNOWN_CALL）イベントも処理済みにするため、`providerCallId` の記録より先に届いた（`callId` なしの）イベントは再送されても二度と反映されない | CONFIRMED（アプリ層） |

### 詳細

#### IQA-01（HIGH）結果の先着で DNC が記録できず、拒否した顧客へ再発信される
- **Description**: `RecordOutcomeUseCase` は既存の結果があれば `replayOutcome` を返し、別の結果は `OUTCOME_ALREADY_RECORDED`（409）。抑止の登録はこのユースケースの中にしかなく、API にも抑止を登録する別のエンドポイントがない。
- **Impact**: 担当者の誤操作（「不在」→ すぐ「拒否」に直す）、2 人の担当者の同時操作、画面の再送のどれでも、顧客の明確な拒否が永久に記録されない。`NO_ANSWER` の RETRY フォローアップも OPEN のまま残る。INV-1 違反・再勧誘禁止（宅建業法施行規則 16 条の 11 等、〔要法務確認〕）の法的リスク。
- **Reproduction**: `apps/api/test/iqa-independent.test.ts` › 「IQA-01 … 「不在」を記録した通話でも、顧客の拒否は抑止として登録でき、以後の発信は 422」
  `npx vitest run apps/api/test/iqa-independent.test.ts -t IQA-01`
  観測: `AssertionError: expected 409 not to be 409`。アサーションを外して実際の挙動を観測すると `DNC 409 OUTCOME_ALREADY_RECORDED` → `GET /v1/contacts/{id}` は `"suppression":"NONE"` → 翌日 `POST /v1/calls` は **201 DIALING・`placed=2`**（新しい外部発信）。
- **Root cause**: 「結果は書き直さない」（不変）と「抑止は必ず登録できる」の 2 つの要求を同じ操作に載せている。抑止を上書き不能な結果の副作用としてしか作れない。
- **Recommended fix**: 抑止の登録を結果から独立した、常に受け付ける操作にする（例 `POST /v1/contacts/{id}/suppression`、または結果が既にあっても `DO_NOT_CALL` / `WRONG_NUMBER` だけは「抑止を追加する訂正」として受け付け、監査に残す）。抑止の追加は冪等・追記のみなので、先着の結果に関係なく必ず成功させる。RETRY 等の OPEN な予定も取り消す。

#### IQA-02（HIGH）プロバイダの冪等キーがテナントをまたいで衝突する
- **Description**: `CreateCallUseCase` は `telephony.createCall({ idempotencyKey: key, … })` にクライアントのキーをそのまま渡す。冪等キーの一意性は DB では `(organization_id, idempotency_key)` だが、プロバイダ（1 つのサーバー＝1 インスタンスを全組織で共有）側では組織の区別がない。
- **Impact**: 組織 B の発信要求が組織 A の既存の通話として扱われ、B の相手には掛からない。B の要求には A の `providerCallId` が返り、`attachProvider` が一意制約違反で 500。B の通話行は REQUESTED のまま残り、その番号は「通話中」扱いで塞がれ、同時通話数の枠も占有する（IQA-08 と同じ）。実プロバイダでも「冪等キーをそのまま渡す」契約なら同じことが起きうる（INFERRED、実アダプタは未実装）。決定的なキー（リード ID・試行回数から作るキー）を使うクライアントでは現実的に起きる。
- **Reproduction**: `apps/api/test/iqa-independent.test.ts` › 「IQA-02 … 組織 A と組織 B が同じ Idempotency-Key を使っても…」
  `npx vitest run apps/api/test/iqa-independent.test.ts -t IQA-02`
  観測: `AssertionError: expected 500 to be 201`。応答は `{"error":{"code":"INTERNAL",…}}`、`placed=1`、DB は A=`DIALING`（`MOCK-…-1`）、B=`REQUESTED`・`provider_call_id=null`。
- **Root cause**: プロバイダへ渡す冪等キーの名前空間がクライアント入力のまま（組織・通話で一意化していない）。
- **Recommended fix**: プロバイダへ渡す冪等キーを `call.id`（REQUESTED 保存時に確定し、同じ論理要求の再送では replay されるので一意）にする。少なくとも `${organizationId}:${key}` にする。`attachProvider` の一意制約違反は 500 ではなく「プロバイダの取り違え」として検知・監査する。

#### IQA-03（MEDIUM）受け付け済みかもしれない失敗を確定失敗とみなし、二重発信できる
- **Reproduction**: `packages/application/test/iqa-independent.test.ts` › 「IQA-03 … 同じ相手への 2 件目の外部発信を生まない」
  `npx vitest run packages/application/test/iqa-independent.test.ts -t IQA-03`
  観測: `AssertionError: expected { ok: true, value: {…} } to match object { ok: false }`（2 件目の要求で外部発信が行われた）。
- **Impact**: 1 件目は実際には鳴っている（プロバイダは受け付けた）のに FAILED で終端化され、番号の「回線上は 1 件」の部分一意インデックスから外れる。担当者が押し直す（画面は新しいキー）と、同じ相手に 2 本の回線。後から届く 1 件目の RINGING は終端のため STALE で捨てられる。`perNumberDailyLimit` が 1 なら 2 件目は止まるが、2 以上なら止まらない。
- **Root cause**: `ProviderTimeoutError` 以外は全て「拒否が確定」と分類している。
- **Recommended fix**: プロバイダの「明確な拒否」（4xx 相当の型付きエラー）だけを FAILED にし、それ以外（ネットワーク・5xx・不明）は曖昧扱いで REQUESTED/UNKNOWN に残して照合（`getCall`）で確定させる。

#### IQA-04 / IQA-04b（MEDIUM / LOW）直前の再確認と発信の間に I/O が残っている
- **Reproduction**: `packages/application/test/iqa-independent.test.ts` › 「IQA-04 … CallRequested の配信中に DNC が登録されたら、外部発信しない」「… 監査ログの書き込み中に全発信停止が入ったら、外部発信しない」「IQA-04b … キャンペーンを一時停止したら、外部発信しない」
  `npx vitest run packages/application/test/iqa-independent.test.ts -t IQA-04`
  観測: 3 件とも `expected [ { idempotencyKey: 'key-1', … } ] to have a length of +0 but got 1`。
- **Root cause**: `create-call.ts` で再確認（114-129）の後に `audit.append`（130）と `publish`（138）を await してから `telephony.createCall`（145）。`RecordOutcome` の抑止登録は組織ロックも番号ロックも取らないので、DB 層でも直列化されない（INFERRED）。
- **Recommended fix**: 監査・イベントを再確認の前へ移す（または発信後にまとめる）か、抑止の追加と発信直前の確認を同じ番号単位のロック（advisory lock）で直列化する。再確認に組織・キャンペーンの一時停止も含める。

#### IQA-05（LOW）`providerCallId` が食い違う Webhook でも状態が動く
- **Reproduction**: `apps/api/test/iqa-independent.test.ts` › 「IQA-05 … 記録済みの providerCallId と違う ID の Webhook は、その通話の状態を変えない」
  観測: `expected { result: 'APPLIED' } to deeply equal { result: 'UNKNOWN_CALL' }`（DIALING の通話が ENDED になった）。
- **Root cause**: `locate_provider_call` の第2分岐が `c.provider is null or c.provider = p_provider` しか見ず、記録済みの `provider_call_id` と照合しない。`attachProvider` は既存の ID を上書きしないので食い違いが黙って通る。
- **Recommended fix**: 通話に `provider_call_id` が記録済みで、イベントの `providerCallId` と違うときは UNKNOWN_CALL（または不整合として隔離・監査）にする。
- 注: 署名鍵を持つ者（＝プロバイダ）にしかできないので LOW。

#### IQA-06（LOW）NUL を含むカーソルで 500
- **Reproduction**: `apps/api/test/iqa-independent.test.ts` › 「IQA-06 … NUL を含むカーソルは 400」→ `expected 500 to be 400`。
- **Recommended fix**: `cursorSchema` の `n` に `\u0000` を拒否する refine を付ける（入力の文字列全般も同様）。

#### IQA-07（MEDIUM）想定外の DB 失敗で完全な電話番号がログに出る
- **Reproduction**: `apps/api/test/iqa-independent.test.ts` › 「IQA-07 … console.error に E.164 の完全な番号を出さない」（テストで calls の INSERT にトリガーで失敗を注入し、DB 障害を模擬）
  観測: ログに `[…] unhandled error Failed query: insert into "calls" … params: …,+819000000002,+81300000000,HUMAN_DIALED,REQUESTED,k-fail,…,佐藤,…` が出る。
- **Impact**: DB 障害・タイムアウト・直列化失敗のたびに PII（番号・担当者名）がログ基盤へ流れる。`SECURITY.md` の「ログでは電話番号を `+8190****5678` にする」は未チェック項目で、未実装であることがテストで確定。
- **Recommended fix**: `onError` では `e.name`・PG のエラーコード・制約名だけをログに出し、`DrizzleQueryError.params`/`message` は出さない（またはログの共通 redactor で E.164 をマスク）。

#### IQA-08（MEDIUM）タイムアウト後の REQUESTED が枠を永久に占有
- **Reproduction**: `packages/application/test/iqa-independent.test.ts` › 「IQA-08 …」→ 1 時間後に別の相手へ発信して `{"ok":false,"error":{"code":"CONCURRENCY_LIMIT_REACHED"}}`。
- **Recommended fix**: タイムアウトした通話を `getCall`／idempotency 照会で照合するジョブ、または一定時間で UNKNOWN（枠を解放しつつ同じ番号の再発信は人の確認を要する）へ移す仕組み。

#### IQA-09（MEDIUM・潜在）`+81 (0)…` 表記で 2 通りの E.164
- **Reproduction**: `packages/domain/test/iqa-phone.test.ts` › 「IQA-09 …」5 ケース（`+81 (0)90-…`、`+81 090-…`、`+81-090-…`、`0081 090 …`、全角）
  `npx vitest run packages/domain/test/iqa-phone.test.ts`
  観測: `expected { ok: true, value: '+8109000000001' } to deeply equal { ok: true, value: '+819000000001' }`（5/5 失敗）。
- **Impact**: 抑止は E.164 の完全一致（`PgSuppression.canContact`）。取り込み（Phase 4）が `toE164` を使うと、`090-…` で登録された DNC が `+81(0)90-…` で取り込まれた連絡先に効かない（現行 TAC の QA-TAC-02 と同種）。DB の CHECK `^\+[1-9][0-9]{6,14}$` も `+8109…` を通す。
- **Recommended fix**: 国番号の直後の trunk prefix `0`（`(0)` を含む）を除去する。可能なら libphonenumber 相当で国ごとに検証。DB の CHECK に `+810` を拒否する条件を追加。

#### IQA-10（MEDIUM）ログイン試行の制限を同時送信で超えられる
- **Reproduction**:
  - `apps/api/test/iqa-independent.test.ts` › 「IQA-10 … 20 件の誤ったパスワードを同時に送っても、照合（401）は上限の 5 件まで」→ `expected 20 to be less than or equal to 5`
  - `packages/db/test-postgres/iqa-login-throttle.test.ts` › 「IQA-10（実 PG）…」（`TEST_DATABASE_URL=… npx vitest run --config vitest.postgres.config.ts packages/db/test-postgres/iqa-login-throttle.test.ts`）→ `expected 30 to be less than or equal to 5`
- **Impact**: 1 回のバーストで任意回数のパスワード照合ができる（上限はサーバーの処理能力だけ）。IP 単位の 50 回も同様。15 分ごとに繰り返せる。
- **Root cause**: `lockedUntil` の確認（145）→ scrypt 照合（163）→ `recordFailure`（153）が非原子的。
- **Recommended fix**: 照合の前に試行を原子的に予約する（`auth_throttle_attempt(key)` で回数を先に増やし、上限超過なら照合しない。成功時に戻す/リセット）。

#### IQA-11（LOW）UNKNOWN_CALL を処理済みにしてしまう
- **Reproduction**: `packages/application/test/iqa-independent.test.ts` › 「IQA-11 …」→ `expected 'DUPLICATE' to be 'APPLIED'`。
- **Impact**: 実プロバイダで、発信の応答（ID の記録）より先に `callId` を持たないイベントが届くと、その状態（終話など）は失われ、通話が回線上のまま残る（IQA-08 と同じ影響）。mock は常に `callId` を付けるので現状は顕在化しない。
- **Recommended fix**: UNKNOWN_CALL は `complete` せず保留（後で再照合）にするか、受信箱に UNMATCHED として残し、ID の記録時に再処理する。

### INFO（バグとはしない観察。回帰テストなし）
- `APP_ENV` の既定が `local`。本番で付け忘れると mock・PGlite・自動マイグレーション・mock の Webhook 受け口・非 Secure Cookie の構成になる（実プロバイダ指定なら起動しないので誤発信はしない）。fail-safe ではあるが、デプロイ設定で必須にすることを推奨。
- `TRUSTED_CLIENT_IP_HEADER` に `x-forwarded-for` も指定でき、値全体をキーにする。クライアントが値の先頭を制御できるヘッダーを指定すると IP 単位の制限が無効になる（文書には「プロキシが必ず上書きするものだけ」と注意書きあり）。
- 同じ組織の別担当者が同じ冪等キーを送ると、他人の通話が replay で返る（同じ組織内なので漏えいではない）。
- `pg` のドライバで、1 つのトランザクション（1 クライアント）に `Promise.all` で同時にクエリを流しており、pg@9 で動かなくなる旨の警告が出る（`test:postgres` で OBSERVED）。

---

## 3. 確認して安全と判断した領域（False Positive を含む）

| 観点 | 証拠（OBSERVED） |
|---|---|
| 抑止の照会失敗・不正な応答 → 発信しない（fail closed） | `isContactable`（`suppression-check.ts`）のコード、既存 `create-call.test.ts`・`tenant-isolation.test.ts`「UUID でない組織 ID…」PASS |
| 全発信停止の行が無い・読めない → 停止扱い | `PgSafetyControls`（`row?.stopped !== false`）、`admit` 内の例外は 500 で発信なし、既存テスト PASS |
| `OUTBOUND_CALLS_ENABLED=false`・AI 音声ゲート | `evaluateCallPolicy` の先頭で拒否。発信経路は `CreateCallUseCase` のみ（`grep` で `telephony.createCall` の呼び出しは 1 箇所）。既存 `calls.test.ts` PASS |
| 同じキーの並列・同じキー違う内容（409）・タイムアウト後の同じキーの再送 | 既存 `calls.test.ts`・`adversarial.test.ts`（2/10/100 並列）PASS、実 PG16 で `test:postgres` 16/16 PASS |
| 別キー・同じ番号の同時発信 | 部分一意インデックス `calls_one_active_per_number_uq`、実 PG で PASS |
| Webhook の未署名・不正署名・期限切れ（±5 分）・再送（DUPLICATE）・順序逆転・終端からの後退 | 既存 `webhooks.test.ts`（147・158・166・103・122・132・138 行のテスト）PASS、`reconcileProviderStatus` は終端を動かさない。プローブで未署名は 401 |
| Webhook の別プロバイダの通話 | `locate_provider_call` がプロバイダの一致を要求。既存 `auth-webhooks.test.ts:202` PASS |
| 本番での mock の Webhook 受け口 | `mockWebhooksEnabled` は local/test かつ mock のときだけ。`server.test.ts:126`「production では mock の Webhook の受け口を開かない」PASS |
| テナント分離（ID の書き換え・本文の organizationId・RLS・複合 FK・WITH CHECK・未設定は 0 行） | 全クエリに `organization_id` の WHERE＋RLS。既存 `tenant-isolation.test.ts`・`calls.test.ts`（別テナントは 404、本文の organizationId は 400）PASS |
| SECURITY DEFINER 関数 | 全関数に `set search_path = public, pg_temp`、`public` から EXECUTE を剥奪、所有者 `tac_definer`（NOLOGIN）。返す列は目的に必要なものだけ。テナントをまたぐのは `locate_provider_call`（Webhook 用、設計どおり）と認証系のみ |
| ロール | プローブ: VIEWER の発信・結果・通話参照はすべて 403 |
| CSRF | プローブ: 別セッションのトークンは 403、`text/plain` は 415、`X-HTTP-Method-Override` は効かず 403 |
| セッション | 12 時間で失効（IQA-01 の調査中に 401 で OBSERVED）、ログアウトで失効・ロールは毎回引き直し（既存テスト PASS）。ログイン時に新しいトークンを発行（固定化なし） |
| ユーザー列挙 | 存在しない/パスワード違いは同じ 401・ダミー照合で時間も揃える（既存 `auth.test.ts:59` PASS、コード確認） |
| 入力検証・エラー漏えい | プローブ: 70KB 本文 413、壊れた JSON 400、孤立サロゲート・NUL 入りメール 400、300 文字の冪等キー 400、空白だけのキー 400、UUID でない ID は 404、`limit=abc` 400、壊れたカーソル 400。エラー本文にスタックトレース・SQL なし（IQA-06 の 500 も本文は汎用） |
| Kill switch の判定後の割り込み（判定〜再確認の間） | 既存 `adversarial.test.ts` PASS（再確認の後の窓は IQA-04） |

## 4. 未検証の領域（UNKNOWN）
- 実プロバイダ（Twilio / SIP）のアダプタ・実 Webhook の署名：未実装のため検証不能。IQA-02/03/08/11 は実アダプタの契約次第で影響が変わる
- worker・再試行ジョブ・自動発信・AI Tool Gateway・音声（INV-4 / INV-5）：未実装
- `pnpm test:mutation`・`pnpm test:e2e`・`pnpm build`：未実行
- IQA-04 の窓が実 PostgreSQL の同時実行で実際に起きる頻度：アプリ層で決定的に再現しただけ（DB 層で直列化されないことはコードからの INFERRED）
- 依存の脆弱性・Git 履歴のシークレット・コンテナ設定：今回は対象外
- 本番 DB の接続ユーザー・権限設計（`DATABASE.md`「運用」が未設計）

## 5. 追加した回帰テスト（すべて RED、本番コードは無変更）
- `tac-next/apps/api/test/iqa-independent.test.ts`（IQA-01・02・05・06・07・10）
- `tac-next/packages/application/test/iqa-independent.test.ts`（IQA-03・04・04b・08・11）
- `tac-next/packages/domain/test/iqa-phone.test.ts`（IQA-09、5 ケース）
- `tac-next/packages/db/test-postgres/iqa-login-throttle.test.ts`（IQA-10 の実 PG 版、`TEST_DATABASE_URL` が必要）

実行: `pnpm test`（監査時点のコード） → 17 failed / 615 passed。Biome と `tsc -b` は追加ファイルを含めて OK。

## 6. 過去の QA（`docs/QA_REPORT.md`）との突き合わせ
- 重複する指摘はない。IQA-04 は前回 §15「再確認から外部発信までの残り TOCTOU は Phase 2 で塞ぐ」とされたものが**未解消**であることを確認した
- IQA-09 は現行 TAC の QA-TAC-02（表記ゆれで DNC がすり抜ける）と同じ種類の問題が tac-next の正規化に残っているもの

## 7. 最終判定: **NO-GO**
理由:
1. **IQA-01（HIGH）**: 通常の画面操作（誤操作・同時操作）で、顧客の明確な拒否を記録できなくなり、その顧客へ新しい外部発信が生まれる（INV-1 違反、法的リスク）。訂正する経路が API に存在しない
2. **IQA-02（HIGH）**: プロバイダの冪等キーがテナントをまたいで衝突し、別組織の発信が混ざる・発信が黙って行われない・番号が塞がれる（INV-2 / INV-3）
3. INV-3 の「受け付けたが応答なし」（IQA-03・08・11）が、実プロバイダを入れる前の段階で設計上の穴として残っている
4. 既存の Production Blockers（実プロバイダ・worker・AI・本番 DB 運用が未実装）も解消していない

GO WITH CONDITIONS に進む条件（最低限）: IQA-01・02 の修正とこのテストの GREEN、IQA-03・04・07・10 の修正、IQA-08/11 の照合の設計（ADR）、`test:mutation` に新しい安全ルールの反転を追加。

---

## 付録：修正の記録（開発側、ブランチ `claude/iqa-fixes`）

監査者の再現テスト（17 件、すべて RED）をそのまま取り込み、RED を確認してから最小の修正で GREEN にした。
再現テストは `vitest.critical.config.ts` に入れて CI の必須ゲートにした。修正ごとに安全ルールの反転を `scripts/mutation-smoke.mjs` に追加した。

| ID | 修正 | 場所 |
|---|---|---|
| IQA-01 | 先に別の結果を記録した通話でも、「拒否」「番号違い」は抑止として追加する（結果は残し、抑止だけ加える。監査ログに `correctionOf`） | `application/src/record-outcome.ts` |
| IQA-02 | プロバイダへの冪等キーを、クライアントのキーではなく通話 ID（全組織で一意）にした | `application/src/create-call.ts` |
| IQA-03 | プロバイダの失敗のうち「確実に受け付けなかった」もの（`ProviderRejectedError`）だけを FAILED にする。それ以外は発信済みかもしれないので REQUESTED のまま `PROVIDER_UNCERTAIN` を返す（回線上の 1 件として残るので、同じ番号への掛け直しは拒否される） | `application/src/ports.ts`・`create-call.ts`・`telephony/src/index.ts` |
| IQA-04 | 監査ログとイベント配信を、発信直前の再確認より前に移した（再確認と `createCall` の間に await を残さない） | `create-call.ts` |
| IQA-04b | 発信直前の再確認で、組織・キャンペーンの一時停止も見る（照会できなければ停止扱い） | `create-call.ts` |
| IQA-05 | 通話 ID だけでの特定は、プロバイダの ID が未記録の通話か、記録と一致する場合に限る | `db/migrations/0005_independent_qa_fixes.sql` |
| IQA-06 | NUL を含むカーソルを入口で 400 にする | `apps/api/src/app.ts` |
| IQA-07 | 想定外の例外のログから SQL のパラメータを落とし、電話番号らしい数字列は末尾 4 桁以外を伏せる | `apps/api/src/app.ts`（`redactForLog`） |
| IQA-08 | 15 分以上 REQUESTED のまま確定しない通話は、同時通話数に数えない（番号ごとの回線上 1 件には残る） | `CallRepository.countActive`（インメモリ・PostgreSQL） |
| IQA-09 | 国番号の後ろに残った国内の 0（`+81 (0)90…`）を落として同じ E.164 にそろえる（日本・韓国・中国・英国・独・仏・豪・NZ。0 が番号の一部の国は対象外） | `domain/src/phone.ts` |
| IQA-10 | パスワードを照合する前に、試行の枠を行ロックで不可分に予約する（`auth_throttle_reserve`）。成功したらアドレス側はリセット、IP 側は 1 回分戻す | `0005`・`application/src/auth.ts`・`db/src/repositories.ts` |
| IQA-11 | 通話を特定できなかった Webhook は処理済みにせず受信箱を解放し、再送で処理し直せるようにする | `application/src/provider-events.ts` |

### 残る課題（この修正では扱っていない）
- IQA-08 は数え方の対策だけで、**確定しない発信をプロバイダに問い合わせて照合する仕組み**（reconcile）はまだない。設計は ADR で行う
- IQA-09 の正規化は、まだ本番の取り込み経路で使われていない（連絡先の取り込みは Phase 4）。既存データの `+810…` を直す移行も Phase 4 で行う
- INFO の 4 件（`APP_ENV` の既定・`x-forwarded-for` の扱い・同じ組織の別担当者による replay・pg@9 での `Promise.all`）は未対応
- 独立性：この監査は会話の文脈を持たない別エージェント（同じ Claude）による。Codex など別系統の AI による監査は未実施
