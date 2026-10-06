# SECURITY.md

## 秘密情報
- Twilio/OpenAI 等の認証情報は **env（Fly secrets）のみ**。コード・リポジトリ・チャットに置かない。
- 操作 API (`/tac/call`, `/tac/autofollow/*` の操作系) は `TAC_OUTBOUND_TOKEN` 必須。
  未設定なら発信 API は無効化（公開URLからの無断課金発信を防止）。
- Webhook（Twilio が叩く `dtmf` / `call-status` / `amd-status`）はトークン不要だが、
  Twilio 署名検証 `tac/twilio_sig.py` を利用可能。

## PII
- 電話番号・氏名を含むファイル（`calls.jsonl`, `followup.json`, `dnc.txt`, 台帳）は **gitignore**。
- 本番はこれらを永続ボリューム上に置く。ログに生の番号/トークンを残さない。

## 課金・暴走防止
- `autofollow_enabled` 既定 OFF、scheduler 既定 OFF、`autofollow_batch_max=10`。
- 実顧客への実発信・大量課金は人の明示承認が必須。

## 露出時の対応
- トークンがチャット等に露出したら**ローテーション**（Fly secrets 再設定）。
