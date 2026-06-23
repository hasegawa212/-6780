# 本番デプロイ手順（BC自動生成サービス）

仕入れ（AB）書類から転売（BC）の重要事項説明書＋不動産売買契約書を、御社の公式様式WBへ自動差込するサービスのデプロイ手順です。

## 構成
```
n8n（オーケストレーション）
  ├─ /extract  … 実PDF→構造化（Claude, 要 ANTHROPIC_API_KEY）
  ├─ /generate … AB→BC変換＋公式様式WBへ差込（doc_type=juyojiko/keiyaku/package）
  ├─ /bundle   … 添付PDFの結合
  └─ /approval … Slack ✅/❌ 判定
FastAPI(bc_service:app) ＋ templates/<様式>.xlsx（御社のブランク様式WB）
```

## 0. 前提
- Python 3.11+（Mac mini 常駐 or Docker）
- `ANTHROPIC_API_KEY`（/extract を使う場合）。/generate だけなら不要。
- 公式様式のブランクWB：`36-1.xlsx`（戸建）/ `37-1.xlsx`（区分・敷地権）/ `38-1.xlsx`（区分・非敷地権）

## 1. セットアップ（ワンショット）
```bash
cd bc-pipeline
bash deploy/setup.sh        # venv作成・依存導入・テンプレ確認・.env作成・テスト
$EDITOR .env                # ANTHROPIC_API_KEY 等を記入
cp /path/to/御社WB/36-1.xlsx templates/   # 37-1.xlsx / 38-1.xlsx も同様に配置
```
> `templates/` と `*.xlsx`・`.env` は `.gitignore` 済み（PII・鍵をコミットしない）。

## 2. 起動（いずれか）
**A) Mac mini 常駐（launchd）**
```bash
cp deploy/com.martialarts.bcservice.plist ~/Library/LaunchAgents/
# <YOUR_USER>・各パス・ANTHROPIC_API_KEY を編集
launchctl load ~/Library/LaunchAgents/com.martialarts.bcservice.plist
```
**B) Docker**
```bash
docker compose -f deploy/docker-compose.yml up -d   # .env と templates/ を自動マウント
```
**C) フォアグラウンド（動作確認）**
```bash
set -a; . ./.env; set +a
.venv/bin/uvicorn bc_service:app --host 0.0.0.0 --port 8800
```

## 3. プリフライト
```bash
curl -s localhost:8800/health | python -m json.tool
# api_key_configured: true / templates_available: ["36-1","37-1","38-1"] を確認
```

## 4. n8n 配線
1. n8n に `bc_pipeline.n8n.json` をインポート。
2. 各 HTTP ノードの URL を `http://<mac-mini-ip>:8800/...` に設定。
3. 認証情報を設定：Google Sheets（案件マスタ）/ Google Drive（納品）/ Slack（#30_反響_lp-hp 承認）。
4. `/generate` は `template` 未指定でもWBのA1マーカーから様式を自動判定（36-1/37-1/38-1）。本番WBを `template_base64` で渡すか、`BC_TEMPLATE_DIR` 配置のものを使用。

## 5. 動作確認（実データ不要のデモ）
```bash
.venv/bin/python demo.py                 # サンプルAB→BC一式（標準様式Excel）
.venv/bin/python demo.py --template templates/37-1.xlsx   # 公式様式へ差込
```

## 運用メモ（「間違いなく」の担保）
- 物件事実はABから引継ぎ、差し替えは当事者（A→B→C）と代金のみ。
- 買主C・売買価格は案件マスタ由来。無ければ空欄（捏造しない）。
- **ブランク様式WBを使うこと**：記入済みの他案件WBを流用すると、未マップセルに前案件のPII・価格が残る恐れがある（占有者・管理・添付・媒介業者欄などは差込/クリア対応済みだが、ブランクが最も安全）。
- 三為（中間省略）：登記名義人は元所有者Aのまま引継ぎ、所有権はA→Cへ直接移転。三為特約を自動付与。
