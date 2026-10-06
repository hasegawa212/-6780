# SECURITY — 脅威モデル（L）とセキュリティ要件

基準：OWASP ASVS（最新版を Phase 15 で確認）＋ OWASP Top 10 for LLM Applications。

## L. 脅威モデル（STRIDE）

| 対象 | 脅威（STRIDE） | 具体例 | 対策 | 検証 |
|---|---|---|---|---|
| 認証 | S なりすまし | 共有トークンの漏えいで誰でも発信できる（**現行で実際に起きた：トークンがチャットに貼られた**） | 個人アカウント＋Cookie セッション。共有トークンは廃止。API キーは用途ごとに発行し、ハッシュで保存して失効できるようにする | 認証テスト・セッション固定の対策テスト |
| テナント境界 | I 情報漏えい | 組織 A のユーザーが組織 B の顧客を見る | 全クエリに org スコープ＋PostgreSQL RLS（二重） | 結合テスト「別テナントのデータを取れない」 |
| 電話 | E 権限昇格・コスト濫用 | 発信 API を連打して課金を膨らませる／大量の迷惑電話 | RBAC・レート制限（ユーザー/組織/キャンペーン/宛先/プロバイダ）・1日上限・同時通話数・予算上限 | 上限超過のテスト |
| Webhook | S・T 改ざん | 偽の「通話終了」「拒否」を送り込む／同じイベントの再送 | 署名とタイムスタンプの検証・`(provider, event_id)` での重複排除・生データ保存 | 署名不正 403・重複イベントの冪等テスト |
| AI のツール呼び出し | E | モデルが他人の contact_id を指定してデータを読む | ID はサーバー側でセッションから注入。モデルには ID を選ばせない。書き込み系は policy 検査 | Tool Gateway のテスト |
| プロンプトインジェクション | T | 顧客が「前の指示を無視して…」と話す／CRM のメモやナレッジに指示文が混ざる | 顧客の発言・CRM・ナレッジは**信頼しないデータ**として区切って渡す。安全規則とツール権限はコードで強制し、モデルの判断に任せない | AI Eval のインジェクションシナリオ |
| CRM | I | エクスポートで個人情報を大量に持ち出す | ADMIN 以上だけ・エクスポート操作も監査ログ・件数上限 | RBAC テスト |
| 録音 | I | 録音・文字起こしの漏えい | 既定 OFF。ON なら同意の告知・保存期間で自動削除・暗号化ストレージ | 保存期間ジョブのテスト |
| 管理画面 | R 否認 | 抑止の解除を誰がしたかわからない | 追記専用の監査ログ（実行者・操作・対象・変更前後・request_id） | 監査ログのテスト |
| 可用性 | D | Webhook の洪水・AI プロバイダの停止 | レート制限・キューでの吸収・サーキットブレーカー・AI が止まったら人につなぐ | 負荷試験（シミュレーター） |

## 実装する対策（チェックリスト）

- [x] RBAC（OWNER / ADMIN / MANAGER / OPERATOR / VIEWER）を **API で**強制（UI で隠すだけにしない）— `apps/api`（Phase 3。発信・通話・結果まで）
- [x] テナント分離：アプリの org スコープ＋RLS（Phase 2・3）
- [x] CSRF：セッションに紐づくトークン（`X-CSRF-Token`）＋SameSite=Lax＋JSON 必須（ADR-0013）
- [ ] XSS：React の自動エスケープ＋CSP（`default-src 'self'`、inline script 禁止）。現行 PWA の `textContent` 方針を引き継ぐ
- [ ] SQL インジェクション：Drizzle のパラメータ化のみ。生 SQL はレビュー必須
- [ ] SSRF：外向き通信先は許可リスト（プロバイダの API ドメインのみ）
- [ ] 入力検証：Zod で全入口（HTTP・Webhook は済。ツール引数・CSV は未実装）
- [x] セキュリティヘッダー：HSTS（Secure 時）・CSP・X-Content-Type-Options・Referrer-Policy・Permissions-Policy（API）
- [ ] シークレット：環境変数／Fly secrets のみ。リポジトリには `.env.example`（ダミー値）だけ
- [ ] PII のマスキング：ログでは電話番号を `+8190****5678` 形式にする。会話の本文はログに出さない
- [x] トークンの保存：**ブラウザの localStorage に認証情報を置かない**（HttpOnly Cookie。CSRF トークンだけを JS が持つ）
- [ ] 依存の脆弱性スキャン：CI で `pnpm audit` ＋ Dependabot
- [ ] Webhook の署名検証：mock は済（HMAC＋5 分）。Twilio（X-Twilio-Signature）・OpenAI（webhook-id / webhook-timestamp / webhook-signature）は Phase 11・12
- [ ] ログイン試行のレート制限・アカウントロック（Phase 16。**本番前に必須**）

## 現行システムで直ちに対応すべきこと（新システムとは別に）
1. チャットに貼られた `TWILIO_AUTH_TOKEN`・`TAC_OUTBOUND_TOKEN`・OpenAI キーの**再発行**（ローテーション）。
2. `/tac/app` のトークンが端末の localStorage に平文で残る。端末を紛失したら即ローテーションする運用にする。
