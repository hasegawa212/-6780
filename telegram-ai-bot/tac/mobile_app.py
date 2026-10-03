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

# 結果ラベル（disposition.DECLINE="拒否" は自動で DNC 登録される）
DISPOSITIONS = ("成約", "検討", "折り返し", "不在", "拒否")


def manifest() -> dict:
    """ホーム画面に追加したときのアプリ定義（Web App Manifest）。"""
    return {
        "name": APP_NAME,
        "short_name": APP_NAME,
        "start_url": "/tac/app",
        "scope": "/tac/",
        "display": "standalone",
        "background_color": "#f6f5f2",
        "theme_color": "#c2410c",
        "lang": "ja",
    }


def render() -> str:
    """アプリ本体の HTML を返す（静的。トークンや電話番号は含まない）。"""
    buttons = "".join(
        f'<button class="dispo{" danger" if d == "拒否" else ""}" data-result="{d}">{d}</button>'
        for d in DISPOSITIONS
    )
    return _PAGE.replace("{{APP_NAME}}", APP_NAME).replace("{{DISPO_BUTTONS}}", buttons)


_PAGE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="{{APP_NAME}}">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="theme-color" content="#c2410c">
<link rel="manifest" href="/tac/app/manifest.webmanifest">
<title>{{APP_NAME}}</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1c1917;--muted:#78716c;--line:#e7e5e4;
--accent:#c2410c;--accent-ink:#fff;--ok:#15803d;--bad:#b91c1c;}
@media (prefers-color-scheme: dark){:root{--bg:#1c1917;--card:#292524;--ink:#f5f5f4;
--muted:#a8a29e;--line:#44403c;--accent:#fb923c;--accent-ink:#1c1917;--ok:#4ade80;--bad:#f87171;}}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.5 -apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif;-webkit-text-size-adjust:100%}
body{padding:calc(env(safe-area-inset-top) + 12px) 16px calc(env(safe-area-inset-bottom) + 84px)}
h1{font-size:20px;margin:4px 0 12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:12px}
label{display:block;font-size:13px;color:var(--muted);margin:10px 0 4px}
input,select,textarea{width:100%;font-size:17px;padding:12px;border:1px solid var(--line);
border-radius:10px;background:var(--bg);color:var(--ink)}
textarea{min-height:140px}
button{font-size:17px;border:0;border-radius:12px;padding:14px;cursor:pointer;
background:var(--line);color:var(--ink);min-height:48px}
button:disabled{opacity:.5}
.primary{background:var(--accent);color:var(--accent-ink);width:100%;font-weight:700;font-size:19px;margin-top:14px}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.row button{flex:1 1 30%}
.danger{background:var(--bad);color:#fff}
.big{font-size:26px;font-weight:700;letter-spacing:.02em;word-break:break-all}
.muted{color:var(--muted);font-size:13px}
.msg{margin-top:10px;font-size:14px;min-height:1.5em}
.msg.ok{color:var(--ok)} .msg.bad{color:var(--bad)}
.hidden{display:none}
ul.list{list-style:none;margin:0;padding:0}
ul.list li{padding:10px 0;border-bottom:1px solid var(--line);font-size:14px;display:flex;justify-content:space-between;gap:8px}
.pills{display:flex;flex-wrap:wrap;gap:6px}
.pill{background:var(--bg);border:1px solid var(--line);border-radius:999px;padding:4px 10px;font-size:13px}
nav{position:fixed;left:0;right:0;bottom:0;display:flex;background:var(--card);border-top:1px solid var(--line);
padding-bottom:env(safe-area-inset-bottom)}
nav button{flex:1;background:none;border-radius:0;font-size:14px;color:var(--muted)}
nav button.on{color:var(--accent);font-weight:700}
</style>
</head>
<body>
<h1>{{APP_NAME}}</h1>

<section id="tab-call">
  <div class="card">
    <label for="to">かける番号</label>
    <input id="to" type="tel" inputmode="tel" autocomplete="off" placeholder="090-1234-5678">
    <label for="agent">つなぐ担当者</label>
    <select id="agent"><option value="">自動で振り分け</option></select>
    <button class="primary" id="dial">📞 発信する</button>
    <p class="muted">相手が出たら、担当者の電話が鳴ります。1回のタップで1件だけ発信します。</p>
    <div class="msg" id="call-msg"></div>
  </div>
  <div class="card hidden" id="dispo-card">
    <div class="muted">通話の結果</div>
    <div class="big" id="dispo-to"></div>
    <div class="row" id="dispo-buttons">{{DISPO_BUTTONS}}</div>
    <p class="muted">「拒否」を選ぶと発信禁止リストに入り、次から掛からなくなります。</p>
  </div>
</section>

<section id="tab-list" class="hidden">
  <div class="card">
    <label for="list">発信リスト（1行に1番号）</label>
    <textarea id="list" placeholder="090-1111-2222&#10;03-1234-5678"></textarea>
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

<section id="tab-today" class="hidden">
  <div class="card">
    <div class="muted">これまでの合計</div>
    <div class="big" id="sum-total">—</div>
    <div class="pills" id="sum-status"></div>
    <div class="row"><button id="refresh">更新</button></div>
  </div>
  <div class="card"><div class="muted">直近の記録</div><ul class="list" id="recent"></ul></div>
</section>

<section id="tab-settings" class="hidden">
  <div class="card">
    <label for="token">操作者トークン（TAC_OUTBOUND_TOKEN）</label>
    <input id="token" type="password" autocomplete="off">
    <button class="primary" id="token-save">保存</button>
    <p class="muted">この iPhone の中にだけ保存されます。Safari の共有ボタン →「ホーム画面に追加」でアプリになります。</p>
    <div class="msg" id="settings-msg"></div>
  </div>
</section>

<nav>
  <button data-tab="call" class="on">発信</button>
  <button data-tab="list">リスト</button>
  <button data-tab="today">記録</button>
  <button data-tab="settings">設定</button>
</nav>

<script>
(function(){
  "use strict";
  var $ = function(id){ return document.getElementById(id); };
  function load(k, d){ try { var v = localStorage.getItem(k); return v === null ? d : v; } catch(e){ return d; } }
  function save(k, v){ try { localStorage.setItem(k, v); } catch(e){} }
  var token = load("tac_token", "");
  var current = null;      // 結果待ちの番号
  var fromList = false;    // リスト経由の発信か

  function say(el, text, ok){ el.textContent = text; el.className = "msg " + (ok ? "ok" : "bad"); }

  function api(path, method, params){
    var opt = { method: method, headers: { "X-TAC-Token": token } };
    if (params) {
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
    ["call","list","today","settings"].forEach(function(t){ $("tab-" + t).classList.toggle("hidden", t !== name); });
    tabs.forEach(function(b){ b.classList.toggle("on", b.getAttribute("data-tab") === name); });
    if (name === "today") refresh();
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
      b.disabled = true;
      api("/tac/calls/disposition", "POST", { to: current, result: result }).then(function(j){
        b.disabled = false;
        if (!j._ok) { say($("call-msg"), j.error || "記録できませんでした", false); return; }
        say($("call-msg"), "「" + result + "」で記録しました" + (j.dnc_added ? "（発信禁止に登録）" : ""), true);
        $("dispo-card").classList.add("hidden");
        current = null;
        $("to").value = "";
        if (fromList) { advance(); show("list"); }
      });
    });
  });

  // ---- 発信リスト（1件ずつ手動） ----
  function numbers(){ return load("tac_list", "").split("\\n").map(function(s){ return s.trim(); }).filter(Boolean); }
  function pos(){ return parseInt(load("tac_list_pos", "0"), 10) || 0; }
  function renderList(){
    var ns = numbers(), i = pos();
    $("list").value = ns.join("\\n");
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

  // ---- 記録・集計（更新ボタンで手動更新） ----
  function refresh(){
    if (!token) return;
    api("/tac/calls/summary", "GET").then(function(j){
      var s = j.summary || {};
      $("sum-total").textContent = (s.total || 0) + " 件（番号 " + (s.unique_numbers || 0) + "）";
      var box = $("sum-status"); box.textContent = "";
      Object.keys(s.by_status || {}).sort().forEach(function(k){
        var sp = document.createElement("span"); sp.className = "pill";
        sp.textContent = k + ": " + s.by_status[k]; box.appendChild(sp);
      });
    });
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
