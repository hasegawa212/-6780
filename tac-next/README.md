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

## AI と人で開発するとき
- 全 Agent 共通の契約：[`AGENTS.md`](AGENTS.md)／Claude Code の追加指示：[`CLAUDE.md`](CLAUDE.md)
- 手順書（BUILD → BREAK → FIX → VERIFY）：[`docs/AI_WORKFLOW.md`](docs/AI_WORKFLOW.md)
- 決して破れない不変条件：[`docs/CRITICAL_INVARIANTS.md`](docs/CRITICAL_INVARIANTS.md)（`pnpm test:critical`）

## 進捗

最新の状態は [`docs/PROGRESS.md`](docs/PROGRESS.md)、計画は [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)（Phase 0〜18）。

| Phase | 内容 | 状態 |
|---|---|---|
| 0 | Foundation（ツール・CI・検証つき設定） | ✅ DONE |
| 1 | Domain（電話番号・状態機械・Safety・発信ガード＋安全装置・結果・時間帯・スコア） | ✅ DONE |
| — | アプリ層の縦切り（発信・結果・キュー）＋ Mock プロバイダ | ✅ インメモリのみ（**MOCK ONLY**） |
| 2〜18 | DB・認証・UI・実プロバイダ・AI 音声・観測・デプロイ | NOT IMPLEMENTED |

現時点ではまだ **HTTP サーバーも DB も UI もありません**。動くのはドメイン層・アプリケーション層と Mock の電話プロバイダで、
テストで検証しています。

## 使い始める

必要なもの：Node.js 22 以上、pnpm 10（`corepack enable` で使えます）

```bash
cd tac-next
pnpm install
pnpm check        # lint + 型チェック + テスト
pnpm test:critical  # 不変条件のテスト（CI で必須）
pnpm test:mutation  # 安全ルールを反転させるとテストが落ちるか
pnpm build        # 型付きビルド（tsc -b）
pnpm test:watch   # TDD 用（保存するたびにテスト）
```

`.env.example` はダミー値だけです。Phase 2 以降で `docker compose up`・`pnpm db:migrate`・`pnpm db:seed`・`pnpm dev` を追加します。

## 構成

```
tac-next/
  packages/
    domain/       純粋なドメインロジック（I/O なし）
    application/  ユースケースとポート（Repository / TelephonyProvider / SafetyControls / Clock）
    telephony/    TelephonyProvider のアダプタ（現在は Mock だけ）
    config/       起動時に検証する設定（Zod）
  docs/
    PRODUCT.md            プロダクトビジョン・ワークフロー・ジャーニー
    EXISTING_APP_AUDIT.md 既存アプリの監査（OBSERVED / INFERRED / UNKNOWN / PROPOSED）・ギャップ分析
    ARCHITECTURE.md       構成・ERD・API・シーケンス図
    DOMAIN.md             ドメインモデルと状態機械
    VOICE.md              音声アーキテクチャ・電話シミュレーター
    AI_AGENT.md           AI エージェント（プロンプトの層・ツール・人への引き継ぎ）
    SECURITY.md           脅威モデル（STRIDE）
    COMPLIANCE.md         法令対応の設計と本番前チェックリスト
    TESTING.md            テスト戦略と必須テスト
    IMPLEMENTATION_PLAN.md Phase 0〜18・タスクグラフ・リスク一覧
    DECISIONS.md          技術判断（ADR）の一覧
    PROGRESS.md           進捗（作業を再開するときはここから読む）
    adr/                  ADR 本文
```

## 開発のルール
- **TDD**：失敗するテストを先に書き、失敗を確認してから実装する。テストを消したり、型チェックや lint を無効化したりしてグリーンにしない。
- 依存の向きは `apps → application → domain` の一方向。`domain` は何にも依存しない。
- シークレットはコミットしない。`.env.example` にはダミー値だけを置く。
