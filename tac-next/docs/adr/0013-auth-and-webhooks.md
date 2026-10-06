# ADR-0013: 認証（Cookie セッション）と Webhook の受信・状態の compare-and-set

- Status: Accepted
- Date: 2026-10-06

## Context
最初の縦切りを HTTP まで通すには、個人アカウントでの認証（現行 TAC の共有トークンは漏えい済み：SECURITY.md）、
組織・ロールの強制、プロバイダの Webhook の受信が要る。Webhook は at-least-once で、重複・順序違い・遅延・同時到着が起きる。
Phase 7 の実装中に、次の競合を見つけた（OBSERVED、テストで再現）：
- 発信 API の応答より先に RINGING の Webhook が届くと、その後の「DIALING で保存」が状態を後戻りさせる
- 同時に届いた Webhook が、古い読み取りに基づいて状態を上書きする（読み取り → 書き込みの競合）

## Decision
1. **認証**：メールアドレス＋パスワード、Cookie セッション。パスワードは scrypt（N=2^17, r=8, p=1、OWASP の推奨値）。
   セッション ID と CSRF トークンは 256 bit の乱数で、DB には `HMAC-SHA256(SESSION_SECRET, token)` だけを保存する。
   存在しないユーザーでもダミーのハッシュ検証を行い、応答と時間で有無を漏らさない。ロールは要求のたびに所属から引き直す
   （所属の削除・ロールの変更・ユーザーの無効化は次の要求から効く）
2. **CSRF**：セッションに紐づくトークンを `X-CSRF-Token` で送る（synchronizer token）。加えて SameSite=Lax と JSON 必須（フォームの自動送信で叩けない）。
   ログインは CSRF トークンの前なので、JSON 必須と SameSite でログイン CSRF を防ぐ
3. **テナントをまたぐ照会は SECURITY DEFINER 関数だけ**：ログイン候補・セッション・Webhook の受信箱・通話の特定。
   関数の所有者は `tac_definer`（NOLOGIN・BYPASSRLS）、`tac_app` は表を直接読めず、関数を実行できるだけ
4. **Webhook の受信**：署名とタイムスタンプ（前後 5 分）を検証 → `(provider, event_id)` の受信箱で処理権を取る
   （処理済み・処理中なら DUPLICATE。失敗したら手放し、処理中のまま 60 秒経ったら取り直せる）→ 状態の変更とイベントの記録を 1 トランザクションで
5. **状態の更新は compare-and-set**：`CallRepository.transitionStatus(org, id, expected, next)`（`UPDATE … WHERE status = expected`）と
   `advanceCallStatus`（読み取り → `reconcileProviderStatus` → CAS、割り込まれたらやり直す）。発信の応答もこの経路を通る。
   プロバイダの ID は `attachProvider`（未記録のときだけ）
6. **mock の Webhook の受け口は local / test だけ**（`config.telephony.mockWebhooksEnabled`）。秘密鍵が無ければすべて 401
7. **電話シミュレーター**：`MockTelephonyProvider` を拡張し、シナリオ（ANSWER・BUSY・REJECT・NO_ANSWER・DISCONNECT・VOICEMAIL・PROVIDER_ERROR）ごとに
   Webhook のイベント列を作る。届ける順序・回数はテストが決める（重複・遅延・順序違い・同時を再現できる）

## Alternatives
- JWT（ステートレス）：失効・ロール変更の即時反映ができない（不採用）
- argon2id：ネイティブ依存が増える。scrypt は Node 標準で OWASP の推奨にもある（scrypt を採用、将来の移行は形式の接頭辞で可能）
- 行ロック（SELECT … FOR UPDATE）で Webhook を直列化：アプリ層の契約として表現しにくく、インメモリと挙動がずれる（CAS を採用）
- ログイン用・Webhook 用に別のログインロールを作る：接続の管理が増える（関数で権限を絞る方を採用）

## Consequences
- （2026-10-06 追記）ログイン試行の制限（`auth_throttle`、0004）と運用 CLI `create-user` を実装。招待・パスワード再設定は未実装
- `tac_definer` の作成にはスーパーユーザー（または BYPASSRLS を付けられる権限）が要る（DATABASE.md「運用」）
- テストは速度のため scrypt のコストを下げる（logN=10）。本番の既定値（17）は別のテストで固定している
