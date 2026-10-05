"""自動フォロー ダッシュボード（担当者用の操作画面）。

設計図「フォロー管理ダッシュボード」に対応。次に掛ける1件・判定の根拠・
ON/OFF・一時停止・1件プレビュー/実行を、1画面で操作できる。

セキュリティ: 操作者トークンはページに埋め込まない。端末の localStorage
（キー tac_token＝モバイルアプリと共通）から読み、X-TAC-Token で送る。
ページ自体には個人情報を含まない。
"""

from __future__ import annotations

APP_NAME = "自動フォロー"

_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>自動フォロー ダッシュボード</title>
<style>
 :root{ --fire:#c2410c; --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb; --ok:#16a34a; }
 *{box-sizing:border-box}
 body{font-family:-apple-system,"Hiragino Kaku Gothic ProN",sans-serif;margin:0;
      background:#f8fafc;color:var(--ink)}
 header{background:var(--fire);color:#fff;padding:16px 20px;display:flex;
        align-items:center;gap:10px}
 header .logo{font-size:22px} header h1{font-size:18px;margin:0}
 main{max-width:860px;margin:0 auto;padding:20px}
 .card{background:#fff;border:1px solid var(--line);border-radius:14px;
       padding:18px;margin-bottom:16px}
 .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
 .pill{padding:4px 10px;border-radius:999px;font-size:13px;font-weight:700}
 .pill.on{background:#dcfce7;color:#166534} .pill.off{background:#f1f5f9;color:#475569}
 .pill.pause{background:#fef9c3;color:#854d0e}
 button{font:inherit;border:0;border-radius:10px;padding:10px 14px;cursor:pointer}
 button.primary{background:var(--fire);color:#fff;font-weight:700}
 button.ghost{background:#fff;border:1px solid var(--line);color:var(--ink)}
 button.warn{background:#fee2e2;color:#991b1b;font-weight:700}
 .muted{color:var(--muted);font-size:13px}
 .big{font-size:20px;font-weight:800}
 label{font-size:13px;color:var(--muted)} input{font:inherit;padding:8px;border:1px solid var(--line);border-radius:8px;width:100%}
 .kv{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px dashed var(--line)}
 .safe{background:#ecfdf5;border:1px solid #a7f3d0;border-radius:10px;padding:10px;font-size:13px;color:#065f46}
</style>
</head>
<body>
<header><span class="logo">🔥</span><h1>自動フォロー ダッシュボード</h1></header>
<main>

 <div class="card" id="token-card" style="display:none">
   <label for="token">操作者トークン（TAC_OUTBOUND_TOKEN）</label>
   <div class="row" style="margin-top:6px">
     <input id="token" type="password" autocomplete="off" aria-label="操作者トークン">
     <button class="primary" id="token-save">保存</button>
   </div>
   <p class="muted">端末内にだけ保存します（localStorage）。ページには埋め込みません。</p>
 </div>

 <div class="card">
   <div class="row" style="justify-content:space-between">
     <div>エンジン状態：<span id="state" class="pill off">—</span></div>
     <div class="row">
       <button class="primary" id="btn-on">ONにする</button>
       <button class="ghost" id="btn-pause">一時停止</button>
       <button class="warn" id="btn-off">OFFにする（停止）</button>
     </div>
   </div>
   <p class="muted" id="state-msg" style="margin-top:8px"></p>
 </div>

 <div class="card">
   <div class="big">次に掛ける1件</div>
   <div id="next-box" style="margin-top:10px">
     <div class="kv"><span>お客様</span><span id="n-name">—</span></div>
     <div class="kv"><span>分類</span><span id="n-cat">—</span></div>
     <div class="kv"><span>電話番号</span><span id="n-num">—</span></div>
     <div class="kv"><span>判定の根拠</span><span id="n-reason">—</span></div>
   </div>
   <div class="row" style="margin-top:14px">
     <button class="ghost" id="btn-refresh">更新</button>
     <button class="ghost" id="btn-preview">プレビュー（発信しない）</button>
     <button class="primary" id="btn-run">この1件に発信（ON時のみ）</button>
   </div>
   <p class="muted" id="run-msg" style="margin-top:8px"></p>
 </div>

 <div class="safe">
   安全装置：同意なし・拒否(DNC)・時間帯外・本日発信済み・上限到達は自動でスキップ。
   OFF／一時停止のときは1件も発信しません。1回の「発信」で最大1件（一斉自動発信ではありません）。
 </div>

</main>
<script>
 var $=function(id){return document.getElementById(id)};
 function load(k,d){try{var v=localStorage.getItem(k);return v===null?d:v}catch(e){return d}}
 function save(k,v){try{localStorage.setItem(k,v)}catch(e){}}
 var token=load("tac_token","");
 function api(path,method,body){
   var opt={method:method||"GET",headers:{"X-TAC-Token":token}};
   if(body){opt.headers["Content-Type"]="application/json";opt.body=JSON.stringify(body)}
   return fetch(path,opt).then(function(r){return r.json().catch(function(){return{}})});
 }
 function renderState(eng){
   var el=$("state");
   if(!eng){el.className="pill off";el.textContent="不明";return}
   if(eng.paused){el.className="pill pause";el.textContent="一時停止中"}
   else if(eng.enabled){el.className="pill on";el.textContent="ON（稼働）"}
   else{el.className="pill off";el.textContent="OFF（停止）"}
 }
 function refresh(){
   if(!token){$("token-card").style.display="block";return}
   api("/tac/autofollow/status").then(function(d){
     if(d&&d.ok){
       renderState(d.engine);
       var n=d.next||{};
       $("n-name").textContent=n.name||"—";
       $("n-cat").textContent=n.category||"—";
       $("n-num").textContent=n.number||"—";
       $("n-reason").textContent=d.reason||"—";
       $("state-msg").textContent="";
     }else{ $("state-msg").textContent=(d&&d.error)||"取得できませんでした"; }
   });
 }
 function toggle(body,msg){ api("/tac/autofollow/toggle","POST",body).then(function(d){
   if(d&&d.ok){renderState(d.engine);$("state-msg").textContent=msg||"更新しました";refresh();}
   else{$("state-msg").textContent=(d&&d.error)||"失敗しました";}
 });}
 $("btn-on").addEventListener("click",function(){toggle({enabled:true,paused:false},"ONにしました");});
 $("btn-off").addEventListener("click",function(){toggle({enabled:false},"OFFにしました（停止）");});
 $("btn-pause").addEventListener("click",function(){toggle({paused:true},"一時停止にしました");});
 $("btn-refresh").addEventListener("click",refresh);
 $("btn-preview").addEventListener("click",function(){
   api("/tac/autofollow/run","POST",{}).then(function(d){
     $("run-msg").textContent=d&&d.ok?("プレビュー: "+(d.would_place?"発信できます":"対象なし")+"（"+(d.reason||"")+"）"):"失敗";
   });
 });
 $("btn-run").addEventListener("click",function(){
   if(!confirm("この1件に実際に発信します。よろしいですか？"))return;
   api("/tac/autofollow/run","POST",{execute:true}).then(function(d){
     $("run-msg").textContent=d&&d.ok?(d.placed?"発信しました":("発信しませんでした："+(d.reason||""))):"失敗";
     refresh();
   });
 });
 $("token-save").addEventListener("click",function(){
   token=$("token").value.trim();save("tac_token",token);
   if(token){$("token-card").style.display="none";refresh();}
 });
 if(!token){$("token-card").style.display="block";}else{refresh();}
</script>
</body>
</html>"""


def render() -> str:
    return _PAGE
