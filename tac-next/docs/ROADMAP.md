# ROADMAP — 実装計画（O）とリスク一覧（P）

## O. 実装ロードマップ（縦切り＝Vertical Slice）

最初の縦切りは **Lead → Queue → Mock Call → Outcome → Follow-up**。DB だけ、UI だけを先に全部作ることはしない。

| Phase | 内容 | 完了の条件 | 状態 |
|---|---|---|---|
| 0 | リポジトリ・ツール（pnpm / TS strict / Biome / Vitest / CI） | `pnpm check` が通り、CI がグリーン | **完了（本 PR）** |
| 1 | ドメインモデル：電話番号・状態機械・Safety・抑止・結果分類・発信時間帯・スコアの説明 | 必須ドメインテスト 1・3・5・6・7・8・9・10 を純粋ロジックとして通す | **完了（本 PR）** |
| 1.5 | アプリケーション層：CreateCallUseCase（冪等性・ポリシー検査）・RecordOutcome（フォローアップ・抑止）・Mock Telephony | 縦切りがインメモリで端から端まで動く | **完了（本 PR）** |
| 2 | DB：Drizzle スキーマ・マイグレーション・RLS・Repository 実装（PGlite で結合テスト） | 必須テスト 4（テナント分離）を Postgres で証明 | 次 |
| 3 | 認証・組織・RBAC（Cookie セッション・CSRF） | 権限ごとの API テスト | |
| 4 | 顧客・リード：一覧・検索・カーソルページング・CSV 取り込み/出力 | 取り込みウィザードの結合テスト | |
| 5 | 抑止・法令ポリシーの永続化と管理 API | 抑止の解除に理由と監査ログが要る | |
| 6 | キュー（Postgres ベース）・worker・再試行ポリシー | 時間外はキューに入っても発信しない | |
| 7 | Mock Telephony の HTTP シミュレーター（Webhook の順序入れ替え・重複を再現） | 順序の入れ替えで状態が後退しない | |
| 8 | Web UI（Next.js PWA）：Dashboard / Calls / Leads / Follow-ups / Call Workspace | 重要な E2E が通る | |
| 9 | 結果・フォローアップの UI と自動生成 | 結果 → フォローアップの E2E | |
| 10 | Twilio アダプタ（現行の番号・KYC を引き継ぐ） | コントラクトテスト＋staging で実通話 1 件 | |
| 11 | リアルタイム AI 音声（ConversationRelay → OpenAI Realtime SIP） | AI Eval の合格（DNC の取りこぼし 0） | |
| 12 | 人への引き継ぎ（Take Over / Mute / Resume / Transfer） | 必須テスト 8 を E2E で | |
| 13 | 分析（営業・音声・AI の KPI） | ダッシュボードの数値が集計テストと一致 | |
| 14 | 観測（OpenTelemetry：request_id / trace_id / call_id / conversation_id / organization_id） | 1通話をトレースで端から追える | |
| 15 | セキュリティ強化（ASVS チェック・CSP・ZAP） | 重大な指摘 0 | |
| 16 | E2E・負荷・AI Eval を CI に | main で自動実行 | |
| 17 | デプロイ（staging → production）・バックアップの復元テスト・Runbook | 本番前チェックリストの完了 | |

### 現行システムからの移行
- 現行 TAC（Python）は、新システムの Twilio アダプタ（Phase 10）が staging で動くまで本番で使い続ける。
- 移行するデータ：`dnc.txt`（**最優先・欠落させない**）→ `suppression_entries`、`calls.jsonl` → `calls` / `call_events`、
  `screenings.jsonl` → `lead_scores`。移行スクリプトは件数照合のテストつきで作る。
- 番号の Voice Webhook を新システムへ向ける切り替えは、1 番号ずつ行い、すぐ戻せるようにする。

## P. リスク一覧

| リスク | 起こりやすさ | 影響 | 対策 | 検知 | 担当 |
|---|---|---|---|---|---|
| 法令違反（名乗り不足・再勧誘） | 中 | 致命的 | DisclosurePolicy で発信を止める・抑止の強制・法務確認 | blocked の記録・監査ログ | Owner |
| 個人情報の漏えい | 中 | 致命的 | RLS・RBAC・PII マスキング・エクスポートの監査 | 監査ログ・異常なエクスポートのアラート | Security |
| AI のハルシネーション | 高 | 高 | ナレッジにない内容は断定しない規則・引き継ぎ・AI Eval | 会話の抜き取り確認・Eval の回帰 | AI |
| 二重発信 | 中 | 高 | Idempotency-Key・`calls(org_id, idempotency_key)` の一意制約・UI の二重クリック防止 | 同じ番号への短時間の重複発信アラート | Backend |
| DNC の取りこぼし | 中 | 致命的 | ルール＋AI の両方で検知（取りこぼさない側に倒す）・発信直前の canContact | Eval・抑止後の発信 0 件を監視 | Compliance |
| プロバイダの停止 | 中 | 高 | アダプタの差し替え・サーキットブレーカー・AI が止まったら人につなぐ | ヘルスチェック・エラー率 | SRE |
| 遅延（音声） | 高 | 中 | speech-to-speech・つなぎの一言・地域（nrt） | time_to_first_audio の p95 | Voice |
| コストの暴走 | 中 | 高 | 1日上限・同時通話数・通話の最長時間・予算上限 | 費用メトリクスのアラート | Owner |
| データの漏えい（ログ） | 中 | 高 | ログに PII を出さない・マスキングのテスト | ログ走査 | SRE |
| プロンプトインジェクション | 高 | 中 | 入力を信頼しないデータとして扱う・ツール権限をコードで強制 | Eval のインジェクションシナリオ | AI |
| テナント間の漏えい | 低 | 致命的 | アプリのスコープ＋RLS の二重化 | 結合テスト | Backend |
| Webhook の重複・順序の入れ替え | 高 | 中 | `(provider, event_id)` での重複排除・終端状態を後退させない | 重複率のメトリクス | Backend |
| 移行時の DNC の欠落 | 低 | 致命的 | 件数照合のテスト・移行後の突き合わせ | 移行レポート | Owner |

## Definition of Done（全体）
ユーザーが認証できる／リードを取り込み・管理できる／抑止中の相手に発信できない／発信できる／
Mock プロバイダが動く／設定すれば実プロバイダのアダプタが動く／通話のライフサイクルが保存される／
結果が保存される／フォローアップが動く／有効にすれば AI 音声が動く／人への引き継ぎが動く／監査ログがある／
分析が動く／重要な E2E が通る／セキュリティ対策が検証済み／観測が動く／本番ビルドが成功する／デプロイ手順書どおりに動く。
