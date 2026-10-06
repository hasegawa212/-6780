# DOMAIN.md — ドメインモデルと用語

## エンティティ
- **Contact / 台帳エントリ**: 1人の見込み客。`num`(E.164正規化) をキー、`name/area/assignee`、
  分類(category)、フォロー回数、出典(record)、最終更新を持つ。保存: `tac/followup.json`。
- **分類(category)**: 記録文から `tac/followup.classify` が判定。
  - 発信可(CALLABLE): `再調整希望` / `日程返答待ち` / `不在`
  - 非対象: `要確認`（人の確認待ち） / `連絡停止`（DNC相当・自動ブロック）
- **Decision**: `autofollow.decide` の結果。発信可否と理由(reason)。
- **Call / Outcome**: 発信1回と結果（completed/no-answer/busy/… + AnsweredBy）。記録: `calls.jsonl`。

## 電話番号
- 正規化は `tac/phone.py`。保存・DNC照合・重複排除はすべて E.164 で行う。
- 台帳 ingest の重複判定キー: `num:{e164}` または `rec:{record[:40]}`。

## ゲート（発信可の条件・全て満たす）
1. 同意が「拒否」でない（consent）
2. DNC に載っていない（dnc）
3. 発信時間帯内（calling_hours, JST）
4. レート制限内（rate_limit）
5. 当日未発信（1相手1日1回）かつ 合計 `follow_cap`(=2) 未満
6. バッチ全体上限 `autofollow_batch_max`(=10) 未満
7. エンジン ON かつ（バッチ時）自動運転 ON

## 発信モード
- **会話型**: `VOICE_STREAM_URL` 設定時、`<Connect><Stream>` でさくらに接続。
  `<Parameter mode=followup customer_name=...>` を渡す。
- **IVR(DTMF)**: ストリーム未設定時のフォールバック。`1`日程変更 / `2`担当者と話す / `9`連絡不要(DNC登録)。
