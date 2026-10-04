# TAC Next — AI + Human ハイブリッド営業支援プラットフォーム

現行の TAC（`telegram-ai-bot/tac/`、Python/Flask）を参照実装として監査し、ゼロから設計し直している次世代版です。
「電話をかける画面」ではなく、**リードの取り込みからフォローアップと学習までの営業ループ全体**を、
安全・法令順守・正確さを最優先にして支えることを目的にしています。

> ⚠️ **法務確認について**：電話による勧誘は、地域・商材・相手（事業者か消費者か）によって規制が異なります
> （日本の特定商取引法・個人情報保護法、米国の TCPA など）。このソフトウェアは法的助言ではありません。
> **本番で使う前に、必ず弁護士など専門家の確認を受けてください。**（`docs/COMPLIANCE.md`）

## 設計の原則
1. 優先順位：**安全・法令順守 → 正確さ → 信頼性 → オペレーターの使いやすさ → 成約率 → AI の自律性**
2. 既定は **Human-dialed**（人がタップして1件ずつ発信）。AI が話す発信は Feature Flag かつ同意済みの相手だけ（`docs/adr/0003`）
3. 抑止（DNC）は DB のフラグではなくサービス。発信の直前に必ず `canContact` を通す
4. 電話・AI のプロバイダは差し替え可能なアダプタ。テストでは本物の電話をかけない

## 進捗

| Phase | 内容 | 状態 |
|---|---|---|
| 0 | リポジトリ・ツール | ✅ |
| 1 | ドメインモデル（電話番号・状態機械・Safety・抑止・結果・時間帯・スコア） | ✅ |
| 1.5 | ユースケース（発信・結果記録）＋ Mock Telephony：最初の縦切りがインメモリで動く | ✅ |
| 2〜17 | DB・認証・UI・実プロバイダ・AI 音声・観測・デプロイ | 未着手（`docs/ROADMAP.md`） |

現時点ではまだ **HTTP サーバーも DB も UI もありません**。動くのはドメイン層・アプリケーション層と Mock の電話プロバイダで、
テストで検証しています。

## 使い始める

必要なもの：Node.js 22 以上、pnpm 10（`corepack enable` で使えます）

```bash
cd tac-next
pnpm install
pnpm check        # lint + 型チェック + テスト
pnpm test:watch   # TDD 用（保存するたびにテスト）
```

`.env.example` はダミー値だけです。Phase 2 以降で `docker compose up`・`pnpm db:migrate`・`pnpm db:seed`・`pnpm dev` を追加します。

## 構成

```
tac-next/
  packages/
    domain/       純粋なドメインロジック（I/O なし）
    application/  ユースケースとポート（Repository / TelephonyProvider / Clock）
    telephony/    TelephonyProvider のアダプタ（現在は Mock だけ）
  docs/
    AUDIT.md        既存アプリの監査（OBSERVED / INFERRED / UNKNOWN / PROPOSED）
    DOMAIN.md       ドメインモデルと状態機械
    ARCHITECTURE.md 構成・ERD・API・音声・AI・シーケンス図
    SECURITY.md     脅威モデル（STRIDE）
    COMPLIANCE.md   法令対応の設計と本番前チェックリスト
    TESTING.md      テスト戦略と必須ドメインテスト
    ROADMAP.md      実装計画・リスク一覧・Definition of Done
    adr/            技術判断の記録
```

## 開発のルール
- **TDD**：失敗するテストを先に書き、失敗を確認してから実装する。テストを消したり、型チェックや lint を無効化したりしてグリーンにしない。
- 依存の向きは `apps → application → domain` の一方向。`domain` は何にも依存しない。
- シークレットはコミットしない。`.env.example` にはダミー値だけを置く。
