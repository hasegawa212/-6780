# BC自動生成パイプライン（Mac mini / Claude Code 用）

AB側（仕入れ）の重要事項説明書から物件概要を抽出し、BC側（B→C 転売）の
BC(.xlsx) を自動生成するサービス。このフォルダを開いて上から実行すれば BC 生成
サービスが立つ。

> 取引構造: A（元売主）→ B（株式会社Martial Arts）→ C（最終買主）。
> 本サービスは **B→C** 区間の書類（BC）を生成する。`buyer_C` / `bc_baibai_daikin`
> の「C」は最終買主を指す。

## 0. ファイル一覧（このフォルダ内）

| ファイル | 役割 |
|----------|------|
| `bc_service.py` | FastAPI サービス（`/health`, `/extract`, `/generate`） |
| `fill_engine.py` | 差し込みエンジン（openpyxl） |
| `bc_schema.py` | テンプレ／用途地域／フィールドのスキーマ定義 |
| `make_blank_templates.py` | 白紙テンプレ生成（`blank_36-1.xlsx` / `blank_37-1.xlsx`） |
| `bc_pipeline.n8n.json` | n8n インポート用ワークフロー |
| `案件マスタ_スキーマ.md` | 連携元（Google Sheets「案件マスタ」）の定義 |
| `deploy/com.martialarts.bcservice.plist` | launchd 常駐設定 |
| `tests/test_fill_engine.py` | 最小テスト |

> **テンプレについて**: 実物の `blank_36-1.xlsx`（戸建）/ `blank_37-1.xlsx`（区分）は
> `bc_schema.py` の定義から **自動生成** される（初回 `/generate` 時、または
> `python make_blank_templates.py`）。実物テンプレが手に入ったら同名で差し替えれば
> そのまま使われる。これにより手順書の残タスク「空の 37-1 テンプレ取得」「37-1 側の
> 用途地域チェック（`checkbox_371`）」は解消済み。

## 1. 環境構築

```bash
cd ~/bc-pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2. APIキー（既存の LiteLLM プロキシ経由でも可）

`/extract`（Claude 抽出）を使う場合のみ必須。`/generate` だけなら不要。

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# もしくは社内 LiteLLM プロキシ(:4001)に向ける
# export ANTHROPIC_BASE_URL=http://localhost:4001
# モデルは既定 claude-opus-4-8（CLAUDE_MODEL で上書き可）
```

## 3. 起動＆疎通テスト

```bash
uvicorn bc_service:app --host 0.0.0.0 --port 8800 &

# /generate を叩いて宇都宮を再生成（抽出JSON＋案件マスタを渡す）
curl -s -X POST http://localhost:8800/generate \
  -H 'Content-Type: application/json' \
  -d '{"bukken":"戸建","extracted":{"shozai":"栃木県宇都宮市清原台二丁目","kuiki":"市街化区域","yoto":"第1種中高層","nijuni_jo":true,"kenpei":60,"yoseki":200},"deal_master":{"buyer_C":"東洋建設ホーム株式会社","bc_baibai_daikin":27800000}}' \
  | python3 -c "import sys,json,base64;d=json.load(sys.stdin);open('test.xlsx','wb').write(base64.b64decode(d['xlsx_base64']));print('OK',d['filename'])"
open test.xlsx
```

### /extract（AB側重説 → 構造化JSON）

実運用の入力は **AB側の重説 PDF（スキャン画像が多い）**。PDF をそのまま渡せば
Claude が OCR＋抽出する。

```bash
# スキャンPDFを base64 で渡す
B64=$(base64 -i 重説.pdf)
curl -s -X POST http://localhost:8800/extract \
  -H 'Content-Type: application/json' \
  -d "{\"bukken\":\"区分\",\"file_base64\":\"$B64\",\"mime\":\"application/pdf\"}"
# → {"extracted":{"shozai":...,"kuiki":"市街化区域","yoto":"第1種住居地域", ...}}

# テキストを直接渡すことも可
curl -s -X POST http://localhost:8800/extract \
  -H 'Content-Type: application/json' \
  -d '{"text":"所在地は長岡市曲新町551-1。市街化区域、第1種住居地域、建蔽率60%、容積率200%。"}'
```

抽出フィールドは `案件マスタ_スキーマ.md` を参照（重説 page4「都市計画法に基づく
制限」/ page5「建築基準法に基づく制限」の各欄に対応）。

## 4. launchd 常駐

`deploy/com.martialarts.bcservice.plist` を編集（`<YOUR_USER>`・APIキー）し設置:

```bash
cp deploy/com.martialarts.bcservice.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.martialarts.bcservice.plist
```

## 5. n8n 配線

1. n8n に `bc_pipeline.n8n.json` をインポート（画面から直接インポート）
2. ②④ の HTTP ノード URL を Mac mini の IP に（`http://<mac-mini-ip>:8800/...`）
3. ③ Google Sheets ノード = 配信対象リスト
   （`1bDAGArxrGwKQbY8F-IZ0RcfWaoPtRB7_F9UowPyEgig`）に「案件マスタ」タブを追加し資格情報を接続
4. ⑤ Slack = `#30_反響_lp-hp`、⑥ Drive = 納品先フォルダを設定

ノード構成: ①トリガ → ②`/extract` → ③案件マスタ取得 → ④`/generate` →
④b base64→ファイル → ⑤Slack通知 / ⑥Drive納品。

## 6. テスト

```bash
cd bc-pipeline && python tests/test_fill_engine.py
```

## 残タスク

- [x] 空の `37-1` テンプレ → スキーマから自動生成（差し替え可）
- [x] 用途地域チェックの 37-1 版（`checkbox_371`）
- [x] AB側重説スキャン PDF からの抽出（`/extract` の document ブロック対応）
- [ ] Slack 承認の ✅/❌ を Webhook で受けて ⑥ 納品を発火するブランチ追加
- [ ] 実物 `blank_36-1.xlsx` / `blank_37-1.xlsx`（正式フォーム）が入手でき次第差し替え
