# DESIGN_SYSTEM — デザインシステム

ブランドの性格：**Trustworthy・Professional・Fast・Calm・Modern・Operational**。
現行 TAC の炎色（`#c2410c`）はブランドとして残すが、**状態の色（危険・成功）とは分ける**（炎色＝ブランドのアクセント、赤＝危険・発信禁止）。
実装は `apps/web/app/globals.css`（CSS 変数＋ Tailwind 4 の `@theme`、OS の設定でダーク）。コントラストは E2E の axe で検査（重大な違反 0）。手動のテーマ切り替え・アイコンの選定・Storybook は未実装（ADR-0014）。

## 1. Semantic Tokens
コンポーネントは生の色を使わず、意味のトークンだけを使う。

| トークン | Light | Dark | 用途 |
|---|---|---|---|
| `surface` | `#ffffff` | `#15171a` | 主な面 |
| `surface-muted` | `#f5f5f4` | `#1d2024` | 補助の面・表のストライプ |
| `surface-raised` | `#ffffff` | `#24282d` | ダイアログ・メニュー |
| `text-primary` | `#1c1917` | `#f5f5f4` | 本文 |
| `text-secondary` | `#57534e` | `#a8a29e` | 補足（本文の上で 4.5:1 以上） |
| `border` | `#e7e5e4` | `#2f343a` | 区切り |
| `border-strong` | `#a8a29e` | `#57534e` | 入力欄（3:1 以上） |
| `accent` | `#c2410c` | `#fb923c` | ブランド・主操作 |
| `success` | `#15803d` | `#4ade80` | 接続・保存できた |
| `warning` | `#b45309` | `#fbbf24` | 再接続中・期限切れ |
| `danger` | `#b91c1c` | `#f87171` | 発信禁止・切断・失敗・終了ボタン |
| `info` | `#1d4ed8` | `#60a5fa` | AI の活動・呼び出し中 |
| `focus-ring` | `#2563eb` | `#93c5fd` | フォーカス（2px＋2px のオフセット） |

- ダークモードは採用する（長時間の架電・夜の作業）。**色の反転ではなく**、面の明度の段階を作り直す。OS の設定に従い、手動切り替えも用意する
- 状態は**色だけで表さない**：文字ラベル＋アイコン＋色（`callIndicator` が常にセットで返す）

## 2. タイポグラフィ
- フォント：`"Noto Sans JP", "Hiragino Sans", system-ui, sans-serif`。数字（経過時間・KPI・電話番号）は `font-variant-numeric: tabular-nums`
- サイズ：12 / 14（表・補足）/ **16（本文の基準）** / 18 / 20 / 24 / 30。行間 1.6（本文）・1.3（見出し）
- 見出しは太さで階層をつけ、大きさの段を増やしすぎない。英字の全大文字ラベルは使わない（日本語 UI）

## 3. 余白・角丸・影・線
- 余白：4px 刻み（4・8・12・16・24・32・48）。表の行の高さ：標準 44px・詰め 36px（密度の切り替え）
- 角丸：4（入力欄・バッジ）・8（ボタン・カード）・12（ダイアログ）。過剰な角丸にしない
- 影は浮いている要素（ダイアログ・メニュー・下部固定バー）だけ。面の区別は主に線と面の色で
- **何でもカードにしない**。一覧は表、関連する情報はまとまりと見出しで区切る

## 4. モーション
- 使うのは 状態の変化・操作への反応・位置関係の理解 の3つだけ。120〜200ms、`ease-out`
- `prefers-reduced-motion: reduce` では移動・拡大縮小をやめ、不透明度の変化だけにする
- 画面全体のスピナーは使わない。スケルトンか局所のローディング

## 5. アイコン
線のアイコン1系統（Lucide 等、Phase 9 でライセンスを確認して選定）。意味のある場所にだけ使い、必ず文字ラベルを伴う（アイコンだけのボタンは `aria-label`）。AI のキラキラアイコンは使わない（AI は「AI」と文字で示す）。

## 6. ブレークポイント
| 名前 | 幅 | 方針 |
|---|---|---|
| mobile | < 768px | 下部タブ4つ＋その他。Call Workspace は1カラム＋下部固定の操作バー |
| tablet | 768〜1279px | サイドバーを折りたたみ。Call Workspace は2カラム（右列はタブ） |
| desktop | ≥ 1280px | サイドバー＋3カラムの Call Workspace |

単純に縮小しない。幅ごとに優先順位を変える（モバイルの通話画面は 状態・顧客・文字起こし・主要ボタン を優先）。Visual QA は 1440・1024・768・390px。

## 7. コンポーネント一覧（実際に共通の振る舞いがあるものだけ）
| 部品 | 振る舞い | ロジック |
|---|---|---|
| `CallStatusBadge` | 文字＋アイコン＋色、読み上げ | `callIndicator` |
| `AiActivity` | AI の活動、引き継ぎ後は停止表示、低い確信度の注意 | `aiActivityView`・`aiConfidenceNotice` |
| `SuppressionBanner` | 発信禁止・保存失敗の fail-safe 表示 | `suppressionBanner` |
| `CallButton` | 連打防止・冪等キーの保持・抑止中は出さない | `CallStarter`・`canOfferCall` |
| `OutcomeBar` | 5ボタン＋その他、次にやること | `outcomeButtons`・`nextStepFor` |
| `ConfirmDialog` | 何を・誰に・どうなるか、強い確認は対象名の入力 | `describeConfirmation` |
| `StateView` | Loading / Empty / Error / Offline / Forbidden の出し分けと文言 | `screenState`・`errorCopy` |
| `Transcript` | 話者の区別（お客様 / AI / あなた）、確定前は薄く斜体、検索、自動スクロールは手動スクロール中に止める | Phase 12 |
| `DataTable` | サーバー側の並び替え・絞り込み・カーソルページング・列の調整 | Phase 4 |
| `CommandPalette` | ⌘K、リード検索・発信・フォロー・キャンペーン | `resolveShortcut` |
| `Toast` | 補助の通知のみ（重要な情報はトーストだけにしない） | — |

Storybook は Phase 9 で導入を判断（Visual Regression に使う）。

## 8. アクセシビリティ（WCAG 2.2 AA）
- キーボードだけで主要な流れを完了できる。フォーカスは常に見える（`focus-ring`）。ダイアログはフォーカスを閉じ込め、閉じたら元へ戻す
- コントラスト：本文 4.5:1・大きな文字と UI 部品 3:1
- タップ領域：最小 44×44px（現行の 48px を維持）
- ライブ領域：通話状態は `polite`、切断・発信禁止の保存失敗は `assertive`
- 200% の拡大で横スクロールが出ない（表は横スクロール可）
- 自動テスト（axe）＋手動のキーボードレビュー。スクリーンリーダー（VoiceOver・NVDA）での確認
