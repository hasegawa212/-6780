"""iPhone 用 発信アプリ（Mobile App）。

iPhone の Safari で /tac/app を開き「ホーム画面に追加」すると、アプリのように使える
1枚の HTML（PWA）。既存の API（/tac/call・/tac/calls/disposition・/tac/calls/summary・
/tac/calls・/tac/agents）を叩くだけで、サーバー側の DNC・発信時間帯・1日上限の
ガードはそのまま効く。

- 操作者トークンはページに埋め込まない。端末で一度入力し、端末内（localStorage）に
  保存して X-TAC-Token ヘッダーで送る。ページ自体には個人情報を含まない。
- 発信は1件ずつ担当者がタップして行う。リストは「次の番号」を順に表示するだけで、
  タイマー等で自動的に掛け続けることはしない（一斉自動発信=オートダイヤラーではない）。
- 表示する値は textContent で入れる（innerHTML に外部の値を入れない＝XSS 対策）。
"""

from __future__ import annotations

APP_NAME = "さくら発信"

DISPOSITIONS = ("成約", "検討", "折り返し", "不在", "拒否")


def manifest() -> dict:
    """ホーム画面に追加したときのアプリ定義（Web App Manifest）。"""
    return {
        "name": APP_NAME,
        "short_name": APP_NAME,
        "start_url": "/tac/app",
        "scope": "/tac/",
        "display": "standalone",
        "background_color": "#0c0a09",
        "theme_color": "#c2410c",
        "lang": "ja",
        "icons": [
            {"src": "/tac/app/icon-192.png", "sizes": "192x192",
             "type": "image/png", "purpose": "any"},
            {"src": "/tac/app/icon-512.png", "sizes": "512x512",
             "type": "image/png", "purpose": "any"},
            {"src": "/tac/app/icon-512.png", "sizes": "512x512",
             "type": "image/png", "purpose": "maskable"},
        ],
    }


def render() -> str:
    """アプリ本体の HTML を返す（静的。トークンや電話番号は含まない）。"""
    buttons = "".join(
        f'<button class="dispo{" danger" if d == "拒否" else ""}" data-result="{d}">{d}</button>'
        for d in DISPOSITIONS
    )
    return _PAGE.replace("{{APP_NAME}}", APP_NAME).replace("{{DISPO_BUTTONS}}", buttons)


_PAGE = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="{{APP_NAME}}">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#c2410c">
<link rel="manifest" href="/tac/app/manifest.webmanifest">
<link rel="apple-touch-icon" href="/tac/app/icon-180.png">
<link rel="icon" type="image/png" sizes="192x192" href="/tac/app/icon-192.png">
<title>{{APP_NAME}}</title>
<style>
:root {
  --bg: #fafaf9; --bg2: #f5f5f4; --card: #ffffff; --ink: #1c1917; --ink2: #44403c;
  --muted: #78716c; --line: #e7e5e4; --line2: #d6d3d1;
  --accent: #c2410c; --accent-light: #ea580c; --accent-bg: #fff7ed; --accent-ink: #ffffff;
  --ok: #15803d; --ok-bg: #f0fdf4; --bad: #b91c1c; --bad-bg: #fef2f2;
  --shadow: 0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.04);
  --shadow-lg: 0 4px 12px rgba(0,0,0,.1);
  --radius: 16px; --radius-sm: 10px;
  --font: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", sans-serif;
  --transition: .2s cubic-bezier(.4,0,.2,1);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0c0a09; --bg2: #1c1917; --card: #1c1917; --ink: #fafaf9; --ink2: #d6d3d1;
    --muted: #a8a29e; --line: #292524; --line2: #44403c;
    --accent: #ea580c; --accent-light: #fb923c; --accent-bg: #431407; --accent-ink: #ffffff;
    --ok: #4ade80; --ok-bg: #052e16; --bad: #f87171; --bad-bg: #450a0a;
    --shadow: 0 1px 3px rgba(0,0,0,.3); --shadow-lg: 0 4px 12px rgba(0,0,0,.4);
  }
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { background: var(--bg); color: var(--ink); font: 16px/1.55 var(--font);
  -webkit-text-size-adjust: 100%; -webkit-tap-highlight-color: transparent; }
body { min-height: 100dvh; padding: calc(env(safe-area-inset-top) + 8px) 16px calc(env(safe-area-inset-bottom) + 76px);
  max-width: 480px; margin: 0 auto; }

/* ヘッダー */
.app-header { display: flex; align-items: center; gap: 10px; padding: 8px 0 16px; }
.app-logo { width: 36px; height: 36px; border-radius: 10px; background: linear-gradient(135deg, #c2410c, #ea580c);
  display: flex; align-items: center; justify-content: center; color: #fff; font-size: 18px; flex-shrink: 0; }
.app-title { font-size: 20px; font-weight: 700; letter-spacing: .01em; }
.app-subtitle { font-size: 12px; color: var(--muted); margin-top: -2px; }

/* カード */
.card { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 20px; margin-bottom: 12px; box-shadow: var(--shadow); transition: box-shadow var(--transition); }
.card:hover { box-shadow: var(--shadow-lg); }
.card-title { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase;
  letter-spacing: .05em; margin-bottom: 12px; }

/* フォーム要素 */
label { display: block; font-size: 13px; font-weight: 500; color: var(--muted); margin: 12px 0 6px; }
input, select, textarea { width: 100%; font-size: 17px; padding: 12px 14px;
  border: 1.5px solid var(--line2); border-radius: var(--radius-sm); background: var(--bg2);
  color: var(--ink); font-family: var(--font); transition: border-color var(--transition), box-shadow var(--transition);
  -webkit-appearance: none; appearance: none; }
input:focus, select:focus, textarea:focus { outline: none; border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(194,65,12,.15); }
textarea { min-height: 120px; resize: vertical; }
select { background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%2378716c' stroke-width='1.5' fill='none'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 14px center; padding-right: 36px; }

/* ボタン */
button { font-size: 16px; font-weight: 600; font-family: var(--font); border: 0;
  border-radius: 12px; padding: 14px 20px; cursor: pointer; min-height: 48px;
  background: var(--bg2); color: var(--ink); transition: all var(--transition);
  position: relative; overflow: hidden; -webkit-user-select: none; user-select: none; }
button:active { transform: scale(.97); }
button:disabled { opacity: .45; pointer-events: none; }
button::after { content: ""; position: absolute; inset: 0; background: currentColor; opacity: 0;
  transition: opacity .15s; pointer-events: none; }
button:active::after { opacity: .08; }
.primary { background: linear-gradient(135deg, #c2410c, #ea580c); color: var(--accent-ink);
  width: 100%; font-size: 18px; font-weight: 700; margin-top: 16px;
  box-shadow: 0 2px 8px rgba(194,65,12,.3); }
.primary:active { box-shadow: 0 1px 4px rgba(194,65,12,.2); }
.secondary { background: var(--accent-bg); color: var(--accent); border: 1px solid var(--accent); }
.row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.row button { flex: 1 1 28%; min-width: 80px; }
.danger { background: linear-gradient(135deg, #b91c1c, #dc2626); color: #fff;
  box-shadow: 0 2px 6px rgba(185,28,28,.25); }

/* disposition ボタン */
.dispo { font-size: 15px; padding: 12px 8px; }

/* 数字表示 */
.big { font-size: 28px; font-weight: 800; letter-spacing: .02em; word-break: break-all; color: var(--ink); }
.muted { color: var(--muted); font-size: 13px; line-height: 1.5; }
.msg { margin-top: 10px; font-size: 14px; min-height: 1.5em; font-weight: 500; }
.msg.ok { color: var(--ok); } .msg.bad { color: var(--bad); }
.hidden { display: none !important; }

/* リスト */
ul.list { list-style: none; margin: 0; padding: 0; }
ul.list li { padding: 12px 0; border-bottom: 1px solid var(--line); font-size: 14px;
  display: flex; justify-content: space-between; align-items: center; gap: 8px; }
ul.list li:last-child { border-bottom: 0; }

/* ピル */
.pills { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.pill { background: var(--bg2); border: 1px solid var(--line); border-radius: 999px;
  padding: 4px 12px; font-size: 12px; font-weight: 500; }
.pill-accent { background: var(--accent-bg); border-color: var(--accent); color: var(--accent); }

/* キューリスト */
.queue-item { display: flex; align-items: center; gap: 10px; padding: 12px 0;
  border-bottom: 1px solid var(--line); cursor: pointer; }
.queue-item:last-child { border-bottom: 0; }
.queue-item:active { background: var(--bg2); }
.queue-score { min-width: 36px; height: 36px; border-radius: 50%; display: flex;
  align-items: center; justify-content: center; font-size: 13px; font-weight: 700;
  background: var(--accent-bg); color: var(--accent); flex-shrink: 0; }
.queue-info { flex: 1; min-width: 0; }
.queue-name { font-size: 15px; font-weight: 600; }
.queue-detail { font-size: 12px; color: var(--muted); }

/* チャートエリア */
.chart-area { margin-top: 12px; }
.bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.bar-label { font-size: 12px; color: var(--muted); min-width: 52px; text-align: right; }
.bar-track { flex: 1; height: 24px; background: var(--bg2); border-radius: 6px; overflow: hidden; }
.bar-fill { height: 100%; background: linear-gradient(90deg, #c2410c, #ea580c); border-radius: 6px;
  transition: width .5s ease; min-width: 2px; }
.bar-value { font-size: 12px; font-weight: 600; min-width: 28px; }
.stat-row { display: flex; gap: 8px; margin-top: 12px; }
.stat-card { flex: 1; background: var(--bg2); border-radius: var(--radius-sm); padding: 12px; text-align: center; }
.stat-value { font-size: 22px; font-weight: 800; color: var(--accent); }
.stat-label { font-size: 11px; color: var(--muted); margin-top: 2px; }

/* メモ */
.note-input { margin-top: 12px; }
.note-input textarea { min-height: 60px; font-size: 14px; padding: 10px; }

/* 折り返し */
.cb-time { margin-top: 8px; }
.cb-time input[type="datetime-local"] { font-size: 15px; }

/* 検索 */
.search-box { position: relative; margin-bottom: 12px; }
.search-box input { padding-left: 36px; font-size: 15px; }
.search-box::before { content: "🔍"; position: absolute; left: 12px; top: 50%; transform: translateY(-50%);
  font-size: 14px; pointer-events: none; }

/* ナビゲーション */
nav { position: fixed; left: 0; right: 0; bottom: 0; z-index: 100;
  display: flex; background: var(--card); border-top: 1px solid var(--line);
  padding: 4px 0 calc(env(safe-area-inset-bottom) + 4px);
  box-shadow: 0 -2px 10px rgba(0,0,0,.06); backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px); }
nav button { flex: 1; background: none; border-radius: 0; font-size: 11px; font-weight: 500;
  color: var(--muted); padding: 6px 4px 2px; min-height: 44px;
  display: flex; flex-direction: column; align-items: center; gap: 2px; }
nav button .nav-icon { font-size: 20px; line-height: 1; }
nav button.on { color: var(--accent); font-weight: 700; }
nav button::after { display: none; }
</style>
</head>
<body>

<header class="app-header">
  <div class="app-logo" role="img" aria-label="さくら発信">🔥</div>
  <div>
    <div class="app-title">{{APP_NAME}}</div>
    <div class="app-subtitle">Martial Arts — click to call</div>
  </div>
</header>

<!-- ===== 発信タブ ===== -->
<section id="tab-call" role="tabpanel" aria-label="発信">
  <div class="card">
    <label for="to">かける番号</label>
    <input id="to" type="tel" inputmode="tel" autocomplete="off" placeholder="090-1234-5678" aria-label="電話番号">
    <label for="agent">つなぐ担当者</label>
    <select id="agent" aria-label="担当者選択"><option value="">自動で振り分け</option></select>
    <button class="primary" id="dial" aria-label="発信する">📞 発信する</button>
    <p class="muted" style="margin-top:10px">相手が出たら、担当者の電話が鳴ります。1回のタップで1件だけ発信します。</p>
    <div class="msg" id="call-msg" role="status" aria-live="polite"></div>
  </div>
  <div class="card hidden" id="dispo-card">
    <div class="card-title">通話の結果</div>
    <div class="big" id="dispo-to"></div>
    <div class="row" id="dispo-buttons">{{DISPO_BUTTONS}}</div>
    <div class="cb-time hidden" id="cb-time-wrap">
      <label for="cb-time">折り返し日時</label>
      <input type="datetime-local" id="cb-time" aria-label="折り返し予定日時">
    </div>
    <div class="note-input">
      <label for="dispo-note">メモ（任意）</label>
      <textarea id="dispo-note" placeholder="お客様の要望など" aria-label="通話メモ"></textarea>
    </div>
    <p class="muted" style="margin-top:8px">「拒否」を選ぶと発信禁止リストに入り、次から掛からなくなります。</p>
  </div>
</section>

<!-- ===== リストタブ ===== -->
<section id="tab-list" class="hidden" role="tabpanel" aria-label="リスト">
  <div class="card">
    <div class="card-title">スマートリスト</div>
    <div class="search-box">
      <input id="queue-search" type="search" placeholder="名前・エリア・番号で検索" aria-label="リスト検索">
    </div>
    <div id="queue-list"></div>
    <div class="row" style="margin-top:12px">
      <button id="queue-refresh" class="secondary">更新</button>
      <button id="queue-sort-score">スコア順</button>
      <button id="queue-sort-name">名前順</button>
    </div>
  </div>
  <div class="card">
    <div class="card-title">手動リスト（1行に1番号）</div>
    <textarea id="list" placeholder="090-1111-2222&#10;03-1234-5678" aria-label="手動発信リスト"></textarea>
    <div class="row"><button id="list-save">リストを保存</button><button id="list-reset">最初から</button></div>
  </div>
  <div class="card">
    <div class="muted" id="list-pos"></div>
    <div class="big" id="list-next">—</div>
    <button class="primary" id="list-dial">📞 この番号に発信</button>
    <div class="row"><button id="list-skip">スキップ</button></div>
    <p class="muted">結果を記録すると次の番号が表示されます。自動では掛けません。</p>
  </div>
</section>

<!-- ===== 記録タブ ===== -->
<section id="tab-today" class="hidden" role="tabpanel" aria-label="記録">
  <div class="card">
    <div class="card-title">本日のサマリー</div>
    <div class="big" id="sum-total">—</div>
    <div class="pills" id="sum-status"></div>
    <div class="stat-row" id="stat-cards">
      <div class="stat-card"><div class="stat-value" id="stat-dialed">—</div><div class="stat-label">発信</div></div>
      <div class="stat-card"><div class="stat-value" id="stat-seiyaku">—</div><div class="stat-label">成約</div></div>
      <div class="stat-card"><div class="stat-value" id="stat-rate">—</div><div class="stat-label">成約率</div></div>
    </div>
    <div class="row"><button id="refresh" class="secondary">更新</button></div>
  </div>
  <div class="card">
    <div class="card-title">担当別パフォーマンス</div>
    <div class="chart-area" id="agent-chart"></div>
  </div>
  <div class="card">
    <div class="card-title">折り返し予定（本日）</div>
    <ul class="list" id="cb-list"></ul>
  </div>
  <div class="card">
    <div class="card-title">直近の記録</div>
    <ul class="list" id="recent"></ul>
  </div>
</section>

<!-- ===== フォロータブ ===== -->
<section id="tab-follow" class="hidden" role="tabpanel" aria-label="フォロー">
  <div class="card">
    <div class="card-title">自動フォロー（分類済み）</div>
    <div class="pills" id="follow-cats"></div>
    <div id="follow-list" style="margin-top:12px"></div>
    <div class="row" style="margin-top:12px">
      <button id="follow-refresh" class="secondary">更新</button>
      <button id="follow-promote" class="primary" style="flex:2">✅ 選択をフォロー予定へ</button>
    </div>
    <p class="muted" style="margin-top:8px">チェックした相手を発信リストに入れます。拒否・上限(2回)・時間帯は自動で守ります。要確認・連絡停止は選べません。</p>
    <div class="msg" id="follow-msg" role="status" aria-live="polite"></div>
  </div>
</section>

<!-- ===== 設定タブ ===== -->
<section id="tab-settings" class="hidden" role="tabpanel" aria-label="設定">
  <div class="card">
    <div class="card-title">接続設定</div>
    <label for="token">操作者トークン（TAC_OUTBOUND_TOKEN）</label>
    <input id="token" type="password" autocomplete="off" aria-label="操作者トークン">
    <button class="primary" id="token-save">保存</button>
    <p class="muted" style="margin-top:10px">この iPhone の中にだけ保存されます。Safari の共有ボタン →「ホーム画面に追加」でアプリになります。</p>
    <div class="msg" id="settings-msg" role="status" aria-live="polite"></div>
  </div>
</section>

<nav role="tablist" aria-label="メインナビゲーション">
  <button data-tab="call" class="on" role="tab" aria-selected="true"><span class="nav-icon">📞</span>発信</button>
  <button data-tab="list" role="tab" aria-selected="false"><span class="nav-icon">📋</span>リスト</button>
  <button data-tab="follow" role="tab" aria-selected="false"><span class="nav-icon">🔁</span>フォロー</button>
  <button data-tab="today" role="tab" aria-selected="false"><span class="nav-icon">📊</span>記録</button>
  <button data-tab="settings" role="tab" aria-selected="false"><span class="nav-icon">⚙️</span>設定</button>
</nav>

<script>
(function(){
  "use strict";
  var $ = function(id){ return document.getElementById(id); };
  function load(k, d){ try { var v = localStorage.getItem(k); return v === null ? d : v; } catch(e){ return d; } }
  function save(k, v){ try { localStorage.setItem(k, v); } catch(e){} }
  var token = load("tac_token", "");
  var current = null;
  var fromList = false;

  function say(el, text, ok){ el.textContent = text; el.className = "msg " + (ok ? "ok" : "bad"); }

  function api(path, method, params, json_body){
    var opt = { method: method, headers: { "X-TAC-Token": token } };
    if (json_body) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(json_body);
    } else if (params) {
      opt.body = new URLSearchParams(params);
    }
    return fetch(path, opt).then(function(r){
      return r.json().catch(function(){ return {}; }).then(function(j){
        if (r.status === 401 || r.status === 503) { j.error = j.error || "トークンを確認してください"; }
        j._ok = r.ok && j.ok !== false;
        return j;
      });
    }).catch(function(){ return { _ok: false, error: "通信できませんでした" }; });
  }

  // ---- タブ ----
  var tabs = document.querySelectorAll("nav button");
  function show(name){
    ["call","list","follow","today","settings"].forEach(function(t){
      $("tab-" + t).classList.toggle("hidden", t !== name);
    });
    tabs.forEach(function(b){
      var active = b.getAttribute("data-tab") === name;
      b.classList.toggle("on", active);
      b.setAttribute("aria-selected", active ? "true" : "false");
    });
    if (name === "today") refresh();
    if (name === "list") loadQueue();
    if (name === "follow") loadFollow();
  }
  tabs.forEach(function(b){ b.addEventListener("click", function(){ show(b.getAttribute("data-tab")); }); });

  // ---- 担当者 ----
  function loadAgents(){
    if (!token) return;
    api("/tac/agents", "GET").then(function(j){
      var sel = $("agent");
      while (sel.options.length > 1) sel.remove(1);
      (j.agents || []).forEach(function(a){
        var o = document.createElement("option");
        o.value = a.name || a.number;
        o.textContent = a.name ? a.name + "（" + a.number + "）" : a.number;
        sel.appendChild(o);
      });
    });
  }

  // ---- 発信 ----
  function dial(number, btn, list){
    if (!token) { show("settings"); say($("settings-msg"), "先にトークンを保存してください", false); return; }
    if (!number) { say($("call-msg"), "番号を入れてください", false); return; }
    btn.disabled = true;
    say($("call-msg"), "発信中…", true);
    var p = { to: number };
    if ($("agent").value) p.agent = $("agent").value;
    api("/tac/call", "POST", p).then(function(j){
      btn.disabled = false;
      if (j._ok) {
        current = j.to || number; fromList = list;
        say($("call-msg"), "発信しました。担当者の電話が鳴ります。", true);
        $("dispo-to").textContent = current;
        $("dispo-card").classList.remove("hidden");
        $("dispo-note").value = "";
        $("cb-time").value = "";
        $("cb-time-wrap").classList.add("hidden");
        if (list) show("call");
      } else {
        say($("call-msg"), j.error || j.reason || "発信できませんでした", false);
        if (list) show("call");
      }
    });
  }
  $("dial").addEventListener("click", function(){ dial($("to").value.trim(), $("dial"), false); });

  // ---- 結果記録 ----
  document.querySelectorAll("#dispo-buttons button").forEach(function(b){
    b.addEventListener("click", function(){
      if (!current) return;
      var result = b.getAttribute("data-result");
      if (result === "拒否" && !confirm("発信禁止リストに入れます。よろしいですか？")) return;
      if (result === "折り返し") {
        $("cb-time-wrap").classList.toggle("hidden");
        if (!$("cb-time-wrap").classList.contains("hidden")) return;
      }
      b.disabled = true;
      var params = { to: current, result: result };
      var cbTime = $("cb-time").value;
      if (cbTime) params.callback_at = cbTime;
      var noteText = $("dispo-note").value.trim();
      api("/tac/calls/disposition", "POST", params).then(function(j){
        b.disabled = false;
        if (!j._ok) { say($("call-msg"), j.error || "記録できませんでした", false); return; }
        var msg = "「" + result + "」で記録しました" + (j.dnc_added ? "（発信禁止に登録）" : "");
        // メモがあれば送信
        if (noteText) {
          api("/tac/calls/note", "POST", { to: current, note: noteText }).then(function(){
            say($("call-msg"), msg, true);
          });
        } else {
          say($("call-msg"), msg, true);
        }
        $("dispo-card").classList.add("hidden");
        current = null;
        $("to").value = "";
        if (fromList) { advance(); show("list"); }
      });
    });
  });

  // ---- スマートキュー ----
  var queueSort = "score";
  function loadQueue(searchQ){
    if (!token) return;
    var params = { sort: queueSort };
    if (searchQ) params.q = searchQ;
    api("/tac/calls/queue?" + new URLSearchParams(params).toString(), "GET").then(function(j){
      var box = $("queue-list"); box.textContent = "";
      var items = j.queue || [];
      if (items.length === 0) {
        var empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = searchQ ? "該当なし" : "リストが空です";
        box.appendChild(empty);
        return;
      }
      items.forEach(function(item){
        var row = document.createElement("div");
        row.className = "queue-item";
        var sc = document.createElement("div");
        sc.className = "queue-score";
        sc.textContent = item.score || "—";
        var info = document.createElement("div");
        info.className = "queue-info";
        var nm = document.createElement("div");
        nm.className = "queue-name";
        nm.textContent = item.name || item.number;
        var dt = document.createElement("div");
        dt.className = "queue-detail";
        dt.textContent = (item.area || "") + " " + (item.number || "");
        info.appendChild(nm);
        info.appendChild(dt);
        row.appendChild(sc);
        row.appendChild(info);
        row.addEventListener("click", function(){
          $("to").value = item.number;
          show("call");
        });
        box.appendChild(row);
      });
    });
  }
  $("queue-refresh").addEventListener("click", function(){ loadQueue($("queue-search").value.trim()); });
  $("queue-sort-score").addEventListener("click", function(){ queueSort = "score"; loadQueue($("queue-search").value.trim()); });
  $("queue-sort-name").addEventListener("click", function(){ queueSort = "name"; loadQueue($("queue-search").value.trim()); });
  $("queue-search").addEventListener("input", function(){ loadQueue(this.value.trim()); });

  // ---- 自動フォロー ----
  var followCat = "再調整希望";
  var CALLABLE = { "再調整希望": 1, "日程返答待ち": 1, "不在": 1 };
  function loadFollow(){
    if (!token) { show("settings"); return; }
    api("/tac/follow", "GET").then(function(j){
      var counts = j.counts || {};
      var cats = $("follow-cats"); cats.textContent = "";
      ["再調整希望","日程返答待ち","不在","要確認","連絡停止"].forEach(function(c){
        var sp = document.createElement("span");
        sp.className = "pill" + (c === followCat ? " pill-accent" : "");
        sp.style.cursor = "pointer";
        sp.textContent = c + " " + (counts[c] || 0);
        sp.addEventListener("click", function(){ followCat = c; loadFollow(); });
        cats.appendChild(sp);
      });
      var box = $("follow-list"); box.textContent = "";
      var items = (j.items || []).filter(function(e){ return e.category === followCat; });
      if (items.length === 0){
        var p = document.createElement("p"); p.className = "muted"; p.textContent = "該当なし";
        box.appendChild(p); return;
      }
      items.forEach(function(e){
        var row = document.createElement("div"); row.className = "queue-item";
        var callable = CALLABLE[e.category] && e.eligible !== false;
        var cb = document.createElement("input");
        cb.type = "checkbox"; cb.value = e.id; cb.className = "follow-cb";
        cb.disabled = !callable; cb.style.width = "20px"; cb.style.minHeight = "20px"; cb.style.flex = "0 0 auto";
        var info = document.createElement("div"); info.className = "queue-info";
        var nm = document.createElement("div"); nm.className = "queue-name";
        nm.textContent = (e.name || e.number) + (callable ? "" : "（対象外）");
        var dt = document.createElement("div"); dt.className = "queue-detail";
        dt.textContent = (e.basis || "") + " / 次:" + (e.next_action || "") +
          (e.follow_count ? " / 済" + e.follow_count + "回" : "");
        info.appendChild(nm); info.appendChild(dt);
        row.appendChild(cb); row.appendChild(info);
        box.appendChild(row);
      });
    });
  }
  $("follow-refresh").addEventListener("click", loadFollow);
  $("follow-promote").addEventListener("click", function(){
    var ids = Array.prototype.slice.call(document.querySelectorAll(".follow-cb:checked"))
      .map(function(c){ return c.value; });
    if (ids.length === 0){ say($("follow-msg"), "相手を選んでください", false); return; }
    api("/tac/follow/promote", "POST", null, { ids: ids }).then(function(j){
      if (!j._ok){ say($("follow-msg"), j.error || "追加できませんでした", false); return; }
      say($("follow-msg"), (j.moved || 0) + "件を発信リストに追加しました", true);
      loadFollow();
    });
  });

  // ---- 発信リスト（1件ずつ手動） ----
  function numbers(){ return load("tac_list", "").split("\n").map(function(s){ return s.trim(); }).filter(Boolean); }
  function pos(){ return parseInt(load("tac_list_pos", "0"), 10) || 0; }
  function renderList(){
    var ns = numbers(), i = pos();
    $("list").value = ns.join("\n");
    if (i >= ns.length) { $("list-next").textContent = ns.length ? "リスト完了" : "—"; $("list-pos").textContent = ""; $("list-dial").disabled = true; }
    else { $("list-next").textContent = ns[i]; $("list-pos").textContent = (i + 1) + " / " + ns.length + " 件目"; $("list-dial").disabled = false; }
  }
  function advance(){ save("tac_list_pos", String(pos() + 1)); renderList(); }
  $("list-save").addEventListener("click", function(){ save("tac_list", $("list").value); save("tac_list_pos", "0"); renderList(); });
  $("list-reset").addEventListener("click", function(){ save("tac_list_pos", "0"); renderList(); });
  $("list-skip").addEventListener("click", advance);
  $("list-dial").addEventListener("click", function(){
    var ns = numbers(), i = pos();
    if (i < ns.length) dial(ns[i], $("list-dial"), true);
  });

  // ---- 記録・集計 ----
  function refresh(){
    if (!token) return;
    api("/tac/calls/summary", "GET").then(function(j){
      var s = j.summary || {};
      $("sum-total").textContent = (s.total || 0) + " 件（番号 " + (s.unique_numbers || 0) + "）";
      var box = $("sum-status"); box.textContent = "";
      Object.keys(s.by_status || {}).sort().forEach(function(k){
        var sp = document.createElement("span"); sp.className = "pill";
        if (k === "dialed") sp.className += " pill-accent";
        sp.textContent = k + ": " + s.by_status[k]; box.appendChild(sp);
      });
    });
    // stats API でチャート描画
    api("/tac/calls/stats", "GET").then(function(j){
      var st = j.stats || {};
      $("stat-dialed").textContent = st.total_dialed || 0;
      $("stat-seiyaku").textContent = st.total_seiyaku || 0;
      var rate = st.total_dispositions > 0 ? Math.round(st.total_seiyaku / st.total_dispositions * 100) : 0;
      $("stat-rate").textContent = rate + "%";
      // 担当者別バーチャート
      var chart = $("agent-chart"); chart.textContent = "";
      var agents = st.by_agent || {};
      var names = Object.keys(agents);
      var maxDialed = 0;
      names.forEach(function(n){ if (agents[n].dialed > maxDialed) maxDialed = agents[n].dialed; });
      if (names.length === 0) {
        var nodata = document.createElement("p");
        nodata.className = "muted";
        nodata.textContent = "データなし";
        chart.appendChild(nodata);
      }
      names.forEach(function(n){
        var ag = agents[n];
        var row = document.createElement("div"); row.className = "bar-row";
        var lbl = document.createElement("div"); lbl.className = "bar-label"; lbl.textContent = n;
        var track = document.createElement("div"); track.className = "bar-track";
        var fill = document.createElement("div"); fill.className = "bar-fill";
        fill.style.width = (maxDialed > 0 ? Math.round(ag.dialed / maxDialed * 100) : 0) + "%";
        track.appendChild(fill);
        var val = document.createElement("div"); val.className = "bar-value"; val.textContent = ag.dialed;
        row.appendChild(lbl); row.appendChild(track); row.appendChild(val);
        chart.appendChild(row);
      });
    });
    // 折り返し予定
    api("/tac/calls/callbacks?today=true", "GET").then(function(j){
      var ul = $("cb-list"); ul.textContent = "";
      var cbs = j.callbacks || [];
      if (cbs.length === 0) {
        var li = document.createElement("li");
        li.className = "muted";
        li.textContent = "本日の予定なし";
        ul.appendChild(li);
      }
      cbs.forEach(function(c){
        var li = document.createElement("li");
        var a = document.createElement("span"); a.textContent = c.to || "";
        var b = document.createElement("span"); b.className = "muted";
        b.textContent = (c.callback_at || "").slice(11, 16) || "";
        li.appendChild(a); li.appendChild(b);
        li.style.cursor = "pointer";
        li.addEventListener("click", function(){ $("to").value = c.to; show("call"); });
        ul.appendChild(li);
      });
    });
    // 直近の記録
    api("/tac/calls?limit=20", "GET").then(function(j){
      var ul = $("recent"); ul.textContent = "";
      (j.calls || []).forEach(function(c){
        var li = document.createElement("li");
        var a = document.createElement("span"); a.textContent = c.to || "";
        var b = document.createElement("span"); b.className = "muted";
        b.textContent = (c.disposition || c.status || "") + " " + String(c.ts || "").slice(5, 16).replace("T", " ");
        li.appendChild(a); li.appendChild(b); ul.appendChild(li);
      });
    });
  }
  $("refresh").addEventListener("click", refresh);

  // ---- 設定 ----
  $("token").value = token;
  $("token-save").addEventListener("click", function(){
    token = $("token").value.trim(); save("tac_token", token);
    api("/tac/agents", "GET").then(function(j){
      if (j._ok) { say($("settings-msg"), "保存しました。接続OKです。", true); loadAgents(); }
      else say($("settings-msg"), j.error || "接続できませんでした", false);
    });
  });

  renderList();
  loadAgents();
  if (!token) show("settings");
})();
</script>
</body>
</html>
"""
