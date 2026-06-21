# BC自動生成パイプライン（Mac mini / Claude Code 用）

**AB側（仕入れ）の重要事項説明書を読み込み → BC側（B→C 転売）の重要事項説明書を
自動生成する**サービス。同じ物件なので物件事実はそのまま引き継ぎ、当事者と代金だけ
差し替えるので「間違いない」。

> 取引構造: A（元所有者）→ B（株式会社Martial Arts）→ C（最終買主）。
> 本サービスは **B→C** 区間の書類を生成する。
>
> **対応書類: BC重要事項説明書（`doc_type=juyojiko`）／ BC不動産売買契約書（`doc_type=keiyaku`）。**
> いずれも「物件事実・約款は引き継ぎ、当事者A→B→Cと代金のみ差し替える」方式。

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
| `bc_service.py` | FastAPI（`/health`, `/extract`, `/generate`。`doc_type` で重説/契約書を切替） |
| `juyojiko_schema.py` / `juyojiko_excel.py` | 重要事項説明書のスキーマ／Excel様式再現 |
| `keiyaku_schema.py` / `keiyaku_excel.py` | 不動産売買契約書のスキーマ／Excel様式再現（約款条文対応） |
| `bc_transform.py` | AB→BC 変換（当事者・代金差し替え、物件事実・約款引継ぎ） |
| `wb_fill.py` / `cellmaps.py` | 本番ワークブックへの差し込みエンジン／変種別セルマップ |
| `wb_diff.py` | セルマップ整備用の差分ツール（実例2通から投入セルを特定） |
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

### 設定（環境変数）

`.env.example` をコピーして値を埋める。`.env` はコミットしない。

```bash
cp .env.example .env   # 値を編集
set -a; . ./.env; set +a
```

| 変数 | 既定 | 用途 |
|------|------|------|
| `ANTHROPIC_API_KEY` | — | Claude（`/extract` のみ必須） |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | 社内 LiteLLM プロキシ等に向ける場合 |
| `CLAUDE_MODEL` | `claude-opus-4-8` | 抽出モデル |
| `CLAUDE_MAX_TOKENS` | `4000` | 抽出の最大トークン |
| `BC_PORT` | `8800` | 待受ポート |
| `BC_TEMPLATE_DIR` | `templates` | 本番ワークブック（`36-1.xlsx` 等）の配置先 |

設定状態は `curl http://localhost:8800/health` で確認できる
（`api_key_configured` / `base_url` / `template_dir` / `templates_available`）。

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

### BC売買契約書（`doc_type=keiyaku`）

```bash
# AB契約書を抽出 → BC契約書を生成
B64=$(base64 -i AB売買契約書.pdf)
curl -s -X POST http://localhost:8800/extract \
  -d "{\"doc_type\":\"keiyaku\",\"file_base64\":\"$B64\",\"mime\":\"application/pdf\"}" -H 'Content-Type: application/json'
curl -s -X POST http://localhost:8800/generate -H 'Content-Type: application/json' \
  -d '{"doc_type":"keiyaku","ab": <extractのextracted>, "deal_master":{"buyer_C":"...","bc_baibai_daikin":27800000,"bc_tetsuke":2000000,"bc_zankin_date":"2025-12-01"}}'
```

物件表示・約款（FRK標準条文）はそのまま引き継ぎ、当事者・代金内訳（売買代金・手付・残代金）を
差し替える。価格が変われば残代金（=売買代金−手付）を自動再計算。約款本文が抽出できない場合は
標準条文の見出し骨子を出力する（本文は別添約款による）。

### 本番ワークブックへの差し込み（最も忠実）

御社の BC 用ワークブック（36-1=土地建物 / 37-1=区分敷地権 / 38-1=区分非敷地権）に
そのまま差し込んで出力できる。`template` を指定すると、`BC_TEMPLATE_DIR`（既定 `templates/`）の
`<template>.xlsx` をテンプレに使う。差し込み前に各データセルを**クリア**してから書くため、
記入済みワークブックをテンプレに使っても旧案件のデータは残らない（clear-then-fill）。

```bash
# templates/36-1.xlsx を置いておく（御社ワークブック。リポジトリには含めない）
curl -s -X POST http://localhost:8800/generate -H 'Content-Type: application/json' \
  -d '{"doc_type":"keiyaku","template":"36-1","ab": <extractのextracted>, "deal_master":{...}}'
# あるいは template_base64 でワークブックを直接渡す
```

**重説＋契約書を一括（`doc_type=package`）**: 1回の呼び出しで同じワークブックの
重説シートと契約書シートを両方差し込んで、完成した一式（.xlsx）を返す。

```bash
curl -s -X POST http://localhost:8800/generate -H 'Content-Type: application/json' \
  -d '{"doc_type":"package","template":"36-1",
       "ab": <重説のextracted>, "ab_keiyaku": <契約書のextracted>, "deal_master":{...}}'
```

`template` 未指定なら自作レイアウト Excel にフォールバックする。
セル座標は同一テンプレの実例2通を差分（`python wb_diff.py 例1.xlsx 例2.xlsx "不動産売買契約書"`）して
特定したもの。`cellmaps.py` に変種ごとに定義する（現状: 36-1 契約書シートを実装）。

### /bundle — 添付書類（PDF）の束ね

登記簿・公図・検査済証などの添付 PDF を、指定順に1つの PDF へ結合する。

```bash
curl -s -X POST http://localhost:8800/bundle -H 'Content-Type: application/json' \
  -d '{"attachments":["<base64 PDF1>","<base64 PDF2>"],"filename":"添付書類束.pdf"}'
# → {"filename":"添付書類束.pdf","page_count":9,"pdf_base64":"..."}
```

### /reference — 法令制限の正式名称マスタ

用途地域（14種）・地域地区・「(3) 都市計画法・建築基準法以外の法令」（最新61件）の
正式名称一覧を返す（n8n/UI のドロップダウンや抽出の正規化に利用）。

```bash
curl -s http://localhost:8800/reference
# → {"yoto":[...14...],"chiiki_chiku":[...],"other_horei":[...61...]}
```

一次ソースは御社の重説様式を**複数照合**して特定（`horei_master.py`）。最新版は
「地域における生物の多様性の増進のための活動の促進等に関する法律」「宅地造成及び特定盛土等規制法」
「重要土地等調査法」等を含む。MLIT 原典はegress許可リスト外のため未参照。

`/extract` の出力は `horei_master.normalize_horei` で**略称→正式名称**に正規化される
（例「マンション建替え円滑化法」→「マンションの建替え等の円滑化に関する法律」、
「盛土規制法」→「宅地造成及び特定盛土等規制法」）。発行団体ごとの表記ゆれを吸収する。

### /approval — Slack 承認（✅/❌）の判定

Slack のリアクションを受けて承認可否を返す。n8n は `approved` で⑥納品ブランチを分岐する。
Slack Events API の URL 検証（`type=url_verification`）にも対応。

```bash
curl -s -X POST http://localhost:8800/approval -H 'Content-Type: application/json' \
  -d '{"event":{"type":"reaction_added","reaction":"white_check_mark"}}'
# → {"decision":"approve","approved":true,"reaction":"white_check_mark"}
# ✅系→approve / ❌系→reject / それ以外→pending
```

## 4. launchd 常駐

`deploy/com.martialarts.bcservice.plist` を編集（`<YOUR_USER>`・APIキー）し設置:

```bash
cp deploy/com.martialarts.bcservice.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.martialarts.bcservice.plist
```

## 5. n8n 配線

1. `bc_pipeline.n8n.json` をインポート（画面から直接）
2. HTTPノード（②②b④⑤⑦b）の URL を Mac mini の IP に（`http://<mac-mini-ip>:8800/...`）
3. ③ Google Sheets ノード = 案件マスタ（`1bDAGArxrGwKQbY8F-IZ0RcfWaoPtRB7_F9UowPyEgig`）
4. ⑥ Slack = `#30_反響_lp-hp`、⑨ Drive = 納品先フォルダ
5. ⑦ Webhook（path `bc-approval`）に Slack のリアクションイベントを転送する

全体構成:
- 生成系: ①トリガ →（②`/extract`重説 ＋ ②b`/extract`契約書）→ ③案件マスタ →
  ④`/generate`(**package**=重説＋契約書一括) → ④b base64→ファイル ／ ⑤`/bundle`(添付束ね) →
  ⑥ Slack承認依頼
- 承認系: ⑦ Slackリアクション受信(Webhook) → ⑦b`/approval` → ⑧ IF(approved) →
  ⑨ Drive納品（承認時のみ）

トリガ入力（例）: `{ template:"36-1", juyojiko_pdf_base64, keiyaku_pdf_base64, attachments:[...],
buyer_C, bc_baibai_daikin, ... }`。

## 6. テスト

```bash
cd bc-pipeline && python tests/test_pipeline.py
```

## 残タスク

- [x] AB重説スキャンPDFからの抽出（`/extract` の document ブロック対応）
- [x] AB→BC 変換（当事者A→B→C・代金差し替え、物件事実引継ぎ）
- [x] BC重説の様式再現 Excel 生成（戸建/区分、用途地域・区域区分の■/□）
- [x] BC不動産売買契約書の生成（表紙＋代金内訳＋約款条文。三為特約付与）
- [x] 本番ワークブック（36-1 契約書シート）への差し込み（clear-then-fill）
- [x] 本番ワークブック（36-1 重要事項説明書シート）への差し込み
- [x] 37-1 / 38-1（区分）契約書シートへの差し込み（レイアウト共通）
- [x] 37-1 / 38-1（区分）重説シートへの差し込み（レイアウト共通）
- [x] 重説のチェックボックス（区域区分・用途地域）の■/□差し込み（36-1/区分）
- [x] 重説の地域地区チェック（防火/準防火・建築基準法22条・高度地区）の差し込み
- [x] 日付の複数セル分割差込（残代金支払日・融資承認取得期日 を 令和年/月/日 に分解）
- [x] 添付書類（登記簿・公図・検査済証等）の PDF 結合（`/bundle`）
- [x] 重説＋契約書の一括差込（`doc_type=package`）
- [x] 地番の複数セル分割差込（土地所在を 所在/番/番地 に分解。36-1 契約書・重説）
- [x] Slack 承認 ✅/❌ の判定（`/approval`）。n8n は `approved` で⑥納品を分岐
