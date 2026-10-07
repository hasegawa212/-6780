# IMPLEMENTATION_PLAN.md

優先度順。各項目は TDD（RED→GREEN→REFACTOR→REGRESSION）で進める。

## P0 — 本番稼働の前提（人の入力待ち。コード作業なし）
- [ ] 正式発信元 03 番号の審査通過 → `TAC_CALLER_ID`
- [ ] `TAC_AGENT_NUMBER`（担当携帯）
- [ ] 有効な `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN`（Fly secrets）
  → 揃ったら test 1本 → エンジン ON → 自動運転 ON。

## P1 — Webhook idempotency（ガイド STEP16 の実ギャップ）✅ 完了（TDD）
Twilio は同じ Webhook を重複/遅延/順序逆転で送ることがある。`tac/idempotency.py` で対応。
- [x] 重複 `call-status`（同一 CallSid+CallStatus）で outcome を二重計上しない
- [x] 異なる CallStatus（ringing→completed）はそれぞれ1回ずつ記録する
- [x] `amd-status` 重複（同一 CallSid）で redirect_call を二度叩かない
- 方式: 処理済み `CallSid+イベント` を JSON に短期記憶（TTL既定6h, `seen()`）し冪等化。
- テスト: `tests/test_idempotency.py`（6本）。全417 passed。

## P2 — docs をAI長期記憶として運用
- [x] `CLAUDE.md` + `docs/*` 作成（本コミット）
- [ ] 以降の作業で PROGRESS.md を都度更新（巨大プロンプト卒業）

## P3 — 観測性・運用
- [ ] ダッシュボードに idempotency/重複検知のカウンタ表示
- [ ] 発信結果の日次サマリ

## やらない（明示承認が必要）
実顧客への実発信 / Production Secret 変更 / 不可逆操作 / 有料処理の大量実行。
