# RUNBOOK — 障害対応

**状態：骨組みのみ**（実際の手順は API・worker・観測ができてから、staging で確かめたうえで書く。確かめていない手順を書かない）。

## 共通の流れ
Contain（止める）→ 影響の特定 → 調査 → 根本原因 → 回帰テスト → 修正 → 検証 → ポストモーテム → 再発防止

## 最初の一手（すべての重大事故で共通）
1. **全発信を止める**：組織の全発信停止スイッチ（管理画面）または `OUTBOUND_CALLS_ENABLED=false` で再起動（設定のゲート）
2. 監査ログと通話記録を保全する（削除・上書きしない）
3. 影響した通話・顧客を `call_id` / `organization_id` で特定する

## シナリオ（手順は Phase 15・18 で記入）
| シナリオ | 最初にやること | 状態 |
|---|---|---|
| 想定外の発信 | 全発信停止 → 対象の通話を特定 | TODO |
| DNC 事故（抑止中の相手に発信） | 全発信停止 → 抑止の状態と判定ログを保全 → 法務へ連絡 | TODO |
| 個人情報の漏えいの疑い | 認証情報の失効 → アクセスログの保全 | TODO |
| 電話・AI・DB の障害 | 新規発信を止め、通話中は人へ引き継ぐ | TODO |
| コストの急増 | 全発信停止 → 予算の確認 | TODO |
| Webhook の滞留 | 受信の状態確認 → 重複排除が効いているか確認 | TODO |
| 認証情報の漏えい | ローテーション（Twilio・OpenAI・セッション） | TODO |

## 現行 TAC（telegram-ai-bot/tac）
現行の本番は別システム。手順は `telegram-ai-bot/tac/DEPLOY.md`。現行の未対応事項は `QA_REPORT.md` と `RISK_REGISTER.md` の R-13・R-14。

## Twilio の番号が届いたら（Phase 11、ADR-0015）
**状態：手順のみ（未実施）**。コードとテストは揃っている（偽の Twilio に対するテスト・公式 SDK と署名が一致すること）。
実際の Twilio とはまだ一度も通信していない。★ はオーナーの承認が要る手順。

### 0. 前提（番号とは別に必要なもの）
- tac-next の staging がまだ無い（Phase 18）。★ Fly のアプリ・PostgreSQL 16 の用意、★ マイグレーション 0001〜0005 の適用（`DATABASE.md`「運用」）
- 担当者の電話番号（最初の発信先。E.164）

### 1. Twilio コンソール
1. Regulatory Bundle が **Twilio-approved** になったことを確かめる（メールが届く）
2. ★ 日本のローカル番号を購入し、その Bundle を紐づける（費用が発生する）
3. 番号の Voice の着信 Webhook は**変えない**（現行 TAC の番号と混ぜない。新しい番号を tac-next 専用にする）

### 2. staging の設定（★ 本番シークレットの変更にあたる）
```bash
fly secrets set -a <tac-next-staging> \
  APP_ENV=staging TELEPHONY_PROVIDER=twilio \
  TWILIO_ACCOUNT_SID=AC… TWILIO_AUTH_TOKEN=… \
  TWILIO_AGENT_NUMBER=+81… PUBLIC_BASE_URL=https://<tac-next-staging>.fly.dev
# OUTBOUND_CALLS_ENABLED はまだ false のまま（既定）
```
- キャンペーンの `caller_id_e164` を購入した番号にする
- 起動ログが `telephony=twilio, outbound=false` であること。署名のない `POST /v1/webhooks/twilio` が 401 になること

### 3. 実通話 1 件（★ オーナーの承認が要る。相手は社内の担当者の携帯だけ）
1. テスト用の組織・キャンペーン・連絡先を作る（連絡先の番号は**社内の協力者の番号**。実在のお客様は使わない）
2. ★ `OUTBOUND_CALLS_ENABLED=true` にして再起動
3. 画面から 1 件だけ発信 → 担当者の電話が鳴る → 出る → 協力者の電話が鳴る → 名乗りが流れる → つながる → 切る
4. 確かめること：通話の状態が DIALING → RINGING → IN_PROGRESS → ENDED と進む／`webhook_events` に電話番号が保存されていない／
   Twilio の通話ログでお客様側の発信が 1 本だけ／担当者が出ない・協力者が出ない・話し中のときの状態
5. 終わったら `OUTBOUND_CALLS_ENABLED=false` に戻す。ADR-0015 の UNKNOWN を、観察した結果で更新する

### 4. 失敗したとき
- すぐ `OUTBOUND_CALLS_ENABLED=false`（設定のゲート）。通話中なら Twilio コンソールで通話を終了する
- 発信が REQUESTED のまま残った（Twilio から状態通知が来ない）：その番号は回線上の通話としてふさがる（fail closed）。
  1 分ごとの照合（ADR-0016）が Twilio の通話一覧と突き合わせ、見つかれば回収、15 分見つからなければ FAILED にして番号を解放する。
  ログの `reconcile: AMBIGUOUS=…` / `ERROR=…` が続くときは、Twilio の通話ログで発信の有無を確かめる
- 署名の不一致（401 が続く）：`PUBLIC_BASE_URL` が Twilio に渡した URL と一字一句同じか（`https`・ホスト名・末尾の `/` なし）

