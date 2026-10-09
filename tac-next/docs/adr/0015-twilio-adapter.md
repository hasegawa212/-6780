# ADR-0015: Twilio アダプタ（担当者が先の会議ブリッジ・再送しない・署名つき状態通知）

- Status: Accepted
- Date: 2026-10-07

## Context
Phase 11。日本の番号（Twilio の Regulatory Bundle「Japan: Local - Business」）は審査中で、番号はまだ無い。
番号が届いた時点で、設定を入れるだけで staging の実通話 1 件（**オーナーの承認が必要**）に進める状態にしておく。

確認した一次情報（開発環境から twilio.com のドキュメントには接続できないため、Twilio 公式の機械可読な仕様を使った）:
- **Twilio 公式 OpenAPI**（`github.com/twilio/twilio-oai` の `spec/json/twilio_api_v2010.json`）：
  `POST /2010-04-01/Accounts/{AccountSid}/Calls.json` の引数（`To`・`From`・`Twiml`（最大 4000 文字）・`StatusCallback`・
  `StatusCallbackMethod`・`StatusCallbackEvent`（`initiated`・`ringing`・`answered`・`completed`、1 つずつ別の引数で渡す）・`Timeout`・`TimeLimit`）、
  通話の状態（`queued`・`ringing`・`in-progress`・`completed`・`busy`・`failed`・`no-answer`・`canceled`）、
  通話の終了（`POST …/Calls/{Sid}.json` の `Status` は `canceled`・`completed`）。**発信 API に冪等キーの仕組みは無い**
- **Twilio 公式 SDK**（twilio-node 6.1.2 の `lib/webhooks/webhooks.js`）：`X-Twilio-Signature` の計算
  （URL＋キー昇順の「キー＋値」を認証トークンで HMAC-SHA1 → base64）
- **現行 TAC**（`telegram-ai-bot/tac/outbound.py`・`server.py`、本番で稼働中）：会議（Conference）で担当者とお客様をつなぐ方式、
  状態通知の `CallSid`・`CallStatus` の使い方

## Decision
1. **会議ブリッジ・担当者が先**：1 回の発信で、まず担当者（`TWILIO_AGENT_NUMBER`）へ、次にお客様へ発信し、同じ会議 `tac-{callId}` に入れる。
   現行 TAC は「お客様が先」だが、逆にした。担当者のレッグがどんな形で失敗しても（拒否・接続断・SID なし）お客様には一度も発信せず、
   `ProviderRejectedError` で返す（通話は FAILED、番号はふさがない）
2. **会議の設定**：担当者は `startConferenceOnEnter=true`、お客様は `false`（担当者が入るまで待つ）。どちらが切っても会議は終わる
   （`endConferenceOnExit=true`）。両方のレッグに `TimeLimit`（既定 1800 秒）をかけ、会議に 1 人で残り続けない
3. **名乗り**：お客様のレッグの冒頭で `<Say language="ja-JP">`（XML エスケープ）。TwiML が 4000 文字を超えるなら、名乗りを切り詰めずに発信しない
4. **録音しない**：`Record` も `record=` も付けない（`RECORDING_ENABLED` は既定 OFF、録音の同意の告知は未実装）
5. **再送しない**：Twilio に冪等キーが無いので、アダプタは同じ発信を自分で送り直さない。同じプロセス内で同じ冪等キー（通話 ID）の発信は 1 回だけ送る。
   お客様のレッグの結果の分類：
   - 4xx → `TwilioRejectedError`（`ProviderRejectedError`）。担当者のレッグを `canceled`（だめなら `completed`）で切る
   - 接続断・5xx・応答の形が想定外 → `TwilioUncertainError`。中断（既定 10 秒）→ `ProviderTimeoutError`。
     どちらも通話は REQUESTED のまま（IQA-03）。番号はふさがったままなので、別のキーで掛け直しても二重発信にならない
   - エラーの本文は電話番号を含むことがあるので、例外のメッセージには Twilio のエラーコードだけを残す
6. **状態通知**：お客様のレッグだけに `StatusCallback = {PUBLIC_BASE_URL}/v1/webhooks/twilio?callId={callId}`。
   - 署名は**常に**検証する（無効にする設定は作らない）。URL は Host ヘッダーではなく設定した `PUBLIC_BASE_URL` から組み立てる。
     通話 ID は URL の一部として署名されるので、別の通話に付け替えられない
   - 重複排除のキーは `{CallSid}:{CallStatus}`（1 状態につき 1 回の通知。再送は同じキー）
   - 知らない `CallStatus` は 200 `IGNORED`（推測で状態を作らない・再送させない）。`initiated` は DIALING として扱う
   - 受信箱に保存するのは状態に関わる項目だけ（`To`・`From` などの電話番号は保存しない）
   - 応答が失われた発信（REQUESTED・プロバイダ ID なし）も、届いた通知の通話 ID から特定でき、Twilio の ID が付く
7. **設定**：`TELEPHONY_PROVIDER=twilio` には `TWILIO_ACCOUNT_SID`・`TWILIO_AUTH_TOKEN`・`TWILIO_AGENT_NUMBER`（E.164）・
   `PUBLIC_BASE_URL`（https、パスなし）が必須。local / test では従来どおり Twilio を作れない（`ProviderNotAllowedError`）。
   発信元の番号はキャンペーンの `caller_id_e164`（取得した Twilio の番号を入れる）

## 確認できていないこと（UNKNOWN。実通話の前に確かめる）
- 状態通知の `Timestamp`・`SequenceNumber` の形式：使っていない（受け取った時刻を `occurredAt` にする）
- `StatusCallbackEvent=initiated` の通知の `CallStatus` の値：`initiated` と `queued` の両方を DIALING として扱う
- 鳴っている最中の通話に `Status=completed` を送ったときの挙動：`completed` → だめなら `canceled` の順で送る
- 日本の番号での発信元表示・会議の保留音（既定の保留音が流れる）
- 留守電の判定（AMD）は使っていない。留守電につながっても名乗りが流れ、担当者が「留守電」を記録する

## Alternatives
- **お客様が先**（現行 TAC）：担当者のレッグの失敗時に、お客様が保留のまま取り残される。担当者が先を採用
- **公式 SDK を実行時に使う**：依存が大きい。REST 3 本と署名だけなので自作し、SDK はテストで署名の正解を出す参照実装として使う（devDependency）
- **確定しない失敗で 1 回だけ自動再送**：Twilio に冪等キーが無く、二重発信になりうる。採用しない
- **ブラウザの通話（Twilio Client / WebRTC）**：画面と認証の作り込みが要る。担当者の電話を鳴らす方式から始める

## Consequences
- 番号が届いたら、`RUNBOOK.md`「Twilio の番号が届いたら」の手順で staging の実通話 1 件まで進められる（実通話はオーナーの承認が必要）
- 担当者の番号は組織で 1 つ（ユーザーごとの番号・ブラウザ通話は後続）
- 確定しない発信のうち、**実際には発信されなかったもの**は、状態通知が来ないため REQUESTED のまま番号をふさぐ（fail closed）。
  → ADR-0016 で、Twilio の通話一覧との照合（reconcile）を追加した
- 転送（`transferCall`）は未実装で、呼ぶと例外（Phase 13）
