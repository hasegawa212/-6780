# PROGRESS.md — 現在地

更新: 2026-10-06 / ブランチ `feature/sakura-max`

## 完了（本番デプロイ済み・テスト green）
- ✅ 会話AIさくら（低遅延 g711_ulaw / VADチューニング / 日本語文字起こし / barge-in / 人格）
- ✅ 自動フォロー架電エンジン（decide/select_next/run_once/run_batch）＋常駐スケジューラ
- ✅ 安全ゲート: 同意 / DNC / 時間帯 / レート / 1日1回+合計2回 / バッチ上限10 / OFF by default
- ✅ IVR(DTMF) フォールバック（1日程変更 / 2担当 / 9連絡不要=DNC登録）
- ✅ AMD 留守電スキップ（機械→即切断）
- ✅ 担当者への生転送（transfer_to_agent → redirect_call → <Dial>、不在時フォールバック）
- ✅ 台帳 ingest（分類・重複排除・overwrite 補完）
- ✅ ダッシュボード / モバイル UI / 操作 API（トークン必須）
- ✅ Webhook 冪等性（重複/遅延/順序逆転の二重計上を防止, `tac/idempotency.py`, TDD）
- ✅ テスト 417 本（実発信は全てモック）

## 本番データ
- 台帳 90 件ロード済（不在41 / 再調整7 / 要確認37 / 連絡停止5）。発信可 ≒ 48。
- エンジン OFF / 自動運転 OFF（安全既定）。

## ブロッカー（P0・人の入力待ち）
- 正式発信元 03 番号（審査中）/ 担当携帯 / 有効な Twilio 認証。
  → 揃うまで実発信はしない。テスト発信はボス自身の携帯宛に1本のみ可能。

## 次の一手
- 本番 go-live 前: `TAC_ENFORCE_CALL_HOURS` / `TAC_DAILY_CALL_CAP` / `TAC_VERIFY_TWILIO_SIGNATURE` を ON。
- 実客投入は 03 番号の審査通過後（現在審査中）。
