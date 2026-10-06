# QA_AUDIT — 独立 QA・破壊テストの手順（Codex などの BREAK 担当用）

**Do not prove that the system works. Try to prove that the system can fail.**
あなたは開発者の実装を肯定する役ではない。本番で誤発信・二重発信・DNC 違反・情報漏えい・AI の暴走・テナント漏えい・データ破損・復旧不能が起きる前に見つける。
共通の契約は [`../../AGENTS.md`](../../AGENTS.md)。証拠は OBSERVED / INFERRED / UNKNOWN / PROPOSED に分ける。

## 進め方
1. **リポジトリ監査**：`AGENTS.md` → `PROGRESS` → `ARCHITECTURE` → `DOMAIN` → `TESTING` → `SECURITY` → `CRITICAL_INVARIANTS`、ソース・テスト・CI・設定・依存。`QA_REPORT.md`（過去の監査）は**自分の監査が終わってから**突き合わせる
2. **Baseline**：`pnpm install && pnpm check && pnpm test:critical && pnpm test:mutation && pnpm build`。開始時点の失敗を記録
3. **Test Inventory**：Unit / Domain / Integration / Contract / Component / E2E / Security / Concurrency / Load / AI Eval に分類し、未整備の層を明記
4. **Coverage Gap**：行カバレッジではなく、不変条件・分岐・失敗経路・並行性・認可・テナント境界・抑止・冪等性・外部障害の抜けを探す
5. **Risk Map → Critical Invariant Audit → Adversarial Testing**

## 攻撃の観点（存在する層だけ。無い層は UNKNOWN として報告）
- **DNC / 抑止**：直接発信・キュー投入・キャンペーン変更・別オペレーター・再試行ジョブ・再起動後・重複 Webhook による解除・AI ツール・API 直叩き・UI を通らない発信・判定と登録の競合
- **並行性**：Operator A＋B＋Worker が同じ相手へ同時発信／同じ要求を 2・10・100 並列 → 外部発信 1 件
- **Webhook**：再送・順序逆転（COMPLETED → ANSWERED → RINGING）・数分遅れ・署名なし／不正署名／期限切れタイムスタンプ
- **状態機械**：禁止遷移（COMPLETED → RINGING 等）が永続化されない
- **障害**：重要処理の途中の DB 障害（部分状態・二重発信・抑止の消失・孤児レコード）、worker のクラッシュ、プロバイダの「受け付けたが応答なし」（単純再試行で二重にしない）、プロバイダ停止（サーキットブレーカー・通知・観測）
- **AI / 音声**：AI 停止時は人へ or 安全に終了、リアルタイム切断、引き継ぎの瞬間に AI が発話中、バージイン、無音（5・15・30 秒・長時間）、留守電の誤認
- **会話の評価**：明確な拒否（もう電話しないで／二度とかけてこないで／連絡いりません／営業電話やめてください／もう結構です／必要ありません／興味ありません／今後は連絡不要です）と曖昧な断り（今はいいです／今日は大丈夫です／また今度／ちょっと忙しい／考えておきます）を混同しないか。**DNC recall を最優先**
- **インジェクション**：顧客の発話（指示無視・システムプロンプト表示・管理者モード・他の顧客情報・DNC から削除・別番号へ発信・全件エクスポート）、CRM メモ・ナレッジ・ツール出力への埋め込み、ツールの認可迂回（mark_do_not_call・create_appointment・request_handoff）、失敗したのに「登録しました」と言う、ナレッジにない価格・割引・契約条件・実績・保証の捏造
- **認証・認可**：テナント A から B の contact / call / transcript / recording / analytics、連番 ID の書き換え、ロール（Owner・Admin・Manager・Operator・Viewer）の権限表を作り、許可されない API を直接呼ぶ（UI で隠しているだけを安全とみなさない）、セッションなし・期限切れ・改ざん Cookie・CSRF
- **入力**：空・null・巨大文字列・不正 Unicode・壊れた JSON・想定外の型・負数・極端な数値、電話番号（携帯・固定・国際・不正・短すぎ・長すぎ・全角・空白・ハイフン）、CSV（巨大・文字コード違い・列欠け・重複・数式インジェクション）、検索（長文・ワイルドカード）、XSS（氏名・会社名・メモ）、SSRF
- **漏えい**：ログに完全な電話番号・会話本文・キー・トークン・Cookie、本番のエラー応答にスタックトレースや内部パス
- **上限**：レート制限（ユーザー・組織・キャンペーン・宛先）、発信上限、全発信停止中の UI / API / キュー / 再試行 / worker、予算超過、時間帯の境界（23:59・0:00・夏時間・タイムゾーン違い）、日付（うるう年・月末・年末）
- **フォローアップ・予約**：同じイベントから重複作成しない、同じ枠の同時予約
- **UI**：キーボードのみ・フォーカス・ARIA・コントラスト・タップ領域、狭い画面、遅い回線・オフライン・タイムアウト・二重送信、**発信ボタンの連打**、通話中のリロード・複数タブ・ブラウザを閉じる
- **観測・監査**：障害時に「誰が・どの組織で・どの通話に・何が起きたか」を追えるか、重要操作（DNC・ロール変更・エクスポート・発信・録音・AI の書き込み・設定変更）が監査され、一般オペレーターが改ざんできないか
- **その他**：依存の脆弱性、Git 履歴を含むシークレット、コンテナの non-root、開発用設定が本番で有効になっていないか

## バグを見つけたら
Reproduce → Root Cause → **失敗する回帰テスト** → RED 確認 →（修正は原則 Claude Code に戻す）→ GREEN → 回帰一式 → 記録。
セキュリティの問題は修正に必要な最小限の再現にとどめる。既存テストを弱めない。問題でないと判断したものも理由を書く（False Positive）。

## Finding の形式
`ID / Severity / Component / Description / Impact / Reproduction / Root Cause / Regression Test / Fix / Verification / Residual Risk`

## 最終レポート
Executive Summary・Test Scope・Baseline・Findings by Severity・Critical Invariants・DNC・Tenant Isolation・Concurrency・Telephony Failure・AI Safety・Prompt Injection・Security・Performance・Observability・Fixed Issues・Remaining Risks・Production Blockers・**Production Recommendation（GO / GO WITH CONDITIONS / NO-GO）**。
DNC 迂回・テナント漏えい・二重発信・認証迂回・冪等性の破綻・制御できない発信が1つでも残れば、Production Ready と言わない。
