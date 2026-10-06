# RISK_REGISTER — リスク台帳

Likelihood / Impact は 低・中・高。Severity は CRITICAL / HIGH / MEDIUM / LOW。
Owner の「オーナー」は株式会社 Martial Arts の意思決定者（人）を指す。Agent はリスクを受け入れる判断をしない。
CRITICAL / HIGH は、修正と独立した再検証（Codex 等）が終わるまで消さない。終わったら Status を Closed にし、証拠を書く。

| ID | Risk | Likelihood | Impact | Severity | Mitigation | Detection | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| R-01 | DNC の迂回（抑止中の相手へ発信） | 中 | 高 | CRITICAL | 発信直前の `isContactable`（fail closed）＋ TOCTOU の再確認、拒否→抑止、画面でも発信不可。DB の永続化・RLS・アプリのロールから抑止を消せない（Phase 2 で対策） | `test:critical`・mutation smoke／本番は「抑止中の番号への発信試行」メトリクス（Phase 15） | 開発 | Open（アプリ層・DB 層は対策済み、API/worker は未実装） |
| R-02 | 二重発信 | 中 | 高 | CRITICAL | 冪等キー＋「番号ごとに回線上1件」契約、画面のキー保持、タイムアウト時に自動再送しない | `adversarial.test.ts`／同じ番号への短時間の重複発信アラート（Phase 15） | 開発 | Open（DB の一意制約は対策済み。実 PG の同時実行は CI で確認、プロバイダ側は Phase 11） |
| R-03 | テナント漏えい | 中 | 高 | CRITICAL | 全クエリの org スコープ＋PostgreSQL RLS、ID はサーバー側で注入 | 結合テスト「別テナントを取れない」（DB 層は `tenant-isolation.test.ts`、API は Phase 3） | 開発 | Open（DB・API・認証は対策済み。ユーザー管理・ログインの制限は未実装） |
| R-04 | プロンプトインジェクション | 高 | 高 | HIGH | 信頼しないデータとして区切る、安全規則とツール権限はコードで強制、書き込みツールは Policy＋Audit | AI Eval の adversarial（Phase 12・17） | 開発 | Open（AI 未実装） |
| R-05 | ハルシネーション（価格・契約条件の捏造） | 高 | 中 | HIGH | ナレッジにないことは答えず人に回す、価格はツール経由のみ | AI Eval（unknown / pricing） | 開発 | Open（AI 未実装） |
| R-06 | 電話プロバイダの障害 | 中 | 中 | MEDIUM | サーキットブレーカー、失敗は FAILED にして自動再送しない、オペレーターに表示 | プロバイダエラー率アラート（Phase 15） | 開発 | Open |
| R-07 | AI プロバイダの障害 | 中 | 高 | HIGH | SYSTEM_FAILURE → 人へ引き継ぎ or 安全に終了、客を放置しない | 通話中の AI エラー率アラート | 開発 | Open（AI 未実装） |
| R-08 | 応答遅延（AI の無言） | 中 | 中 | MEDIUM | 無音タイムアウト、遅延時のつなぎ文言、上限超過で人へ | 音声 p95 レイテンシ | 開発 | Open |
| R-09 | コストの暴走（大量発信・AI 課金） | 中 | 高 | HIGH | 1日上限・同時通話数・予算・全発信停止・設定ゲート（既定 OFF）。ADR-0009（null=無制限の扱い）はオーナー判断待ち | コスト急増アラート | オーナー＋開発 | Open（上限の競合は組織ロックで対策。実 PG の証拠は CI。ADR-0009 は判断待ち） |
| R-10 | Webhook の重複・順序入れ替え・偽造 | 高 | 中 | HIGH | `reconcileProviderStatus`（後戻りしない）、`(provider, event_id)` で重複排除、署名＋タイムスタンプ検証 | Webhook エラー率／署名不正の件数 | 開発 | Open（受信処理は Phase 7） |
| R-11 | PII の漏えい（ログ・エクスポート・画面） | 中 | 高 | HIGH | ログで電話番号をマスク、本文を出さない、エクスポートは権限＋監査、localStorage に認証情報を置かない | ログの PII スキャン（Phase 15） | 開発 | Open |
| R-12 | DB 障害 | 低 | 高 | HIGH | 発信判定は DB が答えられなければ止める（fail closed）、バックアップと復元テスト | DB 接続エラーアラート／復元リハーサル（Phase 18） | 開発＋オーナー | Open（DB 未実装） |
| R-13 | 現行 TAC（本番）の DNC 迂回 | 中 | 高 | CRITICAL | `hasegawa212/-6780` PR #132 で修正済み・**未デプロイ** | — | オーナー（`fly deploy` の判断） | Open |
| R-14 | 現行 TAC（本番）の認証なしルート・安全設定 OFF | 中 | 高 | HIGH | QA_REPORT の QA-TAC-10〜15 | — | オーナー | Open |
| R-15 | 勧誘に関する法規制（再勧誘禁止・告知・録音同意） | 中 | 高 | HIGH | COMPLIANCE.md の方針、曖昧な断りは抑止せず WRAP_UP（〔要法務確認〕） | 監査ログ | オーナー（法務確認） | Open（LEGAL REVIEW REQUIRED） |
