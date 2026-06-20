# BC自動生成パイプライン（Mac mini / Claude Code 用）

**AB側（仕入れ）の重要事項説明書を読み込み → BC側（B→C 転売）の重要事項説明書を
自動生成する**サービス。同じ物件なので物件事実はそのまま引き継ぎ、当事者と代金だけ
差し替えるので「間違いない」。

> 取引構造: A（元所有者）→ B（株式会社Martial Arts）→ C（最終買主）。
> 本サービスは **B→C** 区間の書類を生成する。
>
> **現フェーズ: BC重要事項説明書まで。** 売買契約書の生成は次段（様式/サンプル入手後）。

## 変換ルール（「間違いないように」の肝）

| 項目 | AB（入力） | BC（出力） |
|------|-----------|-----------|
| 物件の表示・登記・法令制限・設備・管理費等 | — | **そのまま引き継ぐ** |
| 売主 | A（元所有者） | **B（株式会社Martial Arts）** |
| 買主 | B（Martial Arts） | **C（最終買主・案件マスタ）** |
| 売買代金 | AB 仕入価格 | **BC 転売価格（案件マスタ）** |
| 宅建業者・取引士 | A 側仲介 | BC 側媒介（案件マスタにあれば、無ければ空欄） |
| 特約 | 三為（所有権移転先指定） | 引き継ぎ＋BC用注記 |

## 0. ファイル一覧

| ファイル | 役割 |
|----------|------|
| `bc_service.py` | FastAPI（`/health`, `/extract`, `/generate`） |
| `juyojiko_schema.py` | 重要事項説明書の構造化スキーマ（戸建/区分） |
| `juyojiko_excel.py` | 重説を Excel で様式再現するレンダラ |
| `bc_transform.py` | AB→BC 変換（当事者・代金差し替え、物件事実引継ぎ） |
| `bc_schema.py` | 用途地域・物件種別の正規化ユーティリティ |
| `bc_pipeline.n8n.json` | n8n インポート用ワークフロー |
| `案件マスタ_スキーマ.md` | 連携元（Google Sheets「案件マスタ」）と入出力の定義 |
| `deploy/com.martialarts.bcservice.plist` | launchd 常駐設定 |
| `tests/test_pipeline.py` | 最小テスト |

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
# 社内 LiteLLM プロキシ(:4001)に向ける場合:
# export ANTHROPIC_BASE_URL=http://localhost:4001
# モデルは既定 claude-opus-4-8（CLAUDE_MODEL で上書き可）
```

## 3. 起動

```bash
uvicorn bc_service:app --host 0.0.0.0 --port 8800 &
```

### /extract — AB重説（スキャンPDFが多い）→ 構造化JSON

```bash
B64=$(base64 -i AB重説.pdf)
curl -s -X POST http://localhost:8800/extract \
  -H 'Content-Type: application/json' \
  -d "{\"file_base64\":\"$B64\",\"mime\":\"application/pdf\"}"
# → {"extracted": { ... 重説の構造化JSON ... }}
```

PDF をそのまま渡せば Claude が OCR＋抽出する。テキスト直渡し（`"text":"..."`）も可。

### /generate — AB重説JSON ＋案件マスタ → BC重説(.xlsx)

```bash
# 新方式: /extract の出力(ab) ＋ 案件マスタ(deal_master)
curl -s -X POST http://localhost:8800/generate \
  -H 'Content-Type: application/json' \
  -d '{"ab": <extractのextracted>, "deal_master":{"buyer_C":"東洋建設ホーム株式会社","bc_baibai_daikin":27800000}}' \
  | python3 -c "import sys,json,base64;d=json.load(sys.stdin);open('bc.xlsx','wb').write(base64.b64decode(d['xlsx_base64']));print('OK',d['filename'])"
open bc.xlsx
```

旧方式（手順書 curl 互換）も動く。最小フィールドから BC重説を生成する:

```bash
curl -s -X POST http://localhost:8800/generate -H 'Content-Type: application/json' \
  -d '{"bukken":"戸建","extracted":{"shozai":"栃木県宇都宮市清原台二丁目","kuiki":"市街化区域","yoto":"第1種中高層","nijuni_jo":true,"kenpei":60,"yoseki":200},"deal_master":{"buyer_C":"東洋建設ホーム株式会社","bc_baibai_daikin":27800000}}'
```

案件マスタ（`deal_master`）のフィールドは `案件マスタ_スキーマ.md` を参照。

## 4. launchd 常駐

`deploy/com.martialarts.bcservice.plist` を編集（`<YOUR_USER>`・APIキー）し設置:

```bash
cp deploy/com.martialarts.bcservice.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.martialarts.bcservice.plist
```

## 5. n8n 配線

1. `bc_pipeline.n8n.json` をインポート（画面から直接）
2. ②④ の HTTP ノード URL を Mac mini の IP に（`http://<mac-mini-ip>:8800/...`）
3. ③ Google Sheets ノード = 案件マスタ（`1bDAGArxrGwKQbY8F-IZ0RcfWaoPtRB7_F9UowPyEgig`）
4. ⑤ Slack = `#30_反響_lp-hp`、⑥ Drive = 納品先フォルダ

構成: ①トリガ → ②`/extract`(AB重説) → ③案件マスタ取得 → ④`/generate`(BC重説) →
④b base64→ファイル → ⑤Slack通知 / ⑥Drive納品。

## 6. テスト

```bash
cd bc-pipeline && python tests/test_pipeline.py
```

## 残タスク

- [x] AB重説スキャンPDFからの抽出（`/extract` の document ブロック対応）
- [x] AB→BC 変換（当事者A→B→C・代金差し替え、物件事実引継ぎ）
- [x] BC重説の様式再現 Excel 生成（戸建/区分、用途地域・区域区分の■/□）
- [ ] **BC売買契約書の生成**（次フェーズ。区分/土地建物の標準ひな型＋三為特約）
- [ ] Slack 承認の ✅/❌ を Webhook で受けて ⑥ 納品を発火するブランチ
- [ ] 戸建（土地建物）重説の項目網羅を実サンプルで追補
