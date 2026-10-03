# さくら発信 MAX — 設計書

## 概要
既存の「さくら発信」PWA を6機能で強化する。既存の発信/コンプライアンス機能（DNC・
架電時間制限・1日上限・Twilio署名検証・同意/告知・スクリーニング・レート制限）は
一切壊さない。

---

## A. 📇 スマートリスト（Smart Queue）

### データ設計
- ファイル: `TAC_QUEUE_FILE`（既定 `tac/queue.json`）。JSON 配列。
- 各エントリ:
  ```json
  {"number": "+819012345678", "name": "田中太郎", "area": "茨城", "score": 85, "note": ""}
  ```
- ファイルが無ければ空リスト。

### API
| エンドポイント | メソッド | 説明 |
|---|---|---|
| `GET /tac/calls/queue` | GET | リスト取得。`?sort=score\|name\|area` `?q=検索語` でフィルタ/並び替え。トークン認証。 |
| `POST /tac/calls/queue` | POST | エントリ追加/一括インポート。トークン認証。 |

### mobile_app 変更
- 「リスト」タブを刷新: テキストエリアの代わりにサーバーからキューを取得して一覧表示。
- 名前・エリア・スコア表示。検索窓。タップで発信。
- テキストエリア手動入力モードも残す（フォールバック）。

### テスト方針
- `test_queue.py`: ファイル読み書き、ソート、検索、API ルート。

---

## B. 📊 ダッシュボード刷新（Stats Dashboard）

### API
| エンドポイント | メソッド | 説明 |
|---|---|---|
| `GET /tac/calls/stats` | GET | 担当別・日別の発信数/応答率/成約率を集計。トークン認証。 |

### 集計ロジック（`stats.py`）
- `calllog._all()` を読み、担当者別（`agent_name` フィールド）と日付別に集計。
- 応答率 = `dialed` 中の `disposition` が付いた割合。
- 成約率 = `disposition` 中の `成約` の割合。

### mobile_app 変更
- 「記録」タブに軽量チャート追加（素の SVG バーチャート、外部依存なし）。
- 日別発信数の棒グラフ + 成約率の数値表示。

### テスト方針
- `test_stats.py`: 集計ロジック、API ルート。

---

## C. 🔁 折り返しスケジュール（Callback Schedule）

### データ設計
- `disposition.record()` に `callback_at` パラメータ追加。
- 折り返し予定は calllog レコードに `callback_at: "2026-10-04T14:00"` で保存。
- `callbacks.py`: calllog から折り返し予定を抽出、当日分をキュー先頭に出す。

### API
| エンドポイント | メソッド | 説明 |
|---|---|---|
| `GET /tac/calls/callbacks` | GET | 折り返し予定一覧（当日以降）。トークン認証。 |

### mobile_app 変更
- 「折り返し」disposition 選択時に日時ピッカー表示。
- 折り返し一覧をリストタブに表示。

### テスト方針
- `test_callbacks.py`: 折り返し記録、当日抽出、API ルート。

---

## D. 📝 通話メモ（Call Notes）

### API
| エンドポイント | メソッド | 説明 |
|---|---|---|
| `POST /tac/calls/note` | POST | 通話メモ保存。`to`, `note` パラメータ。トークン認証。 |

### ロジック（`notes.py`）
- calllog にメモ付きレコードを追記（status="note"）。
- 外部送信（Slack等）は `TAC_NOTIFY_WEBHOOK` 設定時のみ。デフォルトOFF。

### mobile_app 変更
- disposition カード内にメモ入力欄追加。

### テスト方針
- `test_notes.py`: メモ保存、API ルート、外部送信OFF確認。

---

## E. 🎨 デザイン全面刷新

### 方針
- Martial Arts 炎ブランド: アクセント `#c2410c` 系（既存維持）。
- ダークモード完全対応（既存 CSS 変数拡張）。
- 大きいタップ領域（48px 以上、既存維持）。
- PWA 質感: カード、角丸、リプル効果、スムーズアニメーション。
- アクセシビリティ: ARIA ラベル、フォーカスリング、コントラスト確保。
- 操作フロー維持: 1タップ1発信・disposition は変えない。

### テスト方針
- 既存 `test_mobile_app.py` のアサーションが全部通ること。
- 新テスト: ARIA属性、ダークモードCSS変数。

---

## F. 🔔 成約/高スコア通知（Webhook Notification）

### 設計
- `notify.py`: `TAC_NOTIFY_WEBHOOK` に JSON POST。
- disposition が「成約」のとき自動発火。
- env 未設定ならスキップ（デフォルトOFF）。

### API 変更
- `disposition.record()` 内で成約時に `notify.send()` を呼ぶ。
- 通知失敗は握り潰す（通話処理を止めない）。

### テスト方針
- `test_notify.py`: 通知送信モック、env未設定時の安全なdegrade。

---

## ファイル構成（新規追加）
```
tac/
  queue.py          # A: スマートリスト
  stats.py          # B: ダッシュボード集計
  callbacks.py      # C: 折り返しスケジュール
  notes.py          # D: 通話メモ
  notify.py         # F: Webhook通知
  mobile_app.py     # E: デザイン刷新（既存更新）
  server.py         # A-F: 新ルート追加（既存更新）
  config.py         # 新ENV変数追加（既存更新）
  disposition.py    # C,F: callback_at対応、通知連携（既存更新）

tests/
  test_queue.py
  test_stats.py
  test_callbacks.py
  test_notes.py
  test_notify.py
```

## 新しい環境変数
| 変数名 | 既定値 | 説明 |
|---|---|---|
| `TAC_QUEUE_FILE` | `tac/queue.json` | スマートリストのファイルパス |
| `TAC_NOTIFY_WEBHOOK` | `""` | 成約通知の Webhook URL（空=無効） |

## デプロイ手順
```bash
fly deploy -c telegram-ai-bot/tac/fly.toml -a tac-martial-arts
# 必要に応じて:
fly secrets set TAC_QUEUE_FILE=/data/queue.json -a tac-martial-arts
fly secrets set TAC_NOTIFY_WEBHOOK=https://hooks.slack.com/... -a tac-martial-arts
```
