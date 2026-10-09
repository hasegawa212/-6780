# ADR-0016: 確定しない発信の照合（reconcile）

- Status: Accepted
- Date: 2026-10-09

## Context
発信の応答が届かない（接続断・5xx・タイムアウト）と、通話は REQUESTED のまま・プロバイダの ID 無しで残る（IQA-03）。
実際に発信されていれば、状態通知の URL に載せた通話 ID で回収できる（ADR-0015）。
しかし**実際には発信されなかった**ときは通知が来ず、その番号は「回線上 1 件」の制約で**ずっと掛けられない**（fail closed）。
IQA-08 の対策（15 分たったら同時通話数に数えない）は数え方だけで、番号のふさがりは解けなかった。

## Decision
1. **照合のユースケース** `ReconcileUncertainCallsUseCase`：作成から 2 分以上たった、REQUESTED・プロバイダの ID なしの通話を古い順に最大 50 件取り、
   プロバイダの通話一覧（`TelephonyProvider.findCalls`：同じ発信元から同じ相手へ、作成時刻の 2 分前以降）と突き合わせる
   - 候補は「作成時刻の前後（−2 分〜＋10 分）」で、**すでに別の通話に付いている ID は除く**（Twilio のアカウントは組織で共有なので、他の組織が同じ番号に掛けた通話を取り違えない）
   - 候補が **1 件** → その ID を付け（未記録のときだけ）、状態を Webhook と同じ compare-and-set で進める。状態が分からなければ DIALING
   - 候補が **0 件**で、作成から **15 分**以上 → 発信されなかったとして **FAILED**（番号のふさがりが解ける）。15 分未満なら何もしない
   - 候補が **2 件以上**・**一覧が引けない**・**一覧を持たないプロバイダ** → 何も変えない（番号はふさがったまま。二重発信は起きない）
   - 結果は監査ログ `call.reconciled`（操作者 `system:reconciler`）とイベント（`CallStatusChanged` / `CallFailed`）に残す
2. **Twilio の一覧**：`GET …/Calls.json?To=&From=&PageSize=50`（Twilio 公式 OpenAPI の ListCallResponse）。`direction=outbound-api` だけを使い、
   `date_created`（RFC 2822）で絞る。**続きのページがあれば失敗にする**（全部を見ていないのに「発信されなかった」と決めない）
3. **対象の探し方**：マイグレーション 0006 の SECURITY DEFINER 関数 `list_uncertain_calls(older_than, limit)` が全組織から **ID だけ**を返す
   （中身は組織を決めてから RLS の下で読む）。部分インデックス `calls_uncertain_idx`
4. **実行**：API のプロセスで 1 分ごと（`findCalls` を持つプロバイダ＝Twilio のときだけ。mock では動かない）。前の回が終わるまで次を始めない。
   ログは種類ごとの件数だけ（通話 ID・電話番号は出さない）

## Alternatives
- **Twilio に通話 ID を問い合わせる**：Call リソースにこちらの ID を載せる項目が無い（StatusCallback の URL は一覧に出ない）。発信元・相手・時刻で突き合わせる
- **見つからなければすぐ FAILED**：一覧への反映の遅れで、実は発信済みの通話の番号を解放し、二重発信になりうる。15 分待つ
- **別プロセス（worker）で回す**：worker がまだ無い（Phase 6）。API のプロセスで回し、compare-and-set と「未記録のときだけ ID を付ける」で複数台でも安全にする

## Consequences
- 発信されなかった通話は、最長でおよそ 16 分で番号のふさがりが解ける。発信された通話は、Webhook が来なくても一覧から回収できる
- 1 日の同じ番号への上限（`perNumberDailyLimit`）には FAILED の通話も数える（従来どおり）。解けるのは「回線上 1 件」の制約だけ
- UNKNOWN：Twilio の一覧が作成直後の通話をいつ返すか（遅れの大きさ）・一覧の並び順。実通話（RUNBOOK）で確かめる
