# ADR-0014: 画面（apps/web）の構成 — Next.js を webpack で、同一オリジン、ポーリング

- Status: Accepted
- Date: 2026-10-06

## Context
Phase 9 で最初の画面（ログイン → リード → Call Workspace → 結果）を作る。ADR-0002 で Next.js 16 を選んでいる。
OBSERVED：ワークスペースのパッケージ（`@tac/domain`・`@tac/workspace`）は NodeNext の流儀で `./x.js` と書いて `./x.ts` を指すが、
Next 16.3.8 の既定の Turbopack ではこの import を解決できず、ビルドが失敗した。`experimental.extensionAlias` は webpack の設定コードでしか使われていない（Turbopack の対応は UNKNOWN）。

## Decision
1. `next dev --webpack` / `next build --webpack` と `experimental.extensionAlias: { ".js": [".ts", ".tsx", ".js"] }` で動かす
2. 画面と API は同じオリジンに見せる（Next の rewrites で `/v1/*` を API へ）。Cookie セッション・CSRF・SameSite=Lax をそのまま使え、CORS を開けない
3. 画面はクライアントコンポーネントだけで作り、業務の判断は `packages/workspace`（単体テスト済み）に置く。React には表示の組み立てだけを書く
4. CSRF トークンはログインの応答で受け取り、そのタブの `sessionStorage` に置く（セッションは HttpOnly Cookie で JS から読めない）
5. 通話の状態はひとまず 1 秒ごとのポーリング（`GET /v1/calls/{id}`）。SSE は文字起こし（Phase 12）と一緒に入れる
6. アイコンはライセンス確認前のため文字の記号で代用し、必ず文字ラベルと一緒に出す（DESIGN_SYSTEM §5 の選定は後で）

## Alternatives
- ワークスペースの全 import を拡張子なし / `.ts` に書き換える：変更が大きく、Node・tsc との整合を崩す（不採用）
- Vite の SPA：ADR-0002 からの逸脱で、ルーティング・ビルドを自前で持つことになる（不採用）
- CSRF トークンを毎回サーバーから取り直す API：トークンはハッシュでしか保存していないため作り直しが必要（後続で検討）

## Consequences
- 新しいタブ・別のタブでは CSRF トークンが無く、状態を変える操作が 403 になる（もう一度ログインすれば使える）。Known Issue として PROGRESS に記録
- Next の将来の版で Turbopack が extensionAlias に対応したら切り替えを検討する
- 画面の CSP はまだ付けていない（Next の inline script に nonce が要る。Phase 16）。API 側は `default-src 'none'`
