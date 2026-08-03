# iPhoneでBC自動生成を使う（1タップ公開）

Mac不要。iPhoneのSafariだけで、いつでも開ける `https://…` のURLを作れます。

## ステップは3つだけ

### ① 下のボタンをタップ

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/hasegawa212/-6780)

タップできないときは、この直リンクをSafariで開く：
`https://render.com/deploy?repo=https://github.com/hasegawa212/-6780`

（Renderに初めてなら「Get Started / GitHubでサインアップ」が出ます。無料）

### ② 3つ入力する

| 項目 | 入れる値 |
|---|---|
| `BC_BOOTSTRAP_USER` | 好きなログインID（例：`hikaru`）|
| `BC_BOOTSTRAP_PASSWORD` | 好きなパスワード（8文字以上）|
| `ANTHROPIC_API_KEY` | 自動読取キー（**無ければ空でOK**。空でも書類は作れます）|

### ③ 「Apply」を押す

数分で `https://bc-auto-xxxx.onrender.com` のようなURLが出ます。
そのURLをSafariで開き、②で決めたID/PWでログイン。
**共有ボタン → ホーム画面に追加** で、アプリのように使えます。

---

- 公開URLなのでログイン必須（他人は入れません）。
- パスワードは必ず強めに（顧客情報を扱うため）。
- APIキーを使う場合は、**必ず新しく発行したキー**を入れてください。
