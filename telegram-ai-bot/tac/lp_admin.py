"""ライフパートナーの管理画面（集計・個別の回答）と、閲覧の権限。

権限（強い順）:
- admin   : 集計・個別の回答（電話番号は下 4 桁だけ）。TAC_OUTBOUND_TOKEN も admin として扱う
- viewer  : 集計・個別の回答
- analyst : 集計だけ

担当者は TAC_LP_USERS に「名前:権限:トークンの sha256」をカンマ区切りで設定する（平文のトークンは置かない）。
ハッシュは `python -m tac.lp_admin hash <トークン>` で作る。形式が崩れた行は誰にも権限を与えない。

少人数の集計の秘匿：回答・同意から数える件数は、1〜(k-1) 件なら伏せる（k = TAC_LP_MIN_CELL、既定 5）。
さらに「全体 − その件数」が 1〜(k-1) 件のときも伏せる（残りの少人数が分かってしまうため）。0 件は伏せない。
閲覧（集計・個別・拒否）は監査ログに残す。電話番号は監査ログに書かない。
"""

from __future__ import annotations

import hashlib
import hmac
import re

from .config import CONFIG

ROLES = {"analyst": 1, "viewer": 2, "admin": 3}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
INTERESTS = (
    ("lifestyle_interest", "生活改善への関心"),
    ("household_budget_interest", "家計見直しへの関心"),
    ("financial_education_interest", "金融教育への関心"),
    ("insurance_review_interest", "保険見直しへの関心"),
    ("future_life_interest", "将来の暮らしへの関心"),
)


# ---- 認証 ----
def _users() -> list[tuple[str, str, str]]:
    out = []
    for raw in (CONFIG.lp_users or "").split(","):
        parts = raw.strip().split(":")
        if len(parts) != 3:
            continue
        name, role, digest = (p.strip() for p in parts)
        if name and role in ROLES and _HEX64.match(digest.lower()):
            out.append((name, role, digest.lower()))
    return out


def authenticate(headers) -> tuple[str, str] | None:
    """(名前, 権限) か None。トークンはハッシュで比べ、どの担当者とも一致しなければ None。"""
    token = (headers.get("X-LP-Token") or "").strip()
    if token:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        for name, role, expected in _users():
            if hmac.compare_digest(digest, expected):
                return name, role
        return None
    tac = (headers.get("X-TAC-Token") or "").strip()
    if tac and CONFIG.outbound_token and hmac.compare_digest(tac.encode("utf-8"),
                                                            CONFIG.outbound_token.encode("utf-8")):
        return "operator", "admin"
    return None


def allows(role: str, need: str) -> bool:
    return ROLES.get(role, 0) >= ROLES[need]


# ---- 少人数の秘匿 ----
def cell(value: int, base: int | None = None) -> dict:
    k = max(int(CONFIG.lp_min_cell), 1)
    hidden = 0 < value < k or (base is not None and 0 < base - value < k)
    if hidden:
        return {"value": None, "suppressed": True, "display": f"{k}未満" if 0 < value < k else "非表示"}
    return {"value": value, "suppressed": False, "display": str(value)}


# ---- 集計 ----
def _count_permitted() -> tuple[int, int]:
    from . import dnc, survey

    entries = survey.load_list()
    ok = 0
    for e in entries:
        if not str(e.get("lead_source") or "").strip():
            continue
        if "survey" not in (e.get("permission_scope") or []):
            continue
        if not str(e.get("permission_evidence") or "").strip():
            continue
        number = str(e.get("number") or "")
        if not number or dnc.is_blocked(number):
            continue
        ok += 1
    return len(entries), ok


def summary() -> dict:
    from . import lp_db

    targets, permitted = _count_permitted()
    with lp_db.tx() as c:
        one = lambda q, *a: int(c.execute(q, a).fetchone()[0])  # noqa: E731
        dialed = one("SELECT COUNT(*) FROM call_attempts WHERE call_id IS NOT NULL")
        answered = one("SELECT COUNT(*) FROM call_attempts WHERE outcome IS NOT NULL AND outcome != 'DIAL_FAILED'")
        errors = one("SELECT COUNT(*) FROM call_attempts WHERE call_status='failed' OR outcome='ERROR'")
        completed = one("SELECT COUNT(*) FROM call_attempts WHERE outcome='COMPLETED'")
        started = one("SELECT COUNT(*) FROM contact_permissions WHERE purpose='survey' "
                      "AND status IN ('GRANTED', 'WITHDRAWN')")
        declined = one("SELECT COUNT(*) FROM contact_permissions WHERE purpose='survey' AND status='DECLINED'")
        dnc_n = one("SELECT COUNT(*) FROM dnc_entries")
        appts = one("SELECT COUNT(*) FROM appointments WHERE status IN ('REQUESTED', 'CONFIRMED')")
        responders = one("SELECT COUNT(*) FROM interest_profiles")
        interests = {col: one(f"SELECT COUNT(*) FROM interest_profiles WHERE {col}='YES'") for col, _ in INTERESTS}
        info = {p: one("SELECT COUNT(*) FROM contact_permissions WHERE purpose=? AND status='GRANTED'", p)
                for p in ("insurance_info", "material_info")}
        campaigns = [dict(r) for r in c.execute(
            "SELECT survey_id, survey_version, title, status, created_at FROM surveys ORDER BY created_at")]
    return {
        "campaigns": campaigns,
        "summary": {
            # 運用の件数（人の回答から数えないので、伏せない）
            "targets": targets, "permitted": permitted, "dialed": dialed, "answered": answered,
            "dnc": dnc_n, "errors": errors, "appointments": appts,
            # 回答・同意から数える件数（少人数は伏せる）
            "started": cell(started), "completed": cell(completed), "declined": cell(declined),
            "interests": {col: cell(n, responders) for col, n in interests.items()},
            "information_requested": {p: cell(n, started) for p, n in info.items()},
            "min_cell": int(CONFIG.lp_min_cell),
        },
    }


def responses(limit: int = 200) -> list[dict]:
    """個別の回答（viewer 以上）。電話番号は下 4 桁だけ。発話の原文は元から持たない。"""
    from . import lp_db

    out = []
    with lp_db.tx() as c:
        custs = c.execute(
            """SELECT DISTINCT c.customer_id, c.phone_e164 FROM customers c
               JOIN survey_responses r ON r.customer_id = c.customer_id
               ORDER BY c.customer_id DESC LIMIT ?""", (limit,)).fetchall()
        for row in custs:
            cid = row["customer_id"]
            answers = {r["question_id"]: ("SKIPPED" if r["skipped"] else r["answer_value"])
                       for r in c.execute("SELECT * FROM survey_responses WHERE customer_id=? ORDER BY response_id",
                                          (cid,))}
            versions = sorted({r[0] for r in c.execute(
                "SELECT survey_version FROM survey_responses WHERE customer_id=?", (cid,))})
            consents = {r["purpose"]: r["status"] for r in c.execute(
                "SELECT purpose, status FROM contact_permissions WHERE customer_id=?", (cid,))}
            out.append({"customer_id": cid, "phone": "****" + str(row["phone_e164"])[-4:],
                        "survey_versions": versions, "answers": answers, "consents": consents})
    return out


def record_view(actor: str, action: str, metadata: dict | None = None) -> None:
    from . import lp_db

    with lp_db.tx() as c:
        lp_db.audit(c, actor, action, None, metadata or {})


# ---- 画面（データは埋め込まない。トークンは sessionStorage にだけ置く） ----
_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>ライフパートナー 管理画面</title>
<style>
 :root{--bg:#f6f7f9;--surface:#fff;--ink:#1c2430;--muted:#5d6878;--line:#e2e6ec;--accent:#0f766e;--bar:#0f766e;--warn:#9a3412}
 @media (prefers-color-scheme:dark){:root{--bg:#12161c;--surface:#1a2029;--ink:#e6eaf0;--muted:#9aa5b5;--line:#2b3442;--accent:#2dd4bf;--bar:#2dd4bf;--warn:#fdba74}}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,"Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif}
 header{padding:16px;border-bottom:1px solid var(--line);background:var(--surface)}
 header h1{margin:0;font-size:18px} header p{margin:4px 0 0;color:var(--muted);font-size:13px}
 main{max-width:980px;margin:0 auto;padding:16px}
 .card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px}
 h2{font-size:15px;margin:0 0 12px}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
 .kpi{border:1px solid var(--line);border-radius:10px;padding:10px}
 .kpi .l{font-size:12px;color:var(--muted)} .kpi .v{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
 .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
 input{font:inherit;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);flex:1;min-width:0}
 button{font:inherit;border:0;border-radius:8px;padding:8px 14px;background:var(--accent);color:#fff;cursor:pointer}
 .bar{display:grid;grid-template-columns:160px 1fr 60px;gap:8px;align-items:center;margin:6px 0;font-size:13px}
 .track{height:10px;border-radius:5px;background:var(--line);overflow:hidden} .fill{height:100%;background:var(--bar)}
 .muted{color:var(--muted);font-size:12px} .warn{color:var(--warn)}
 table{width:100%;border-collapse:collapse;font-size:12px} th,td{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}
 .scroll{overflow-x:auto}
 @media (max-width:520px){.bar{grid-template-columns:110px 1fr 48px}}
</style>
</head>
<body>
<header><h1>ライフパートナー 管理画面</h1><p>生活意識調査の集計。少人数の集計は伏せて表示します。</p></header>
<main>
 <div class="card">
  <label class="muted" for="tok">担当者トークン</label>
  <div class="row" style="margin-top:6px"><input id="tok" type="password" autocomplete="off"><button id="go">表示</button></div>
  <p class="muted" id="who">トークンはこのタブの中だけ（sessionStorage）に置き、ページには埋め込みません。</p>
 </div>
 <div class="card"><h2>調査キャンペーン</h2><div class="scroll"><table><thead><tr><th>調査</th><th>版</th><th>状態</th></tr></thead><tbody id="camps"></tbody></table></div></div>
 <div class="card"><h2>架電と調査</h2><div class="grid" id="kpis"></div></div>
 <div class="card"><h2>関心（調査に答えた人のうち「関心あり」）</h2><div id="ints"></div>
  <p class="muted" id="cellnote"></p></div>
 <div class="card" id="respcard" hidden><h2>個別の回答（電話番号は下4桁のみ）</h2><div class="scroll"><table><thead><tr><th>番号</th><th>版</th><th>回答</th><th>同意</th></tr></thead><tbody id="resp"></tbody></table></div></div>
 <p class="muted" id="err"></p>
</main>
<script>
var $=function(id){return document.getElementById(id)};
function getTok(){try{return sessionStorage.getItem("lp_token")||""}catch(e){return ""}}
function setTok(v){try{sessionStorage.setItem("lp_token",v)}catch(e){}}
function api(p){return fetch(p,{headers:{"X-LP-Token":getTok()},cache:"no-store"}).then(function(r){if(!r.ok)throw r.status;return r.json()})}
function disp(x){return (x&&typeof x==="object")?x.display:String(x)}
function el(t,txt){var e=document.createElement(t);if(txt!==undefined)e.textContent=txt;return e}
var KPI=[["targets","対象者数"],["permitted","架電許可確認済み"],["dialed","発信件数"],["answered","応答件数"],
 ["started","調査開始件数"],["completed","調査完了件数"],["declined","回答拒否件数"],["dnc","DNC登録件数"],
 ["insurance_info","保険の案内を希望"],["material_info","資料の案内を希望"],["appointments","相談予約件数"],["errors","エラー件数"]];
var INT=[["lifestyle_interest","生活改善への関心"],["household_budget_interest","家計見直しへの関心"],
 ["financial_education_interest","金融教育への関心"],["insurance_review_interest","保険見直しへの関心"],["future_life_interest","将来の暮らしへの関心"]];
function render(d){
 var s=d.summary,k=$("kpis");k.textContent="";
 KPI.forEach(function(p){var v=s[p[0]]!==undefined?s[p[0]]:s.information_requested[p[0]];
  var b=el("div");b.className="kpi";b.appendChild(el("div",p[1])).className="l";b.appendChild(el("div",disp(v))).className="v";k.appendChild(b)});
 var c=$("camps");c.textContent="";
 d.campaigns.forEach(function(x){var tr=el("tr");[x.title||x.survey_id,x.survey_version,x.status].forEach(function(t){tr.appendChild(el("td",t))});c.appendChild(tr)});
 var max=Math.max(1,s.started.value||0),box=$("ints");box.textContent="";
 INT.forEach(function(p){var v=s.interests[p[0]],row=el("div");row.className="bar";row.appendChild(el("span",p[1]));
  var t=el("div");t.className="track";var f=el("div");f.className="fill";f.style.width=(v.value===null?0:Math.min(100,100*v.value/max))+"%";t.appendChild(f);row.appendChild(t);
  var n=el("span",v.display);if(v.suppressed)n.className="warn";row.appendChild(n);box.appendChild(row)});
 $("cellnote").textContent=s.min_cell+"件未満の集計と、残りが"+s.min_cell+"件未満になる集計は伏せています。";
}
function renderResp(rows){var b=$("resp");b.textContent="";rows.forEach(function(r){var tr=el("tr");
 tr.appendChild(el("td",r.phone));tr.appendChild(el("td",r.survey_versions.join(", ")));
 tr.appendChild(el("td",Object.keys(r.answers).map(function(k){return k+"="+r.answers[k]}).join(" / ")));
 tr.appendChild(el("td",Object.keys(r.consents).map(function(k){return k+"="+r.consents[k]}).join(" / ")));b.appendChild(tr)});$("respcard").hidden=false}
function load(){$("err").textContent="";
 api("/tac/lifepartner/api/me").then(function(me){$("who").textContent=me.name+"（"+me.role+"）として表示中";
  api("/tac/lifepartner/api/summary").then(render);
  if(me.role!=="analyst")api("/tac/lifepartner/api/responses").then(function(d){renderResp(d.responses)});else $("respcard").hidden=true;
 }).catch(function(e){$("err").textContent=(e===401?"トークンが違います。":"読み込めませんでした（"+e+"）")})}
$("go").onclick=function(){setTok($("tok").value.trim());$("tok").value="";load()};
if(getTok())load();
</script>
</body>
</html>
"""


def render() -> str:
    return _PAGE


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "hash":
        print(hashlib.sha256(argv[1].encode("utf-8")).hexdigest())
        return 0
    print("usage: python -m tac.lp_admin hash <token>")
    return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))

