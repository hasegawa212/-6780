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

## 2026-10-09 AI 架電の法令対応（ブランチ `claude/tac-ai-call-compliance`、未デプロイ）
- 調査で判明：自動フォローの AI 架電が ①AI と名乗らない ②勧誘に先立つ名乗りを流さない ③通話中の拒否を DNC に入れない
- 修正：接続前に固定文で名乗り（AI・事業者名・商品・勧誘目的）／名乗りの設定が無ければ発信しない／
  通話中の拒否をサーバーのルールで判定して本体の DNC に登録し固定文で切る／多忙は DNC に入れず切る／
  さくらが DNC に登録できない設定なら AI に掛けさせない
- テスト 497 本（+29）。設定の追加が必要（`tac/DEPLOY.md`「AI 自動フォロー架電の必須設定」）

## 2026-10-09 生活意識調査モード「ライフパートナー」（ブランチ `claude/tac-lifepartner-survey`、既定 OFF・未デプロイ）
- 実施事業者 株式会社ジャパンマネジメント。金融リテラシー・保険の見直しの意識調査（6 問＋同意 2 問）
- 決まった質問を Twilio の音声認識で聞き、同意・撤回・拒否はサーバーのルールで判定（LLM を使わない）
- 調査・保険の案内・資料の案内の同意を別々に記録。保険は同意者の一覧を登録済みの保険代理店へ渡すだけ
- テスト 531 本（+34）。（名称は P0 で全経路「ライフパートナー」に統一）

## 2026-10-09 ライフパートナー P0（ブランチ `claude/tac-lifepartner-p0`、未デプロイ）
- **名称を統一**：お客様が聞く AI の名乗りを全経路で「AI音声案内担当、ライフパートナー」に（着信の受付・不動産の AI 架電・調査）。
  名称は `tac/branding.py` の 1 か所。関数名・ブランチ名など社内の識別子は互換性のため変えていない
- **質問のスキップ**：「答えたくない」「パスで」「飛ばして」はその質問だけ `SKIPPED` にして続ける（冒頭で言われたら断り）
- **第三者提供の明示**：保険の案内の同意の質問で「お名前とお電話番号を保険代理店にお伝えする」ことを告げてから同意を取る
- **緊急停止スイッチ**：`GET/POST /tac/kill-switch`（トークン必須）。再起動なしで手動・自動フォロー・調査の全発信を
  唯一の発信口 `outbound._create_call` の手前で止める。状態ファイルが壊れていれば停止扱い。preflight の critical 項目
- `fly.toml` に `TAC_KILL_SWITCH_FILE`・`TAC_SURVEY_FILE`・`TAC_SURVEY_LIST_FILE` を /data で追加（調査の記録が再デプロイで消えないように）
- テスト 546 本（+15）

## 2026-10-09 ライフパートナー P1-a（ブランチ `claude/tac-lifepartner-p1a`、未デプロイ）
- **CRM（SQLite・WAL）** `tac/lp_db.py`：customers / contact_permissions / surveys / survey_responses / interest_profiles /
  call_attempts / appointments / dnc_entries / audit_logs / call_sessions。`survey_store` が JSON と CRM の両方に書く
- 同意は目的ごと（survey / insurance_info / material_info）に、状態・取得時刻・撤回時刻・説明文の版・証跡（CallSid）を記録
- スキップした回答を `SKIPPED` で残す（P0 では JSON に保存するときに落ちていた不具合を修正）
- 撤回：回答と関心を物理的に削除し、許可をすべて WITHDRAWN に。監査ログに電話番号を書かない
- **通話中の会話の状態を DB に保存**（デプロイ・再起動をまたいで続く）。Gather の action に `turn` を付け、
  Twilio の再送で質問が 2 つ進まない（同じ返事を返す）
- **同時架電数の上限**（`TAC_SURVEY_MAX_CONCURRENT`、既定 1）。状態コールバック `/tac/survey/status`（署名検証あり）で解放。
  30 分以上状態が届かない試行は数えない。`Idempotency-Key` で同じ発信の依頼を 1 回だけにする
- 途中で切られた通話は、そこまでの回答だけを `HUNG_UP` で残し、掛け直さない
- CRM が読めないときは掛けない（`STORE_UNAVAILABLE`）。DNC の正本は `dnc.txt` のまま（CRM はミラー）
- 保存期間（`TAC_LP_RETENTION_DAYS`、既定 365 日）を過ぎた回答の削除、稼働中のバックアップ（`python -m tac.lp_db backup`）
- テスト 568 本（+22）

## 2026-10-10 ライフパートナー P1-b（ブランチ `claude/tac-lifepartner-p1b`、未デプロイ）
- **質問を版で管理**（`tac/survey_questions.py`、`TAC_SURVEY_QUESTION_SET`、既定 `v2`）。通話は始めた版のまま最後まで進む。
  回答・同意の記録に版（survey_version / disclosure_version）が残る
- **v2 = A〜E の 5 分野・16 問**：A 生活全般（満足度・負担・見直しへの関心）／B 家計・固定費／C 金融リテラシー／
  D 保険（入っていなければ保険料の質問は飛ばす）／E 将来の暮らし
- 回答の形：はい・いいえ／段階（満足・まあまあ・不満、よくある・ときどき・あまりない、安心・どちらとも・不安）／
  ひとことを分類（負担・大切にしたいこと。どれにも当たらなければ OTHER）。原文は保存しない
- 分からない答えは、質問ごとの言い換えで 1 回だけ聞き直す。相づち（「毎月の支出が気になっていらっしゃるのですね。」など）を挟む
- 「今忙しい」「運転中」などは BUSY として丁寧に切る（冒頭なら断り扱い、途中ならそこまでの回答だけ残す）
- 調査の完了（「アンケートは以上です」）を告げてから、案内の同意を聞く（調査と案内を分ける）
- 割り込み：Gather に `bargeIn="true"`（読み上げ中に答えられる）
- 仕様の状態名 `survey.state_name()`（SURVEY_PERMISSION / QUESTION / INFORMATION_PERMISSION / DECLINED / DNC / END）
- v1（6 問）の流れを確かめる既存テストは、版を v1 に固定（1 行ずつ追加、確認する中身は変えていない）
- テスト 602 本（+34）

## 2026-10-10 ライフパートナー P1-c（ブランチ `claude/tac-lifepartner-p1c`、未デプロイ）
- **管理画面** `GET /tac/lifepartner/`（画面にデータは入れない。トークンはそのタブの sessionStorage だけ）
- API：`/tac/lifepartner/api/me`・`/api/summary`（analyst 以上）・`/api/responses`（viewer 以上、電話番号は下 4 桁）
- 権限：admin / viewer / analyst。`TAC_LP_USERS` に「名前:権限:トークンの sha256」（平文は置かない。
  `python -m tac.lp_admin hash <トークン>`）。形式の崩れた行は誰にも権限を与えない。`TAC_OUTBOUND_TOKEN` は admin
- 集計：対象者数・架電許可確認済み・発信・応答・調査開始・完了・回答拒否・DNC・5 分野の関心・案内の希望（保険／資料）・相談予約・エラー
- **少人数の集計を伏せる**：回答・同意から数える件数が 1〜4 件（`TAC_LP_MIN_CELL`=5）なら伏せ、
  「全体 − その件数」が 1〜4 件のときも伏せる（残りの人が分かるため）。0 件は伏せない
- 閲覧（集計・個別・権限不足の拒否）を監査ログに残す（担当者名つき、電話番号なし）。応答は `Cache-Control: no-store`
- テスト 618 本（+16）。テストが効いているかを、コピーした環境で 5 か所わざと壊して確認（5/5 で失敗を検出）

## 本番データ
- 台帳 90 件ロード済（不在41 / 再調整7 / 要確認37 / 連絡停止5）。発信可 ≒ 48。
- エンジン OFF / 自動運転 OFF（安全既定）。

## ブロッカー（P0・人の入力待ち）
- 正式発信元 03 番号（審査中）/ 担当携帯 / 有効な Twilio 認証。
  → 揃うまで実発信はしない。テスト発信はボス自身の携帯宛に1本のみ可能。

## 次の一手
- 本番 go-live 前: `TAC_ENFORCE_CALL_HOURS` / `TAC_DAILY_CALL_CAP` / `TAC_VERIFY_TWILIO_SIGNATURE` を ON。
- 実客投入は 03 番号の審査通過後（現在審査中）。
