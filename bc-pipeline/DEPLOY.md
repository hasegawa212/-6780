# 本番デプロイ手順（BC自動生成サービス）

仕入れ（AB）書類から転売（BC）の重要事項説明書＋不動産売買契約書を、御社の公式様式WBへ自動差込するサービスのデプロイ手順です。

## ☁️ クラウド公開（Render）— iPhone/PCから“いつでも”使う

Mac不要で、**固定の `https://…` URL**を作り、iPhone・PCどこからでもログインして使う手順。
リポジトリ直下の `render.yaml`（Blueprint）で数クリックで公開できます。

### 🟣 いちばん簡単：ワンタップ公開ボタン

下のボタンを **iPhoneのSafariでタップ** → Renderにログイン → ID/PW/APIキーを入れて Apply、だけ。

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/hasegawa212/-6780)

> 直リンク: `https://render.com/deploy?repo=https://github.com/hasegawa212/-6780`
> （Renderに未登録なら、タップ後にGitHubでのサインアップ画面が出る。無料）

### 手動でやる場合

1. **Renderに登録**：https://render.com → GitHubアカウントでサインアップ（無料）。
2. **Blueprintで作成**：ダッシュボードで **New → Blueprint** → このリポジトリ（`hasegawa212/-6780`）を選択。`render.yaml` が自動検出される。
3. **環境変数を入力**（`sync:false` の項目。画面で聞かれる）：
   - `BC_BOOTSTRAP_USER` … ログインID（例：`hikaru`）
   - `BC_BOOTSTRAP_PASSWORD` … ログインPW（8文字以上・強めに）
   - `ANTHROPIC_API_KEY` … 自動読取用のキー（無ければ空でも可。手入力で作成はできる）
4. **Apply / Create** → 数分でビルド＆公開。`https://bc-auto-xxxx.onrender.com` のようなURLが出る。
5. **iPhone/PCのブラウザ**でそのURLを開く → ログイン画面 → 3で決めたID/PWでログイン。
   iPhoneは共有ボタン→「ホーム画面に追加」でアプリのように使える。

> - 公開URLなので `BC_AUTH_REQUIRED=1`（ログイン必須）＋ `BC_COOKIE_SECURE=1` を既定でON。
> - ログインユーザーは **env（BC_BOOTSTRAP_*）から毎起動で用意**されるため、Renderの揮発性
>   ディスクでも認証が維持される。追加ユーザーは env を増やすか、永続ディスクで `manage_users.py`。
> - **御社の公式様式WB（templates/）はイメージに含めない**（PII配慮）。未配置だと `/generate`
>   は自作Excelにフォールバックする。公式様式で出したい場合は Render の Disks（永続ディスク）に
>   `templates/*.xlsx` を置き、`BC_TEMPLATE_DIR` を指す。
> - **顧客情報を扱う**ため、強いパスワード必須。社の方針に沿って利用のこと。

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
