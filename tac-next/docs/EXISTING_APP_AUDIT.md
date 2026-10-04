# EXISTING_APP_AUDIT — 既存アプリの監査（A / B / C / E）

## 調査の方法と限界

| 情報源 | 結果 | 区分 |
|---|---|---|
| 本番 `https://tac-martial-arts.fly.dev/tac/app` | 2026-10-04 に2回試行（curl・WebFetch）。どちらもこの開発環境の通信制限で **接続できなかった** | — |
| 本番 `/tac/app` の HTML・JS・manifest | main の `mobile_app.py` が生成する1枚の HTML。fetch 先・localStorage キー・manifest はソースから確認 | **OBSERVED**（ソース経由。本番が main と同じ版である前提） |
| リポジトリ `telegram-ai-bot/tac/`（main `4c66b4a`）と `sales-rank/` | 全モジュール・全ルート・全テストを読んだ | **OBSERVED** |
| 依頼文に挙がった機能（スマートリスト・フォロー・顧客詳細・スコア順など） | GitHub 上の **どのブランチにも存在しない**。利用者の手元の別版で、それが本番に出ている可能性がある | **INFERRED**（実装は **UNKNOWN**） |

> 本番が手元の別版から出ている場合、この監査と本番の内容は違うことがある。差分は Phase 4 の前に利用者の手元の版で確認する。

## A. 既存アプリの監査

### 構成（OBSERVED）
- Flask＋gunicorn（`--workers 1 --threads 8`）の **1 プロセス**、Fly.io の **1 マシン**（nrt）。会話の状態はプロセスのメモリにある。
- 永続データは Fly ボリューム `/data` 上のファイル：`dnc.txt`（1行1番号）、`calls.jsonl`、`screenings.jsonl`。DB はない。
- 音声の経路が3つある：①`<Gather>`（`/tac/voice`）②ConversationRelay（`/tac/voice-relay`＋WS `/tac/relay`、本番で使用中）
  ③Twilio Media Streams ⇄ OpenAI Realtime（`realtime.py`、別の FastAPI アプリ、ツールなし）。
- 認証は共有トークン 1 つ（`TAC_OUTBOUND_TOKEN`）。ユーザーの区別がない。
- 発信は Twilio の Conference を使ったブリッジ：相手に発信 → 保留 → 担当者の電話を鳴らす（1件ずつの手動発信）。

### 良い点（引き継ぐ）
- 発信前のガードが揃っている：抑止（DNC）→ 時間帯 → 1日上限 → 名乗りの設定不足、の順で止め、止めた理由も `blocked` として記録する。
- 「拒否」を選ぶと自動で抑止に入る。断られた相手への再勧誘を仕組みで防ぐ考え方が最初からある。
- CSV 出力で式の実行を無効化している（CSV インジェクション対策）。HTML は全てエスケープしている。
- sales-rank の3軸（会える確度・属性の質・審査適性）。現場の失敗（年収650万・勤続9年の案件が C に埋もれた）から作られた、説明できる順位付け。
- テストが厚い（pytest 約 200 件）。法令まわりの挙動がテストで固定されている。

### 重大な問題（OBSERVED、file:line は `telegram-ai-bot/tac/`）

| # | 問題 | 根拠 | 影響 |
|---|---|---|---|
| 1 | **DNC の表記ゆれで、拒否した相手に発信できてしまう**。`/tac/dnc` は国内表記のまま保存し（`09012345678`）、発信時は E.164（`+819012345678`）で照合する | `server.py:429-435`、`dnc.py:23` | 再勧誘の禁止に違反しうる（**本 PR で現行システムも修正**） |
| 2 | 同時通話で別の人の会話にツールが作用する。`_active_sid` がインスタンス全体で1つ | `connector.py:52,55,100,268` | 引き継ぎや聞き取り結果が、別の発信者の番号で記録される |
| 3 | 担当者が出ないと、相手が保留音のまま放置される（Conference にタイムアウトがない） | `outbound.py:59-63` | 顧客体験の悪化・通話料 |
| 4 | 担当者として任意の `+` 番号を指定できる（名簿にない番号も通る） | `agents.py:59-60` | トークンが漏れると、任意の2番号をつなぐ不正利用ができる |
| 5 | 認証のないルートがある：`/tac/assist/<sid>`・`/tac/insights`・`/tac/close/<sid>`・WS `/tac/relay` | `server.py:205-223, 32-38` | LLM の費用を勝手に使われる・会話の情報が漏れる |
| 6 | ConversationRelay の引き継ぎで `<Connect>` に `action` がなく、`handoffData` を受け取る先がない | `server.py:458-464, 521` | 「担当者におつなぎします」と言った後、つながらずに切れる可能性 |
| 7 | AI のツール `schedule_callback` が何も保存しない | `tools.py:129-131` | AI が約束した折り返しが、誰にも届かない |
| 8 | 1日上限の判定と記録の間にロックがない。冪等キーもない | `outbound.py:159, 200` | 同時に押すと上限を超える・二重発信 |
| 9 | 通話結果（応答・話し中・留守電）を受け取る StatusCallback がない | `outbound.py:104-106` | 「dialed」は Twilio が受け付けたという意味だけで、つながったかがわからない |
| 10 | 安全装置が既定で OFF（署名検証・時間帯・上限・名乗り） | `config.py:87,94,97,114` | 設定し忘れると無防備になる（本番の fly.toml では署名検証は ON） |
| 11 | 発信者の電話番号と発話内容を標準出力に出している | `server.py:494, 502` | ログ経由の個人情報の漏えい |
| 12 | 台本の遵守チェックが失敗したとき「遵守率 1.0・リスクなし」になる | `operators.py:136-137` | 監査の数字が実態より良く見える |
| 13 | Supabase のナレッジ検索が、存在しないモジュールを import していて動いていない | `memory.py:121` | ナレッジ検索が静かに簡易検索へ落ちている |
| 14 | `/tac/app` は認証トークンを端末の localStorage に平文で保存する | `mobile_app.py:164` | 端末を紛失したら漏えいする |
| 15 | 発信時間帯が固定の UTC オフセット・1時間単位・休日の考慮なし | `calling_hours.py:40-41` | 海外番号も日本時間で判定される |

## B. 機能一覧（Feature Inventory）

| 機能 | 現行の挙動 | 区分 |
|---|---|---|
| 発信（番号入力・担当者選択・発信） | `/tac/app` → `POST /tac/call`。国内表記も E.164 に直す。担当者は名前／番号／自動（ラウンドロビン） | OBSERVED |
| 結果の記録（成約／検討／折り返し／不在／拒否） | 自由入力の文字列として記録。「拒否」で自動的に抑止 | OBSERVED |
| 発信リスト（手動） | 端末の localStorage に番号を保存し、1件ずつ「次へ」 | OBSERVED |
| 記録・集計 | 件数・結果別の件数・直近20件 | OBSERVED |
| 設定 | トークンの入力（端末に保存） | OBSERVED |
| PWA（ホーム画面に追加） | manifest＋apple-mobile-web-app-capable | OBSERVED |
| 抑止（DNC）の管理 | `GET/POST /tac/dnc`（追加・削除） | OBSERVED |
| 運用ダッシュボード | `/tac/console`（HTML） | OBSERVED |
| CSV 出力 | `/tac/calls.csv?from&to` | OBSERVED |
| 着信の AI 応対「さくら」 | ConversationRelay（日本語 Neural2-B）＋Claude。話し方は #124〜#126 で調整済み | OBSERVED |
| 電話5問（仮ランク） | `record_screening` ツール → sales-rank の判定 → JSONL | OBSERVED |
| 人への引き継ぎ | Flex `<Enqueue>`／Studio 実行（ConversationRelay 経路は未完成：問題 #6） | OBSERVED |
| 録音と同意の告知 | 発信の Conference だけ録音できる。告知文あり | OBSERVED |
| 名乗り（勧誘目的の明示） | ON のとき相手が出た直後に `<Say>`。項目が欠けていたら発信しない | OBSERVED |
| 顧客管理（メモ・顧客詳細・連絡履歴・元データ・分類修正） | 利用者の一覧にあるが、リポジトリにはない | INFERRED / UNKNOWN |
| スマートリスト・検索・スコア順・名前順・おすすめ順・保存・リセット・スキップ | 手動リスト・保存・リセット・スキップは OBSERVED。それ以外はリポジトリにない | 一部 OBSERVED、残りは INFERRED / UNKNOWN |
| フォロー（連絡予定・再調整希望・要確認・対応済み） | リポジトリにない | INFERRED / UNKNOWN |

## C. KEEP / IMPROVE / REPLACE / REMOVE / ADD

| 機能 | 業務上の目的 | 問題 | 判定 | 新しい挙動 | 受け入れ条件 | テスト |
|---|---|---|---|---|---|---|
| 1件ずつの手動発信 | 法令順守と顧客体験 | — | **KEEP** | Human-dialed を既定とする（ADR-0003） | キューは候補を出すだけで、自動では掛けない | ユースケース・E2E |
| 発信前ガード（DNC→時間帯→上限→名乗り） | 再勧誘禁止・迷惑防止 | 既定 OFF・表記ゆれ（#1・#10） | **IMPROVE** | Policy として常時評価。発信直前に `canContact` を必ず通す | 全ガードの拒否が理由コードつきで返る | ドメイン・プロパティ |
| 「拒否」で自動抑止 | 再勧誘禁止 | 自由文字列で、ファイル書き込みが失敗しうる | **IMPROVE** | `DO_NOT_CALL` の結果と抑止登録を同一トランザクションで | 結果の保存と抑止が片方だけにならない | 結合 |
| 結果の5ボタン | 現場の記録の速さ | 自由文字列・拡張できない | **IMPROVE** | 分類コード（outcome_code）＋組織のプリセット | 5ボタンはそのまま使え、内部はコード | ドメイン |
| DNC ファイル | 抑止 | 表記ゆれ・非原子的な書き込み・誰がいつ追加したか残らない | **REPLACE** | `suppression_entries`（E164・理由・取得元・解除は Admin＋理由） | 解除に監査ログ | 結合 |
| JSONL の通話記録 | 監査証跡 | 書き込み失敗を無視・O(n)・回転なし | **REPLACE** | `calls`＋`call_events`（Postgres） | blocked も残る | 結合 |
| 共有トークン認証 | 発信 API の保護 | 個人の区別なし・漏えいした・URL に載る | **REPLACE** | ユーザーアカウント＋Cookie セッション＋RBAC | localStorage に認証情報を置かない | API |
| プロセス内の会話状態 | 会話の継続 | 同時通話の取り違え（#2）・水平スケール不可 | **REPLACE** | 会話は DB＋通話ごとに独立したセッション | 2通話を並行しても混ざらない | 結合 |
| 担当者の任意番号指定 | 柔軟さ | 不正利用（#4） | **REMOVE** | 名簿にある担当者だけ | 名簿外は 403 | API |
| 認証なしのルート（assist/insights/close/relay） | 開発用 | #5 | **REMOVE** | 全て認証＋署名検証 | 未認証は 401 | API |
| `<Gather>` 経路と FastAPI の Realtime 経路 | 試作 | 3経路の重複・テストなし | **REMOVE** | 音声は VoiceSession ポートの2アダプタに整理 | — | コントラクト |
| `schedule_callback` のスタブ | 折り返しの約束 | 何も保存しない（#7） | **REPLACE** | `create_follow_up` ツール（実際にフォローアップを作る） | 作られたフォローアップが一覧に出る | ユースケース |
| sales-rank の3軸 | 説明できる順位付け | 重み付けの値がなく貯蓄が効かない | **KEEP＋IMPROVE** | 要素ごとの寄与と理由を返す priority | 「なぜおすすめか」を表示 | ドメイン |
| 電話5問 | 仮ランク | 呼び出しの取り違え | **KEEP** | 会話の QUALIFICATION フェーズの項目にする | — | ドメイン |
| 名乗り | 特定商取引法 | 留守電にも流れる | **IMPROVE** | DisclosurePolicy＋留守電を検知したら流さない | 欠けていたら発信不可 | ドメイン |
| 録音と告知 | 同意 | 着信は録音していないのに告知だけ | **IMPROVE** | RecordingPolicy（既定 OFF・保存期間） | 告知と録音の状態が一致 | ドメイン |
| さくらの話し方 | 顧客体験 | 1つの巨大なプロンプト | **IMPROVE** | Persona 層として版管理 | prompt_version を通話ごとに保存 | AI Eval |
| PWA | iPhone で使う | 認証情報を端末に保存 | **KEEP＋IMPROVE** | Next.js の PWA＋Cookie セッション | — | E2E |
| 運用ダッシュボード・CSV | 可視化・監査提出 | UTC 表示が混在 | **IMPROVE** | 分析画面とエクスポート（ADMIN＋監査） | 組織のタイムゾーンで表示 | E2E |
| — | — | — | **ADD** | マルチテナント・CSV 取り込み・検索とカーソルページング・フォローアップ・次アクション・Call Workspace・人の引き継ぎ操作・冪等性・Webhook の重複排除・観測・AI Eval | IMPLEMENTATION_PLAN.md 参照 | — |

### C-2. 依頼文にあるがリポジトリで確認できない機能（Evidence = INFERRED）

| 機能 | 推定される業務目的 | 判定 | 新しい挙動 | 受け入れ条件 | テスト |
|---|---|---|---|---|---|
| メモ | 通話の文脈を次回に残す | **ADD**（現行で未確認） | `notes`（作成者・時刻つき、編集履歴あり） | 顧客詳細に時系列で出る | API・E2E |
| 顧客詳細・連絡履歴・元データ | 掛ける前に状況を把握 | **ADD** | Contact 詳細：通話・結果・フォローアップ・取り込み元の行 | 1画面で履歴がわかる | E2E |
| 分類修正 | 誤った結果の訂正 | **ADD** | 結果の訂正は新しい記録として残し、元の記録は消さない（監査） | 訂正の前後が監査ログに残る | 結合 |
| スマートリスト・おすすめ順・スコア順・名前順 | 掛ける順番を決める | **ADD** | 保存できるフィルター＋説明可能な priority での並び替え（サーバー側・カーソルページング） | 「なぜおすすめか」が表示される | API・E2E |
| 連続処理・スキップ | 1件ずつテンポよく掛ける | **KEEP＋IMPROVE** | 結果を記録すると次の候補を表示（自動では掛けない）。スキップは理由つき | 自動発信しない | E2E |
| フォロー（連絡予定・再調整希望・要確認・対応済み） | 約束の取りこぼし防止 | **ADD** | `follow_ups.status`：OPEN / DONE / CANCELED＋種別（CALLBACK / RETRY / FOLLOW_UP / APPOINTMENT / HUMAN_REVIEW） | 期日の来たものがキューに出る | ユースケース（実装済み）・E2E |

## D. ユーザージャーニー

[PRODUCT.md](PRODUCT.md) に移動。

## E. ギャップ分析（現行 → 次世代版）

| 営業ワークフロー | 現行（OBSERVED） | 次世代版で必要なもの（PROPOSED） | 埋める Phase |
|---|---|---|---|
| Lead selection | 手で番号を入力、または端末に貼り付けたリスト。順位付けなし（sales-rank は着信の電話5問だけ） | 取り込み・重複判定・説明可能なスコア・次アクション・キュー | 4・6 |
| Calling | Conference のブリッジ。冪等性なし・保留放置あり・共有トークン | 発信ガードの一連の検査・冪等キー・担当者が出ない場合のタイムアウト・個人アカウント | 3・7・11 |
| Conversation | さくら（ConversationRelay）。会話はプロセスのメモリ・同時通話で取り違え | 会話ごとに独立した状態（DB）・状態機械・Safety 優先 | 7・12 |
| Outcome | 自由文字列の5ボタン | 分類コード・同一トランザクションでの抑止 | 10 |
| Follow-up | なし（AI の `schedule_callback` は保存しない） | 結果から自動でフォローアップ・キューへの再投入 | 10 |
| History | JSONL の通話記録（書き込み失敗を無視） | `calls`・`call_events`・監査ログ（追記専用） | 2・7 |
| Suppression | ファイル（表記ゆれのすり抜けは PR #129 で修正） | 抑止サービス・発信直前の再確認・解除は Admin＋理由 | 5 |
| 依頼文にある未確認の機能（スマートリスト・フォロー・顧客詳細・分類修正など） | **INFERRED / UNKNOWN**（リポジトリにない） | 次世代版では Leads・Follow-ups・Contact 詳細として設計済み | 4・9・10 |

> UNKNOWN を OBSERVED にするには、本番の `/tac/app` を利用者の端末で開いた画面のスクリーンショット、
> または利用者の Mac で `curl -s https://tac-martial-arts.fly.dev/tac/app > app.html` で取得した HTML が必要。
