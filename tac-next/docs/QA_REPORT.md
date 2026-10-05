# QA_REPORT — 独立品質監査（敵対的テスト）

- 実施日：2026-10-05
- 方針：「動くことを示す」のではなく「壊せることを示す」。見つけた欠陥は、再現テスト（RED）→ 最小修正 → GREEN → 回帰確認、の順で直した。

## 1. Executive Summary

**最終判定：NO-GO**（tac-next・現行 TAC とも本番投入不可。理由は §15）

- **現行 TAC（本番で稼働中、`telegram-ai-bot/tac` @ `feature/sakura-max`）**
  - 抑止（DNC）中の相手に発信できる経路を **6 系統** 再現し、すべて修正した（PR #132、**未デプロイ**）。
  - 認証なしのエンドポイントと、本番設定（`fly.toml`）の安全装置 OFF は**未修正**。オーナー判断が必要。
- **tac-next（次世代版）**
  - Domain／Application 層で、**二重発信と DNC の TOCTOU（判定から発信までの隙間）** を含む 5 件を再現し、修正した（PR #131）。
  - ただし DB・認証・API・Webhook 受信・音声・AI Tool・UI が**未実装**なので、本番に必要な機能の大半がそもそもテストできない。

## 2. Test Scope

| 対象 | 範囲 | テストできなかったもの（理由） |
|---|---|---|
| 現行 TAC | DNC・発信ガード・自動フォロー・フォロー台帳・認証の有無・シークレット・Docker / 本番設定 | 本番の実機（egress でブロック）、Twilio 実通話、音声の barge-in・無音・留守電の実挙動 |
| tac-next | Domain（状態機械・Safety・時間帯・発話検知）、Application（発信・結果・キュー）、evals、mutation | DB・API・認証・RBAC・Webhook 受信・Realtime 音声・AI Tool・Handoff・UI・Analytics・Observability・Load / Soak・Docker（**すべて NOT IMPLEMENTED**） |

## 3. Baseline（開始時点）

| 対象 | lint | typecheck | unit | build | mutation |
|---|---|---|---|---|---|
| tac-next | ✅ | ✅ | ✅ 261/261 | ✅ | ✅ 16/16 |
| 現行 TAC（tac 関連 37 ファイル） | — | — | ✅ 360/360 | — | — |

開始時点の失敗：なし（ただし、§6・§7 の欠陥はすべて「テストが無いので通っていた」）。

## 4. Findings by Severity

| ID | Sev | 対象 | 内容 | 状態 |
|---|---|---|---|---|
| QA-TAC-01/05/07 | **CRITICAL** | 現行 TAC | DNC ファイルが読めない・ボリューム未マウントのとき「禁止なし」になり発信する（手動・自動フォロー・台帳判定の全経路） | **FIXED**（PR #132） |
| QA-TAC-02 | **CRITICAL** | 現行 TAC | `090-…`／全角／空白入りで登録した DNC が、`+8190…` への発信に効かない（逆方向も同様） | **FIXED** |
| QA-TAC-09 | **CRITICAL** | 現行 TAC | API とスケジューラが同時に `run_once` すると、同じ相手へ **5 並列で 5 回発信** | **FIXED** |
| QA-NX-01 | **CRITICAL** | tac-next | Operator A・B・Worker が別々の冪等キーで同時に発信すると **3 件とも外部発信** | **FIXED**（PR #131） |
| QA-NX-02 | **CRITICAL** | tac-next | 判定の後・発信の前に DNC 登録が入っても発信する（TOCTOU） | **FIXED** |
| QA-TAC-03 | HIGH | 現行 TAC | フォロー台帳の「連絡停止」が DNC に入らず、手動発信できる | **FIXED** |
| QA-TAC-06 | HIGH | 現行 TAC | 「連絡不要」「電話しないで」「二度と」「迷惑」が拒否語に無い。再調整語と一緒だと**架電対象**に分類される | **FIXED** |
| QA-TAC-04/08 | HIGH | 現行 TAC | 自動フォローの発信が `dialed` として記録されず、1 日上限に数えられない | **FIXED** |
| QA-NX-03 | HIGH | tac-next | 判定の後・発信の前に全発信停止が入っても発信する | **FIXED** |
| QA-TAC-10 | HIGH | 現行 TAC | 認証なしで `/tac/insights`（200）と `POST /tac/close/<sid>`（200）が使える。`/tac/assist/<sid>`、relay WS、realtime サービスも認証なし | **OPEN**（呼び出し元の確認が必要） |
| QA-TAC-11 | HIGH | 現行 TAC | `fly.toml` で発信時間帯・1 日上限・名乗りが有効化されていない（既定は OFF） | **OPEN**（本番設定の変更はオーナー操作） |
| QA-TAC-12 | HIGH | 現行 TAC | DNC の解除（`/tac/dnc?action=remove`）が共有トークンだけででき、監査記録もない | **OPEN** |
| QA-NX-04 | MEDIUM | tac-next | 同じ通話の結果を同時に 2 回送ると例外で落ちる | **FIXED** |
| QA-NX-05 | MEDIUM | tac-next | 相手のタイムゾーンが不正だと RangeError のまま落ちる。新しい判定は「時間外」（fail closed） | **FIXED** |
| QA-NX-06 | MEDIUM | tac-next | 「今はいいです」「また今度」「忙しい」「考えておきます」を検知せず、AI が説得を続けられた | **FIXED**（SOFT_DECLINE → WRAP_UP、抑止はしない〔要法務確認〕） |
| QA-TAC-13 | MEDIUM | 現行 TAC | トークンを `?token=` のクエリでも受け付ける。gunicorn のアクセスログに残りうる | **OPEN**（外部の呼び出し元を壊す恐れがあるので推奨のみ） |
| QA-TAC-14 | MEDIUM | 現行 TAC | コンテナが root で動作する（Dockerfile / Dockerfile.voice に `USER` がない） | **OPEN** |
| QA-NX-07 | LOW | tac-next | 「連絡いりません」が STOP_REQUESTED に分類される（抑止はされる） | **FIXED**（DO_NOT_CALL へ） |
| QA-TAC-15 | INFO | 両方 | shallow clone のため Git 履歴全体のシークレットスキャンはできていない。作業ツリーにはテスト用のダミー値のみ | 制約として記録 |

### 代表的な Finding の詳細

```
ID: QA-NX-02
Severity: CRITICAL
Component: tac-next / application / CreateCallUseCase
Description: 発信可否の判定（抑止・停止を含む）と外部発信の間に、別の担当者が「拒否」を記録しても発信してしまう。
Impact: 拒否した直後の相手に電話がかかる（再勧誘禁止違反・苦情）。
Reproduction: adversarial.test.ts「判定の後・発信の前に DNC 登録が入ったら、外部発信しない」
  （calls.insert の直後に suppression.add を差し込む）。
Root Cause: 判定時点の結果だけで外部発信していた（check-then-act）。
Regression Test: 上記 + 全発信停止版。
Fix: 外部発信の直前に isContactable と isOutboundStopped を確かめ直す。
  該当すれば通話を CANCELED にし、call.blocked を監査に残す。
Verification: 277→305/305 GREEN、critical mutant「判定後に入った DNC・停止を無視して発信」KILLED。
Residual Risk: 再確認から外部発信までの間（ミリ秒単位）は残る。
  Phase 2 で、抑止の登録と発信を同じ行ロック（相手単位）で直列化して塞ぐ。
```

```
ID: QA-TAC-09
Severity: CRITICAL
Component: 現行 TAC / autofollow.run_once・run_batch
Description: API（/tac/autofollow/run）とスケジューラが同時に動くと、同じ相手を選んで二重に発信する。
Impact: 同じ人に短時間で複数回の自動発信（苦情・費用）。
Reproduction: test_qa_dnc_invariant.py::test_concurrent_run_once_never_double_dials_same_person（5 スレッド → 5 回発信）。
Root Cause: 選ぶ→発信→発信済みにする、がアトミックでない（ロックなし）。
  run_once は渡された一覧の上で「本日発信済み」を更新していなかった。
Fix: _run_lock で run_once / run_batch を直列化し、発信済みの印を一覧にも付ける。
Verification: 380/380 passed。
Residual Risk: 複数マシンに水平スケールすると再発する（現状は 1 マシン固定）。
```

## 5. Critical Invariants

| 不変条件 | 現行 TAC | tac-next |
|---|---|---|
| 抑止中の相手には新しい外部発信を生まない | 6 系統の迂回を塞いだ。残りは TOCTOU（同一プロセス内でミリ秒単位）と、複数台構成 | 判定時と外部発信の直前の 2 回確認。残りは Phase 2 の行ロック |
| 同じ相手へ同時に二重発信しない | 自動フォローはロックで直列化。手動（`/tac/call`）の同時押しは**未検証**（担当者 2 人が同じ番号を同時に押すケース） | 「回線上の通話は番号ごとに 1 件」をリポジトリの契約にした（DB では部分一意インデックス） |
| 止めている間は発信しない | 自動フォローの OFF / 一時停止のみ。**手動発信を止める kill switch がない** | 判定時と直前の 2 回確認 |
| 判定できないときは発信しない（fail closed） | DNC は対応済み。1 日上限は読めないと 0 件扱いのまま（OPEN） | 抑止・時間帯（不正な TZ）は対応済み |

## 6. DNC Results（必須シナリオ）

| シナリオ | 現行 TAC | tac-next |
|---|---|---|
| DNC 済みの相手へ直接発信 | ✅ 拒否（全表記） | ✅ |
| DNC 済みの相手をキューに入れる | ✅（台帳の判定も fail closed） | ✅（照会失敗の 1 件だけ除外） |
| キャンペーンを変えて再発信 | N/A（キャンペーンの概念なし） | ✅（抑止は組織単位） |
| 別の担当者から再発信 | ✅（DNC は全体で 1 つ） | ✅ |
| retry job から再発信 | ✅（自動フォローも is_blocked を通る） | ✅（キューで再確認） |
| 再起動後に再発信 | ✅（ファイルがボリューム上にある。未マウントなら発信しない） | **未検証**（Phase 2：永続化） |
| 重複 Webhook で DNC が解除される | ✅ 解除する Webhook が無い | ✅ |
| AI Tool から DNC を回避 | N/A（AI に発信 Tool が無い） | **未検証**（Phase 12） |
| API を直接呼んで回避 | ✅ 発信 API はガードを必ず通る | ✅ |
| UI を経由しない発信 | ✅（scheduler・run_batch も同じガード） | ✅ |
| DNC 登録と発信の競合 | 一部（同一プロセスのみ） | ✅（直前の再確認）＋ Phase 2 |

## 7. 自然言語の DNC 評価（tac-next `detectSafetySignals`）

- 明確な拒否 8 文：**8/8 を検知**（DO_NOT_CALL 6、STOP_REQUESTED 2。どちらも抑止される）。
- eval データセット全体：拒否 19 件中 19 件を検知（**DNC 再現率 100%**）。
- 適合率は 18/19 ≈ 95%。外れた 1 件「興味がないわけじゃない」は、過検知を安全側として意図的に拒否扱いにしている。
- 曖昧な断り 5 文：修正前は**すべて素通り** → SOFT_DECLINE（抑止なし・WRAP_UP）に変更した。
- プロンプトインジェクション 8 文：Safety の誤発火なし。ただし「従わない」ことの検証は Tool Gateway が無いので**未実施**（Phase 12）。

## 8. Tenant Isolation / RBAC / Authentication
- tac-next：Application 層で他テナントの contact / call が NOT_FOUND になることは既存テストで確認済み。DB の RLS・API・RBAC・セッション・CSRF は**未実装のためテスト不能**。
- 現行 TAC：テナント・ロールの概念がなく、共有トークン 1 本で運用している（**設計上テスト対象外 = 本番の重大リスク**）。認証なしのエンドポイントは QA-TAC-10。

## 9. Concurrency Results
| シナリオ | 結果 |
|---|---|
| 同じ冪等キー × 2 / 10 / 100 並列（tac-next） | ✅ 外部発信は 1 件 |
| 別キー × Operator A・B・Worker（tac-next） | ❌ → ✅（QA-NX-01） |
| 自動フォローの同時実行 5 スレッド（現行 TAC） | ❌ → ✅（QA-TAC-09） |
| 結果記録の同時送信（tac-next） | ❌ → ✅（QA-NX-04） |
| 手動 `/tac/call` の同時押し（現行 TAC） | **未検証**。1 日上限は read-then-call のまま |

## 10. Telephony Failure / Webhook
- Provider timeout → 再送しても二重発信しない：tac-next の既存テストで ✅。
- 重複・順序逆転・遅延 Webhook：tac-next の domain で ✅。非終端どうしの後戻りは統合時に補強済み。受信エンドポイントは**未実装**。
- 偽造 Webhook：現行 TAC は署名検証のテストあり（本番 ON）。tac-next は**未実装**。
- Provider 停止時の circuit breaker：**どちらも未実装**。

## 11. AI Safety / Prompt Injection
- 決定的な安全層（発話 → Safety）は §7 のとおり。
- 次は LLM・Tool が未実装のため **NOT TESTED**：Tool Authorization、Fake tool success、Hallucination、Indirect / Knowledge / Tool injection、AI 停止時の handoff、barge-in、無音、留守電。
- 現行 TAC の Realtime（OpenAI）には Tool も handoff もない。ソース上、「電話しないで」を記録する経路がないことを監査済み（EXISTING_APP_AUDIT F）。

## 12. Security Results
- シークレット：作業ツリーに本物の値なし（テスト用ダミーのみ）。履歴全体は未スキャン（QA-TAC-15）。
- エラー漏洩・入力検証：tac-next には API が無く N/A。現行 TAC は既存テスト範囲のみ。
- Docker：root で動作（QA-TAC-14）。
- XSS：現行 UI は `textContent` を使っている（既存監査で確認）。SSRF：通知 Webhook の URL は環境変数で固定（利用者は入力できない）。

## 13. Performance / Load / Soak / Observability / Alert / Audit
**NOT TESTED**。シミュレーター・API・メトリクス・アラートが未実装のため。監査ログは tac-next の Application 層で `call.blocked`・`call.requested`・`outcome.recorded`・`suppression.added` を確認した。改ざん耐性は未検証。

## 14. Fixed Issues（再現テスト付き）
- 現行 TAC：`tests/test_qa_dnc_invariant.py`（20 件）。360 → **380 passed**。
- tac-next：`application/test/adversarial.test.ts`（8 件）、`calling-window.test.ts`（QA 8 件）、`utterance-safety.test.ts`（QA 12 件）、evals（+18 ケース）。261 → **305 passed**。critical mutant 16 → **21/21**。
- 既存テストの扱い：
  - 削除・弱化は 0 件。
  - `test_autofollow.py` は、DNC の差し替え先に新しい入口 `is_blocked` を追加しただけ（期待値は不変）。
  - eval `objection-001` は方針の変更（SOFT_DECLINE）として期待値を更新した（note に記録）。

## 15. Remaining Risks / Production Blockers

**Production Blockers（これが残る限り GO にしない）**
1. **現行 TAC**
   - PR #132 が**未デプロイ**。本番では今も DNC が fail open で、表記ゆれでもすり抜け、二重発信も起きる。
   - 本番設定（時間帯・上限・名乗り）が OFF（QA-TAC-11）。
   - 認証なしのエンドポイント（QA-TAC-10）。
   - DNC 解除に監査がない（QA-TAC-12）。
   - 手動発信を止める kill switch がない。
2. **tac-next**
   - DB（永続化・再起動後の DNC・行ロック）、認証 / RBAC / テナントの API 強制、Webhook 受信と署名検証が未実装。
   - AI Tool Gateway、Human handoff、Observability、Load 試験も未実装。
   - 再確認から外部発信までの残り TOCTOU は Phase 2 で塞ぐ。

## 16. Production Recommendation
- **現行 TAC：NO-GO**（現状の本番）。
  - PR #132 をデプロイし、QA-TAC-10 / 11 / 12 を是正すれば **GO WITH CONDITIONS** になりうる。
  - 条件：手動発信の kill switch、自動フォローの名乗りの確認、単一マシン構成の維持。
- **tac-next：NO-GO**。Domain の安全ロジックは強化したが、本番に必要な層（DB・認証・API・音声・AI）が未実装。
