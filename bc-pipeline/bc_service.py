"""BC 自動生成サービス（FastAPI）.

AB 側（仕入れ）重要事項説明書を読み込み、BC 側（B→C 転売）の重説を生成する。

エンドポイント:
  GET  /health   … 死活監視
  POST /extract  … AB重説(PDF/画像/テキスト) → 構造化 JSON（Juyojiko）
  POST /generate … AB重説JSON ＋案件マスタ → BC重説(.xlsx) を base64 で返す

環境変数:
  ANTHROPIC_API_KEY  … Claude（/extract のみ必須）
  ANTHROPIC_BASE_URL … 社内 LiteLLM プロキシ等に向ける場合（任意）
  CLAUDE_MODEL       … 既定 claude-opus-4-8
  CLAUDE_MAX_TOKENS  … 既定 4000
"""

from __future__ import annotations

import base64
import io
import json
import math
import os, gc
from typing import Any

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from openpyxl import load_workbook
from pydantic import BaseModel, ConfigDict

import approval
import audit_log
import csrf_protection
import auth
import bundle
import cellmaps
import validate
import juyojiko_excel
import keiyaku_excel
import touki_parser
try:
    import manus_client
except Exception:
    manus_client = None  # type: ignore[assignment]
import wb_fill
from bc_schema import YOTO_OPTIONS, normalize_yoto, resolve_bukken
from bc_transform import transform_ab_to_bc, transform_keiyaku_ab_to_bc, juyojiko_to_keiyakusho
import crm_lookup
from juyojiko_schema import (
    FudosanHyoji,
    HoreiSeigen,
    Juyojiko,
    TatemonoHyoji,
    TochiHyoji,
    TorihikiJoken,
)
from keiyaku_schema import Keiyakusho

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")
MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "8000"))

app = FastAPI(title="BC自動生成サービス", version="0.2.0", root_path="/bc")

# クラウド等の揮発性環境向け: env で指定されたログインユーザーを起動時に用意する。
auth.ensure_bootstrap_user()

_WEBUI = Path(__file__).parent / "webui" / "index.html"
_LOGIN = Path(__file__).parent / "webui" / "login.html"
_CHECKLIST = Path(__file__).parent / "webui" / "checklist.html"
_PLAYBOOK_DIR = Path(__file__).parent / "webui" / "playbook"

# 認証なしでアクセスできるパス（ログイン画面・死活監視・favicon）。
_PUBLIC_PATHS = {"/login", "/logout", "/health", "/favicon.ico"}


@app.middleware("http")
async def _auth_gate(request: Request, call_next):  # type: ignore[no-untyped-def]
    """認証有効時、未ログインのアクセスを遮断する。ブラウザ操作(GET)はログイン画面へ
    リダイレクト、API(POST等)は 401 JSON を返す。認証無効時は素通り（後方互換）。"""
    if not auth.is_enabled() or request.url.path in _PUBLIC_PATHS:
        return await call_next(request)
    if auth.current_user(request.cookies):
        return await call_next(request)
    accepts_html = "text/html" in request.headers.get("accept", "")
    if request.method == "GET" and accepts_html:
        nxt = request.url.path
        return RedirectResponse(f"/login?next={nxt}", status_code=303)
    return JSONResponse({"detail": "ログインが必要です。"}, status_code=401)


def _safe_next(nxt: str) -> str:
    """遷移先を自ホスト内パスのみ許可（オープンリダイレクト防止）。
    `//host` や `/\\host`（ブラウザが //host に正規化）を弾く。"""
    if nxt.startswith("/") and not nxt.startswith(("//", "/\\")):
        return nxt
    return "/"


def _set_session_cookie(resp: Any, username: str) -> None:
    resp.set_cookie(
        auth.COOKIE_NAME, auth.create_session(username),
        httponly=True, samesite="lax", secure=auth.is_secure_cookie(), path="/",
    )


@app.get("/", response_class=HTMLResponse)
def webui() -> str:
    """ブラウザ用の操作画面（AB読取→BC情報入力→BC一式ダウンロード）。"""
    if _WEBUI.exists():
        return _WEBUI.read_text(encoding="utf-8")
    return "<h1>BC自動生成サービス</h1><p>webui/index.html が見つかりません。</p>"


@app.get("/login", response_class=HTMLResponse)
def login_page(next: str = "/", error: str = "") -> HTMLResponse:
    """ログイン画面。認証が無効なら操作画面へ戻す。"""
    if not auth.is_enabled():
        return RedirectResponse("/", status_code=303)  # type: ignore[return-value]
    html = _LOGIN.read_text(encoding="utf-8") if _LOGIN.exists() else \
        "<form method=post action=/login>ID<input name=username> " \
        "PW<input name=password type=password><button>ログイン</button></form>"
    banner = '<div class="err">IDまたはパスワードが違います。</div>' if error else ""
    # next は自ホスト内パスのみ許可＋HTMLエスケープ（反射XSS・オープンリダイレクト防止）
    from html import escape as _esc
    html = html.replace("<!--ERROR-->", banner).replace("__NEXT__", _esc(_safe_next(next), quote=True))
    return HTMLResponse(html)


@app.post("/login")
async def login_submit(request: Request) -> Any:
    """ログイン処理。成功でセッションクッキーを発行し操作画面へ。

    フォームは application/x-www-form-urlencoded。request.form() で読むため
    ファイルアップロード用の python-multipart には依存しない。
    """
    from urllib.parse import parse_qs
    body = (await request.body()).decode("utf-8")
    form = parse_qs(body, keep_blank_values=True)
    username = (form.get("username") or [""])[0]
    password = (form.get("password") or [""])[0]
    nxt = (form.get("next") or ["/"])[0]
    ip = str(request.client.host) if request.client else ""
    if csrf_protection.is_locked(username, ip):
        remaining = csrf_protection.get_remaining_lockout(username, ip)
        audit_log.log_action("login_locked", user=username, success=False, ip=ip, detail=f"locked {remaining}s")
        return RedirectResponse("/login?error=locked", status_code=303)
    if not auth.authenticate(username, password):
        csrf_protection.record_failure(username, ip)
        audit_log.log_action("login_fail", user=username, success=False, ip=str(request.client.host) if request.client else "")
        return RedirectResponse("/login?error=1", status_code=303)
    csrf_protection.record_success(username, ip)
    audit_log.log_action("login", user=username, role=auth.get_role(username) or "", success=True, ip=str(request.client.host) if request.client else "")
    resp = RedirectResponse(_safe_next(nxt), status_code=303)
    _set_session_cookie(resp, username)
    gc.collect()  # メモリ解放
    return resp


@app.post("/logout")
@app.get("/logout")
def logout() -> Any:
    """ログアウト（セッションクッキーを破棄）。"""
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp


@app.get("/me")
def me(request: Request) -> dict[str, Any]:
    """ログイン中ユーザー情報（webui のユーザー表示用）。"""
    if not auth.is_enabled():
        return {"auth_enabled": False, "username": None, "display_name": None}
    user = auth.current_user(request.cookies)
    return {
        "auth_enabled": True,
        "username": user,
        "display_name": auth.display_name(user) if user else None,
    }


@app.get("/favicon.ico")
def favicon() -> Response:
    """ブラウザのfavicon要求に204を返す（コンソールの404を消す）。"""
    return Response(status_code=204)


# ── リクエスト/レスポンス ─────────────────────────────────────
class GenerateReq(BaseModel):
    model_config = ConfigDict(extra="allow")

    doc_type: str = "juyojiko"          # juyojiko（重説） / keiyaku（契約書） / package（両方）
    # 本番ワークブック差込: テンプレ変種（36-1 / 37-1 / 38-1）。指定時は差込を試みる。
    template: str | None = None
    template_base64: str | None = None  # 御社ワークブックを直接渡す場合
    # 新方式: AB 書類の構造化 JSON（/extract の出力）
    ab: dict[str, Any] | None = None        # 重説 JSON（package では重説シート用）
    ab_keiyaku: dict[str, Any] | None = None  # 契約書 JSON（package で契約書シート用）
    # 旧方式（手順書 curl 互換）: 最小フィールド（重説のみ）
    bukken: str | None = None
    extracted: dict[str, Any] | None = None
    # 案件マスタ（BC 側の当事者・代金など）
    deal_master: dict[str, Any] = {}
    filename: str | None = None


class GenerateResp(BaseModel):
    filename: str
    bukken: str
    xlsx_base64: str
    # 発行前チェック（記入漏れ・不整合・御社標準からの逸脱）。空なら問題なし。
    warnings: list[dict[str, str]] = []
    # BC 販売価格を自動計算した場合の内訳（明示指定時は None）
    price_calc: dict[str, Any] | None = None
    # 住所から取得した法令制限（推定）・ハザード判定
    geo_info: dict[str, Any] | None = None
    # 空欄を自動補完した既定値（日付・印紙・手付・仲介手数料など）。UI 確認用。
    auto_defaults: dict[str, Any] | None = None


class ExtractReq(BaseModel):
    doc_type: str = "juyojiko"          # juyojiko（重説） / keiyaku（売買契約書）
    bukken: str | None = None
    text: str | None = None
    file_base64: str | None = None
    mime: str = "application/pdf"


class ExtractResp(BaseModel):
    extracted: dict[str, Any]
    # 自動読取が部分的/不能だった場合の非致命メッセージ（空なら全て正常）。
    # UI はこれを注意表示しつつ、手入力で先へ進める（読取失敗でも止めない）。
    warning: str = ""


# ── /health ───────────────────────────────────────────────────
@app.get("/health")
def health(request: Request) -> dict[str, Any]:
    """死活監視は常時公開だが、設定詳細（base_url・テンプレ一覧等）は
    認証有効時はログイン済みにだけ返す（内部構成の漏えい防止）。"""
    base = {"status": "ok", "version": "0.2.0"}
    if auth.is_enabled() and not auth.current_user(request.cookies):
        return base
    tdir = os.environ.get("BC_TEMPLATE_DIR", "templates")
    templates = sorted(
        p.stem for p in __import__("pathlib").Path(tdir).glob("*.xlsx")
    ) if os.path.isdir(tdir) else []
    return {
        **base,
        "model": MODEL,
        "bukken": ["戸建", "区分"],
        # 設定の見える化（秘密情報は出さない）
        "api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "base_url": os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        "template_dir": tdir,
        "templates_available": templates,   # 例: ["36-1","37-1","38-1"]
    }


# ── /reference（法令制限の正式名称マスタ）──────────────────────
@app.get("/reference")
def reference() -> dict[str, Any]:
    import horei_master

    import house_style as H
    return {
        "yoto": horei_master.YOTO_OPTIONS,
        "chiiki_chiku": horei_master.CHIIKI_CHIKU,
        "other_horei": horei_master.OTHER_HOREI_LAWS,
        # 特約テンプレート（小玉宅建士の実務書式）
        "tokuyaku_templates": {
            "chukan_shoryaku": {"title": H.TOKUYAKU_CHUKAN_SHORYAKU_TITLE,
                                "body": H.TOKUYAKU_CHUKAN_SHORYAKU},
            "teitoken_jokyo": {"title": H.TOKUYAKU_TEITOKEN_JOKYO_TITLE,
                               "body": H.TOKUYAKU_TEITOKEN_JOKYO},
        },
    }


@app.get("/masters")
def masters() -> dict[str, Any]:
    """アプリのプリセット用マスタ（売主業者B＝御社・御社取引士・媒介業者）。

    アプリはこれを使って「選ぶだけ」のドロップダウンを作り、入力を買主C・価格に絞る。
    """
    import house_style

    return {
        "seller_b": house_style.SELLER_B_MASTER,
        "seller_b_torikiishi": house_style.SELLER_B_TORIKIISHI,
        "baikai_gyosha": house_style.BAIKAI_GYOSHA_MASTER,
        # 特約テンプレのチェックボックス定義（UI が生成に使う）
        "tokuyaku_menu": house_style.TOKUYAKU_TEMPLATE_MENU,
        # 過去案件テンプレ（「過去案件から複製」ドロップダウン）
        "past_deals": house_style.PAST_DEALS,
    }


# ── /bundle（添付書類のPDF結合）────────────────────────────────
class BundleReq(BaseModel):
    attachments: list[str]        # base64 PDF（結合する順）
    filename: str | None = None


class BundleResp(BaseModel):
    filename: str
    page_count: int
    pdf_base64: str


@app.post("/bundle", response_model=BundleResp)
def bundle_pdfs(req: BundleReq) -> BundleResp:
    if not req.attachments:
        raise HTTPException(status_code=400, detail="attachments が空です。")
    try:
        pdfs = [base64.b64decode(a) for a in req.attachments]
        merged, pages = bundle.merge_pdfs(pdfs)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"PDF結合に失敗: {e}") from e
    return BundleResp(
        filename=req.filename or "添付書類束.pdf",
        page_count=pages,
        pdf_base64=base64.b64encode(merged).decode("ascii"),
    )


# ── /approval（Slack承認 ✅/❌ の判定）─────────────────────────
class ApprovalReq(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Slack の url_verification ハンドシェイク用
    type: str | None = None
    challenge: str | None = None
    # 簡易形式: {"reaction":"✅"}。Events API の場合は event.reaction を読む。
    reaction: str | None = None
    event: dict[str, Any] | None = None


@app.post("/approval")
def approval_hook(req: ApprovalReq) -> dict[str, Any]:
    # Slack Events API の URL 検証（challenge をそのまま返す）
    if req.type == "url_verification" and req.challenge:
        return {"challenge": req.challenge}
    reaction = approval.reaction_from_payload(req.model_dump(exclude_none=True))
    decision = approval.decide(reaction)
    audit_log.log_action("approval", user="webhook", detail=f"decision:{decision} reaction:{reaction}")
    return {"decision": decision, "approved": decision == "approve", "reaction": reaction}




# ── /diff（差分表示） ─────────────────────────────────────────
@app.get("/diff", response_class=HTMLResponse)
def diff_view(request: Request) -> HTMLResponse:
    """AB抽出値とBC反映値の差分を表示"""
    user = auth.current_user(dict(request.cookies))
    if not user:
        return RedirectResponse("/login", status_code=303)
    
    import json
    from pathlib import Path
    
    # 最新の抽出結果を読み込む
    test_file = Path("saved_docs/test/e2e_keiyaku.json")
    if not test_file.exists():
        return HTMLResponse("<h2>抽出データがありません</h2>")
    
    data = json.loads(test_file.read_text())
    ext = data.get("extracted", {})
    joken = ext.get("joken", {})
    tochi = ext.get("tochi", {})
    tatemono = ext.get("tatemono", {})
    
    # 差分項目リスト
    items = [
        ("売主氏名", ext.get("urinushi", {}).get("name", ""), "株式会社Martial Arts", "売主"),
        ("買主氏名", ext.get("kainushi", {}).get("name", ""), "(案件マスタから)", "買主"),
        ("物件所在地", tochi.get("shozai", ""), tochi.get("shozai", ""), "物件"),
        ("地番", tochi.get("chiban", ""), tochi.get("chiban", ""), "物件"),
        ("家屋番号", tatemono.get("kaoku_bango", ""), tatemono.get("kaoku_bango", ""), "物件"),
        ("売買代金", str(joken.get("baibai_daikin", "")), "(BC価格を入力)", "金額"),
        ("手付金", str(joken.get("tetsuke", "")), "(BC手付を入力)", "金額"),
        ("残代金", str(joken.get("zankin", "")), "(BC残代を入力)", "金額"),
        ("契約日", joken.get("keiyaku_date", ""), "(BC契約日を入力)", "日付"),
        ("決済日", joken.get("kessai_date", ""), "(BC決済日を入力)", "日付"),
        ("土地面積", str(tochi.get("menseki", "")), str(tochi.get("menseki", "")), "面積"),
        ("建物面積", str(tatemono.get("menseki", "")), str(tatemono.get("menseki", "")), "面積"),
        ("特約", ext.get("tokuyaku", ""), "(引継ぎ+BC注記)", "特約"),
    ]
    
    rows_html = ""
    has_issue = False
    for name, ab_val, bc_val, category in items:
        if not ab_val and not bc_val:
            status = "未抽出"
            status_class = "未抽出"
            has_issue = True
        elif ab_val == bc_val:
            status = "一致"
            status_class = "一致"
        elif "入力" in str(bc_val) or "マスタ" in str(bc_val):
            status = "要確認"
            status_class = "要確認"
            has_issue = True
        else:
            status = "不一致"
            status_class = "不一致"
            has_issue = True
        
        rows_html += f"""<tr class="{status_class}">
            <td>{name}</td><td>{category}</td>
            <td>{ab_val or '—'}</td><td>{bc_val or '—'}</td>
            <td><b>{status}</b></td></tr>"""
    
    warning = ""
    if has_issue:
        warning = '<div class="warn">⚠️ 要確認・未抽出・不一致があります。全項目を確認してから承認してください。</div>'
    
    html = f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>差分確認 | BC自動生成</title>
    <style>
    body{{font-family:'Noto Sans JP',sans-serif;background:#0a0a0f;color:#e8e8e8;padding:16px}}
    h1{{color:#ff5a1f;text-align:center;font-size:20px}}
    table{{width:100%;border-collapse:collapse;margin:16px 0;font-size:13px}}
    th{{background:#002060;color:#fff;padding:8px;text-align:left}}
    td{{padding:6px 8px;border-bottom:1px solid #333}}
    .一致 td{{background:#0a1a0a}}
    .不一致 td{{background:#2a0a0a}}
    .未抽出 td{{background:#2a1a00}}
    .要確認 td{{background:#1a1a00}}
    .warn{{background:#2a1a00;border:1px solid #ff5a1f;padding:12px;border-radius:8px;margin:12px 0;text-align:center}}
    .info{{color:#888;font-size:11px;text-align:center;margin:8px}}
    </style></head><body>
    <h1>📋 差分確認（AB→BC）</h1>
    <p class="info">ユーザー: {user} | 抽出項目: {len(ext)}件</p>
    {warning}
    <table><tr><th>項目名</th><th>区分</th><th>AB抽出値</th><th>BC反映値</th><th>状態</th></tr>
    {rows_html}</table>
    <p class="info">状態: 一致 / 不一致 / 未抽出 / 要確認 / 人間修正済み</p>
    </body></html>"""
    
    return HTMLResponse(html)


# ── /generate ─────────────────────────────────────────────────
def _legacy_to_juyojiko(bukken: str, extracted: dict[str, Any]) -> Juyojiko:
    """手順書 curl の最小フィールドを Juyojiko へマップ（後方互換）."""
    key = resolve_bukken(bukken)
    shozai = extracted.get("shozai")
    return Juyojiko(
        bukken_type=key,
        fudosan=FudosanHyoji(
            bukken_type=key,
            jukyo_hyoji=shozai,
            tochi=TochiHyoji(shozai=shozai) if key == "戸建" else None,
            tatemono=TatemonoHyoji() if key == "戸建" else None,
            senyuu=TatemonoHyoji() if key == "区分" else None,
            ittou_shozai=shozai if key == "区分" else None,
        ),
        horei=HoreiSeigen(
            kuiki_kubun=extracted.get("kuiki"),
            yoto=normalize_yoto(extracted.get("yoto")),
            nijuni_jo=extracted.get("nijuni_jo"),
            kenpei=extracted.get("kenpei"),
            yoseki=extracted.get("yoseki"),
        ),
        joken=TorihikiJoken(),
    )


def _shozai_of(f: Any) -> str:
    if not f:
        return "物件"
    tochi = getattr(f, "tochi", None)
    return (getattr(f, "jukyo_hyoji", None) or getattr(f, "ittou_shozai", None)
            or (getattr(tochi, "shozai", None) if tochi else None) or "物件")


def _filename(prefix: str, bukken: str, f: Any, override: str | None) -> str:
    if override:
        return override
    safe = "".join(c for c in str(_shozai_of(f)) if c not in r'\/:*?"<>|').strip()
    return f"{prefix}_{bukken}_{safe[:40]}.xlsx"


# ── BC 販売価格の自動計算 ──────────────────────────────────────
# 販売価格 = (仕入 + リフォーム + 目標利幅) / (1 - 諸経費率)
#   諸経費が「販売価格×率」なので、販売価格について解くと上式になる。
BC_REFORM_DEFAULT = 3_000_000     # リフォーム費用の既定（300万円）
BC_MARGIN_DEFAULT = 5_000_000     # 目標利幅の既定（500万円）
BC_FEE_RATE_DEFAULT = 0.07        # 諸経費率（販売価格に対して7%）
_YEN_UNIT = 10_000                # 万円単位に切り上げ


def calc_bc_price(ab_price: int, reform: int | None = None,
                  margin: int | None = None,
                  fee_rate: float | None = None) -> dict[str, Any]:
    """AB 仕入価格 → BC 販売価格を算出（内訳付き）。"""
    ab = max(int(ab_price or 0), 0)
    rf = BC_REFORM_DEFAULT if reform is None else max(int(reform), 0)
    mg = BC_MARGIN_DEFAULT if margin is None else max(int(margin), 0)
    fr = BC_FEE_RATE_DEFAULT if fee_rate is None else float(fee_rate)
    if not (0 <= fr < 1):
        fr = BC_FEE_RATE_DEFAULT
    base = ab + rf + mg
    raw = base / (1.0 - fr)
    # 万円単位に切り上げ（切り捨てると目標利幅を下回るため必ず切り上げ）
    price = int(math.ceil(raw / _YEN_UNIT) * _YEN_UNIT)
    fee = int(round(price * fr))
    import house_style
    chukai = house_style.chukai_fee(price)   # 仲介手数料（速算式・税込）
    return {
        "ab_price": ab, "reform": rf, "target_margin": mg, "fee_rate": fr,
        "fee": fee, "bc_price": price,
        # 実際の利幅（切り上げ分だけ目標をわずかに上回る）
        "actual_margin": price - ab - rf - fee,
        "chukai_fee": chukai,                # {base, tax, total, formula}
        "formula": "(仕入+リフォーム+目標利幅)/(1-諸経費率)、万円単位で切上げ",
    }


def _ab_price_of(req: GenerateReq) -> int | None:
    """AB 側の仕入価格を探す（案件マスタ > 重説 joken > 契約書 daikin）。"""
    dm = req.deal_master or {}
    for k in ("ab_baibai_daikin", "shiire_kakaku", "ab_price"):
        if dm.get(k):
            return int(dm[k])
    if isinstance(req.ab, dict):
        v = ((req.ab.get("joken") or {}).get("baibai_daikin"))
        if v:
            return int(v)
    if isinstance(req.ab_keiyaku, dict):
        v = ((req.ab_keiyaku.get("daikin") or {}).get("baibai_daikin"))
        if v:
            return int(v)
    return None






def _post_generate_check(extracted: dict, result: dict) -> list:
    """生成後の自動チェック（ミス防止）"""
    checks = []
    joken = extracted.get('joken', {})
    fud = extracted.get('fudosan', {})
    tochi = fud.get('tochi', {})
    
    # 所在地チェック
    if not tochi.get('shozai'):
        checks.append({'level': 'error', 'field': '所在地', 'message': '物件所在地が未設定。重説の「対象物件の表示」を確認してください。'})
    
    # 面積チェック
    chiseki = tochi.get('chiseki_toki') or tochi.get('chiseki_jissoku')
    if chiseki:
        try:
            area = float(str(chiseki).replace(',', ''))
            if area < 10:
                checks.append({'level': 'warning', 'field': '地積', 'message': f'地積{area}㎡が極端に小さい。単位を確認してください。'})
            elif area > 10000:
                checks.append({'level': 'warning', 'field': '地積', 'message': f'地積{area}㎡が極端に大きい。単位を確認してください。'})
        except Exception: pass
    
    # 金額の整合性チェック
    baibai = joken.get('baibai_daikin')
    tetsuke = joken.get('tetsuke')
    zankin = joken.get('zankin')
    if baibai and tetsuke and zankin:
        try:
            expected_zankin = int(baibai) - int(tetsuke)
            actual_zankin = int(zankin)
            if abs(expected_zankin - actual_zankin) > 1:
                checks.append({'level': 'warning', 'field': '残代金',
                    'message': f'残代金{actual_zankin:,}円が「売買代金{int(baibai):,}−手付{int(tetsuke):,}={expected_zankin:,}」と一致しません。内金がある場合は正常です。'})
        except Exception: pass
    
    # 違約金チェック
    iyaku = joken.get('iyakukin_wariai')
    if iyaku and int(iyaku) != 20:
        checks.append({'level': 'info', 'field': '違約金', 'message': f'違約金率が{iyaku}%です（通常は20%）。意図的な設定か確認してください。'})
    
    return checks

def _auto_bc_price(req: GenerateReq) -> dict[str, Any] | None:
    """BC 価格が未指定なら仕入価格から自動算出して deal_master に入れる。"""
    dm = req.deal_master if isinstance(req.deal_master, dict) else {}
    req.deal_master = dm
    if dm.get("bc_baibai_daikin"):
        return None                      # 明示指定が最優先
    if dm.get("bc_auto_price") is False:
        return None
    ab = _ab_price_of(req)
    if not ab:
        return None
    calc = calc_bc_price(ab, dm.get("bc_reform_cost"),
                         dm.get("bc_target_margin"), dm.get("bc_fee_rate"))
    dm["bc_baibai_daikin"] = calc["bc_price"]
    return calc


# ── 日付ユーティリティ（和暦）──────────────────────────────────
def _reiwa(d: "datetime.date") -> str:  # noqa: F821 型は下でimport
    """西暦 date → 「令和N年M月D日」。令和元年=2019。"""
    n = d.year - 2018
    era = f"令和{n}年" if n >= 1 else f"{d.year}年"
    return f"{era}{d.month}月{d.day}日"


def _month_end(d: "datetime.date") -> "datetime.date":  # noqa: F821
    """その月の月末 date を返す。"""
    import calendar
    import datetime
    last = calendar.monthrange(d.year, d.month)[1]
    return datetime.date(d.year, d.month, last)


def _add_months(d: "datetime.date", months: int) -> "datetime.date":  # noqa: F821
    """d の months ヶ月後（同日が無ければ月末に丸める）。"""
    import calendar
    import datetime
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return datetime.date(y, m, day)


def _bukken_of_req(req: GenerateReq) -> str:
    """戸建/区分 を判定（ab.bukken_type > 様式 > 既定 戸建）。"""
    if isinstance(req.ab, dict) and req.ab.get("bukken_type"):
        return "区分" if "区分" in str(req.ab["bukken_type"]) else "戸建"
    if req.template and not str(req.template).startswith("36"):
        return "区分"
    return "戸建"


# 手付金の既定（戸建=30万 / 区分=10万）。KEIYAKU_SHOSHIKI にも定義があるが独立保持。
_TETSUKE_DEFAULT = {"戸建": 300_000, "区分": 100_000}


def _apply_generate_defaults(req: GenerateReq) -> dict[str, Any]:
    """発行前に案件マスタの空欄を実務既定で埋める（Excel空欄をさらに減らす）。

    原本・入力で既に値がある項目は上書きしない（空欄のときだけ補完）。
    戻り値: 補完/計算した内容（UI 表示・warning 用）。
    """
    import datetime
    import house_style as H

    dm = req.deal_master if isinstance(req.deal_master, dict) else {}
    req.deal_master = dm
    bukken = _bukken_of_req(req)
    today = datetime.date.today()
    out: dict[str, Any] = {}

    def _fill(key: str, value: Any, label: str) -> None:
        if value in (None, "", 0):
            return
        if dm.get(key) in (None, ""):
            dm[key] = value
            out[label] = value

    # ⓪ 仲介業者プリセット展開（WebUIから "toyo" / "zeal" 等が来る）
    _PRESET_MAP = {"toyo": 0, "zeal": 1, "shibazaki": 2, "mashiko": 3}
    preset = dm.pop("bc_baikai_gyosha_preset", None)
    if preset and preset in _PRESET_MAP:
        master = H.BAIKAI_GYOSHA_MASTER[_PRESET_MAP[preset]]
        prefix = "bc_baikai_gyosha_"
        for k, v in [
            ("shomei", master.get("shomei")),
            ("menkyo_no", master.get("menkyo_no")),
            ("shozai", master.get("shozai")),
            ("daihyo", master.get("daihyo")),
            ("tel", master.get("tel")),
        ]:
            dm.setdefault(prefix + k, v)
        tp = "bc_baikai_torikiishi_"
        dm.setdefault(tp + "shimei", master.get("torikiishi_shimei"))
        dm.setdefault(tp + "toroku_no", master.get("torikiishi_toroku_no"))
        dm.setdefault(tp + "jimusho", master.get("shomei"))
        if master.get("hosho_kyokai"):
            dm.setdefault(prefix + "hosho_kyokai", master["hosho_kyokai"])
        if master.get("hosho_honbu"):
            dm.setdefault(prefix + "hosho_honbu", master["hosho_honbu"])
        out["仲介業者"] = master.get("shomei")

    # ① 表紙の日付＝今日（契約締結日が空なら今日を入れる）
    _fill("bc_keiyaku_date", _reiwa(today), "契約締結日")
    # ② 引渡・残代金日＝3ヶ月後の月末
    hikiwatashi = _reiwa(_month_end(_add_months(today, 3)))
    _fill("bc_zankin_date", hikiwatashi, "引渡・残代金日")
    _fill("bc_hikiwatashi_date", hikiwatashi, "引渡日")
    # ③ 手付金の既定（戸建30万 / 区分10万）
    _fill("bc_tetsuke", _TETSUKE_DEFAULT.get(bukken, 300_000), "手付金")
    # ④ 違約金＝20%（transform 側でも既定化されるが明示しておく）
    if dm.get("bc_iyakukin_wariai") in (None, ""):
        dm["bc_iyakukin_wariai"] = H.KEIYAKU_DEFAULTS["iyakukin_wariai"]
    out["違約金(%)"] = dm["bc_iyakukin_wariai"]

    # ⑤ 収入印紙・⑥仲介手数料は売買代金から計算（表示のみ。原本金額は変えない）
    price = dm.get("bc_baibai_daikin")
    if price:
        out["収入印紙"] = H.baibai_inshi(int(price))
        out["仲介手数料"] = H.chukai_fee(int(price))
        dm.setdefault("bc_inshi", out["収入印紙"])
        dm.setdefault("bc_chukai_fee", out["仲介手数料"]["total"])
    return out


# ── 住所 → 法令制限・ハザードの自動取得 ────────────────────────
def _addr_of(ab: dict[str, Any]) -> str:
    """重説JSONから物件所在地を取り出す。"""
    f = (ab or {}).get("fudosan") or {}
    return str(f.get("jukyo_hyoji") or f.get("ittou_shozai")
               or ((f.get("tochi") or {}).get("shozai")) or "").strip()


def _fill_missing(d: dict[str, Any], key: str, value: Any) -> bool:
    """未入力(None/空)のときだけ埋める。原本の記載は絶対に上書きしない。"""
    if value is None:
        return False
    cur = d.get(key)
    if cur is None or cur == "" or cur == []:
        d[key] = value
        return True
    return False


def _enrich_from_address(req: GenerateReq) -> dict[str, Any] | None:
    """住所から法令制限（推定）とハザード（国土地理院）を取得し空欄を補う。

    - AB 原本から読み取れた値は **上書きしない**（原本が常に優先）。
    - 自動補完した項目は必ず warnings に「要確認」として列挙する。
    """
    if not isinstance(req.ab, dict):
        return None
    dm = req.deal_master or {}
    if dm.get("auto_horei") is False and dm.get("auto_hazard") is False:
        return None
    addr = _addr_of(req.ab)
    if not addr:
        return None
    try:
        import geo_horei
        info = geo_horei.lookup(addr)
    except Exception as e:  # noqa: BLE001  外部API障害で発行を止めない
        return {"error": f"{type(e).__name__}: {e}", "filled": [], "unknown": []}

    filled: list[str] = []
    unknown: list[str] = []

    # 法令制限（推定値）
    if dm.get("auto_horei") is not False:
        h = req.ab.setdefault("horei", {}) or {}
        req.ab["horei"] = h
        est = info.get("horei") or {}
        for k, label in (("yoto", "用途地域"), ("kenpei", "建蔽率"), ("yoseki", "容積率")):
            if est.get(k) is not None and _fill_missing(h, k, est[k]):
                filled.append(f"{label}（推定: {est[k]}）")
            elif h.get(k) in (None, ""):
                unknown.append(label)

    # ハザード（True/False/None=要確認）
    if dm.get("auto_hazard") is not False:
        s = req.ab.setdefault("saigai", {}) or {}
        req.ab["saigai"] = s
        hz = info.get("hazard") or {}
        mapping = [
            ("kozui", "kozui", "水害(洪水)"),
            ("takashio", "takashio", "水害(高潮)"),
            ("naisui", "naisui", "水害(内水)"),
            ("dosha_keikai", "dosha_keikai", "土砂災害警戒区域"),
            ("dosha_tokubetsu", "dosha_tokubetsu", "土砂災害特別警戒区域"),
            ("tsunami", "tsunami_keikai", "津波災害警戒区域"),
            ("jisuberi", "jisuberi", "土砂災害警戒区域(地すべり)"),
        ]
        for src, dst, label in mapping:
            v = hz.get(src)
            if v is None:
                unknown.append(label)
            elif _fill_missing(s, dst, v):
                filled.append(f"{label}: {'該当' if v else '非該当'}")

    # 都市計画情報（防火・液状化）
    toshi = info.get("toshi_keikaku") or {}
    bouka_data = toshi.get("bouka") or {}
    if bouka_data.get("hit") and bouka_data.get("bouka"):
        h = req.ab.setdefault("horei", {}) or {}
        req.ab["horei"] = h
        if _fill_missing(h, "bouka", bouka_data["bouka"]):
            filled.append(f"防火地域: {bouka_data['bouka']}")

    eki_data = toshi.get("ekijoka") or {}
    if eki_data.get("hit"):
        s = req.ab.setdefault("saigai", {}) or {}
        req.ab["saigai"] = s
        if _fill_missing(s, "ekijoka", eki_data.get("ekijoka")):
            filled.append(f"液状化: {eki_data.get('ekijoka')}")

    # 地価公示・インフラ情報
    chika = info.get("chika") or {}

    return {
        "address": addr, "geo": info.get("geo"),
        "horei": info.get("horei"), "hazard": info.get("hazard"),
        "toshi_keikaku": toshi, "chika": chika,
        "filled": filled, "unknown": unknown,
        "error": info.get("warning") or "",
    }


def _geo_warnings(enrich: dict[str, Any] | None) -> list[dict[str, str]]:
    """自動補完の結果を「要確認」警告に変換する（無警告で通さない）。"""
    if not enrich:
        return []
    out: list[dict[str, str]] = []
    if enrich.get("error"):
        out.append({"level": "warn", "field": "自動取得",
                    "message": f"住所からの自動取得に失敗しました（{enrich['error']}）。"
                               "法令制限・ハザードは手入力で確認してください。"})
    if enrich.get("filled"):
        out.append({"level": "warn", "field": "自動補完",
                    "message": "次の項目を自動補完しました。**発行前に必ず確認**してください："
                               + " / ".join(enrich["filled"])})
    if enrich.get("unknown"):
        out.append({"level": "warn", "field": "要確認",
                    "message": "次の項目は自動判定できませんでした（要確認）："
                               + " / ".join(sorted(set(enrich["unknown"])))})
    h = enrich.get("horei") or {}
    if h.get("estimated") and any("推定" in f for f in (enrich.get("filled") or [])):
        out.append({"level": "warn", "field": "用途地域等",
                    "message": "用途地域・建蔽率・容積率は公的APIで取得できないため"
                               "都道府県既定値による**推定**です。市区町村の都市計画課で"
                               "必ず確認してください。"})
    if any("津波" in f for f in (enrich.get("filled") or [])):
        out.append({"level": "warn", "field": "津波",
                    "message": "津波は『浸水想定区域』のタイル判定です。重説の"
                               "『津波災害警戒区域』（都道府県指定）とは範囲が異なる場合があります。"})
    return out


# ── 金額あわせ・宅建士・重説チェックの警告生成 ────────────────
def _amount_warnings(req: GenerateReq) -> list[dict[str, str]]:
    """AB/BC 金額の整合性と消費税をチェックして警告を返す。"""
    out: list[dict[str, str]] = []
    ab = _ab_price_of(req)
    dm = req.deal_master or {}
    bc = dm.get("bc_baibai_daikin")
    if ab and bc:
        if int(bc) <= int(ab):
            out.append({"level": "error", "field": "売買代金",
                        "message": f"BC販売価格({int(bc):,}円)がAB仕入価格({int(ab):,}円)"
                                   "以下です。BC>ABとなるよう確認してください。"})
    # 消費税＝建物価格×10% の整合
    tate = dm.get("bc_tatemono_kakaku")
    zei = dm.get("bc_shohizei")
    if tate and zei:
        exp = int(round(int(tate) * 0.10))
        if abs(int(zei) - exp) > 1:
            out.append({"level": "warn", "field": "消費税",
                        "message": f"消費税({int(zei):,}円)が建物価格×10%({exp:,}円)"
                                   "と一致しません。建物価格・税額をご確認ください。"})
    return out


def _torikiishi_warnings() -> list[dict[str, str]]:
    """説明宅建士の在籍（退職日）をチェックする。"""
    import datetime
    import house_style as H
    out: list[dict[str, str]] = []
    today = datetime.date.today()
    for t in H.SELLER_B_TORIKIISHI:
        rd = t.get("retire_date")
        if not rd:
            continue
        try:
            d = datetime.date.fromisoformat(rd)
        except ValueError:
            continue
        if today >= d:
            out.append({"level": "warn", "field": "宅地建物取引士",
                        "message": f"{t['shimei']}（{t['toroku_no']}）は{rd}に退職済み"
                                   "の予定です。説明宅建士を後任へ変更してください。"})
        elif (d - today).days <= 30:
            out.append({"level": "info", "field": "宅地建物取引士",
                        "message": f"{t['shimei']}は{rd}に退職予定です（残り"
                                   f"{(d - today).days}日）。以降の発行は後任を選択。"})
    return out


# 重説の俯瞰チェック項目（□→■へ確定する前の確認リスト）。
JUYOJIKO_CHECKLIST: list[str] = [
    "住居表示と地番表示を混同していないか",
    "登記記録（所有権・抵当権）を空欄にしていないか",
    "都市計画法の注意書きを反映しているか",
    "建築基準法の記載で、誤った箇所を■（黒塗り）にしていないか",
    "□→■に確定する前に、俯瞰的視点で全体を再確認したか",
]


def _aux_sheet_values(template: bytes, bc: Any, deal: dict[str, Any]
                      ) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """補助シートの差込値を組む。

    - 宅建業者追記欄: 常時（会社の確定情報）。
    - 付帯設備表 / 物件状況等報告書の既定: deal で opt-in 時のみ（事実断定のため）。
    テンプレートに当該シートが無ければ空を返す（fill 側で無視される）。
    """
    import io
    import aux_sheets
    from openpyxl import load_workbook

    sv: dict[str, dict[str, Any]] = {}
    # ① 宅建業者追記欄（常時）
    sv.update(aux_sheets.tekki_values(bc))

    # ②③ 告知書・設備表の既定（既定ON。現地確認後に変更する前提で初期値を差し込む）。
    #     事実の断定を含むため、案件マスタで bc_prefill_* を明示 False にした時のみ抑止。
    #     いずれの場合も _aux_warnings が「未検証の初期値・現地確認必須」を警告する。
    need_kokuchi = deal.get("bc_prefill_kokuchi") is not False
    need_setsubi = deal.get("bc_prefill_setsubi") is not False
    if need_kokuchi or need_setsubi:
        try:
            # read_only は cell(r,c) のランダムアクセスが O(n^2) になるため通常読込。
            wb = load_workbook(io.BytesIO(template))
            for name in wb.sheetnames:
                if need_kokuchi and "物件状況等報告書" in name and "記入上" not in name:
                    d = aux_sheets.scan_kokuchi_defaults(wb[name])
                    if d:
                        sv[name] = d
                if need_setsubi and "付帯設備表" in name and "記入上" not in name:
                    d = aux_sheets.scan_setsubi_defaults(wb[name])
                    if d:
                        sv[name] = d
            wb.close()
        except Exception:  # noqa: BLE001  走査失敗でも本体生成は止めない
            pass

    sc = {name: list(vals.keys()) for name, vals in sv.items()}
    return sv, sc


def _aux_warnings(req: GenerateReq) -> list[dict[str, str]]:
    """補助シートの自動入力に関する注意（未検証の既定値）を返す。"""
    out: list[dict[str, str]] = []
    dm = req.deal_master or {}
    if dm.get("bc_prefill_kokuchi") is not False:
        out.append({"level": "warn", "field": "物件状況等報告書",
                    "message": "全項目を『売主／発見していない』の初期値で自動入力しました。"
                               "**現地確認のうえ、事実と異なる項目は必ず修正**してください"
                               "（虚偽告知は責任を負います）。"})
    if dm.get("bc_prefill_setsubi") is not False:
        out.append({"level": "warn", "field": "付帯設備表",
                    "message": "中古戸建の標準装備を『設備有無=有／故障不具合=無』の初期値で"
                               "自動入力しました。**現地確認のうえ、実際の設備・故障不具合を"
                               "必ず反映**してください。"})
    return out


def _generate_juyojiko(req: GenerateReq) -> GenerateResp:
    if req.ab is not None:
        try:
            ab = Juyojiko.model_validate(req.ab)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"ab の解析に失敗: {e}") from e
    elif req.extracted is not None:
        if not req.bukken:
            raise HTTPException(status_code=400, detail="bukken が必要です。")
        try:
            ab = _legacy_to_juyojiko(req.bukken, req.extracted)
        except KeyError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    else:
        raise HTTPException(status_code=400, detail="ab または extracted が必要です。")

    bc = transform_ab_to_bc(ab, req.deal_master)
    bukken = bc.bukken_type or (bc.fudosan.bukken_type if bc.fudosan else None) or "区分"

    # 本番ワークブックがあれば差込（最も忠実）。無ければ自作 Excel にフォールバック。
    template = _try_template_bytes(req)
    variant = _resolve_variant(req, template)  # 様式は明示指定 or A1から自動判定
    edition = _kubun_edition(template, variant)  # 区分B版テンプレはB版座標へ写像
    if template is not None and variant in cellmaps.JUYOJIKO_BUILDERS:
        try:
            sv, sc = cellmaps.build_juyojiko(variant, bc, edition=edition)
            av, ac = cellmaps.build_aux(bc)
            xv, xc = _aux_sheet_values(template, bc, req.deal_master or {})
            allv = {**sv, **av, **xv}
            allc = {**sc, **ac, **xc}
            if variant in cellmaps.KEIYAKU_BUILDERS:
                bc_k = juyojiko_to_keiyakusho(bc, req.deal_master)
                kv, kc = cellmaps.build_keiyaku(variant, bc_k)
                allv = {**allv, **kv}
                allc = {**allc, **kc}
            xlsx, _ = wb_fill.fill_workbook(template, allv, allc)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"ワークブック差込に失敗: {e}") from e
        prefix = f"BC重説_{variant}"
    else:
        try:
            xlsx = juyojiko_excel.render(bc)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"重説生成に失敗: {e}") from e
        prefix = "BC重説"

    return GenerateResp(
        filename=_filename(prefix, bukken, bc.fudosan, req.filename),
        bukken=bukken,
        xlsx_base64=base64.b64encode(xlsx).decode("ascii"),
        warnings=validate.validate_juyojiko(bc),
    )


def _kubun_edition(template: bytes | None, variant: str | None) -> str:
    """区分テンプレの様式版 'A'/'B' を判定する。判定不能・非対応は 'A'。

    B版の座標オーバーライドは **37-1 でのみ検証済み**。38-1 のB版マップは未確認の
    ため、38-1 は常に 'A' 扱いにする（B版38-1をB判定してA座標で埋める誤差込を防ぐ。
    実在サンプルの38-1はA版で、これで正しく差し込める。真の38-1 B版は要サンプル）。
    """
    if template is None or variant != "37-1":
        return "A"
    try:
        wb = load_workbook(io.BytesIO(template), data_only=True)
        ws = wb[cellmaps.JUYOJIKO_SHEET] if cellmaps.JUYOJIKO_SHEET in wb.sheetnames else None
    except Exception:  # noqa: BLE001
        return "A"
    ed = cellmaps.detect_kubun_edition(ws) if ws is not None else "A"
    return "B" if ed == "B" else "A"


def _resolve_variant(req: GenerateReq, template: bytes | None) -> str | None:
    """様式を決める: 明示指定（req.template）を優先、無ければWBのA1から自動判定。"""
    if req.template:
        return req.template
    if template is not None:
        try:
            return wb_fill.detect_variant(template)
        except Exception as e:  # noqa: BLE001 壊れた/xlsxでないテンプレは400で返す（500にしない）
            raise HTTPException(
                status_code=400,
                detail="テンプレート(template_base64)が有効なxlsxではありません。") from e
    return None


def _try_template_bytes(req: GenerateReq) -> bytes | None:
    """本番ワークブックのテンプレ実体を返す（無ければ None）。"""
    if req.template_base64:
        try:
            return base64.b64decode(req.template_base64, validate=True)
        except Exception as e:  # noqa: BLE001 不正base64は400で返す（500にしない）
            raise HTTPException(
                status_code=400, detail="template_base64 が不正な base64 です。") from e
    if req.template:
        tdir = os.environ.get("BC_TEMPLATE_DIR", "templates")
        return wb_fill.load_template(tdir, req.template)
    return None


def _generate_keiyaku(req: GenerateReq) -> GenerateResp:
    if req.ab is None:
        raise HTTPException(status_code=400, detail="契約書には ab（契約書JSON）が必要です。")
    try:
        ab = Keiyakusho.model_validate(req.ab)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"ab の解析に失敗: {e}") from e
    bc = transform_keiyaku_ab_to_bc(ab, req.deal_master)
    bukken = bc.bukken_type or (bc.fudosan.bukken_type if bc.fudosan else None) or "戸建"

    # 本番ワークブックがあれば差込（最も忠実）。無ければ自作 Excel にフォールバック。
    template = _try_template_bytes(req)
    variant = _resolve_variant(req, template)  # 様式は明示指定 or A1から自動判定
    if template is not None and variant in cellmaps.KEIYAKU_BUILDERS:
        try:
            sv, sc = cellmaps.build_keiyaku(variant, bc)
            av, ac = cellmaps.build_aux(bc)
            xlsx, _ = wb_fill.fill_workbook(template, {**sv, **av}, {**sc, **ac})
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"ワークブック差込に失敗: {e}") from e
        prefix = f"BC契約書_{variant}"
    else:
        try:
            xlsx = keiyaku_excel.render(bc)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"契約書生成に失敗: {e}") from e
        prefix = "BC契約書"

    return GenerateResp(
        filename=_filename(prefix, bukken, bc.fudosan, req.filename),
        bukken=bukken,
        xlsx_base64=base64.b64encode(xlsx).decode("ascii"),
        warnings=validate.validate_keiyaku(bc),
    )


def _generate_reform(req: GenerateReq) -> GenerateResp:
    """リフォーム工事請負契約書をExcelで生成する。"""
    import house_style as H
    dm = req.deal_master or {}
    chumonsha = dm.get("reform_chumonsha", "")
    if not chumonsha or not str(chumonsha).strip():
        raise HTTPException(status_code=400, detail="注文者氏名が未入力です。")
    total = dm.get("reform_total", 0)
    try:
        total = int(total)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="請負代金が不正な値です。")
    if total <= 0:
        raise HTTPException(status_code=400, detail="請負代金を入力してください（0以下は不可）。")
    koji_name = dm.get("reform_koji_name", "リフォーム工事")
    koji_basho = dm.get("reform_koji_basho", "")
    chumonsha_addr = dm.get("reform_chumonsha_addr", "")
    chumonsha_tel = dm.get("reform_chumonsha_tel", "")
    keiyaku_date = dm.get("reform_keiyaku_date", "")
    koji_start = dm.get("reform_koji_start", "")
    koji_end = dm.get("reform_koji_end", "")

    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side
    wb = Workbook()
    ws = wb.active
    ws.title = "リフォーム工事請負契約書"
    ws.sheet_properties.pageSetUpPr = None

    thin = Side(style="thin")
    bd = Border(top=thin, left=thin, right=thin, bottom=thin)
    title_font = Font(size=16, bold=True)
    header_font = Font(size=11, bold=True)
    normal_font = Font(size=11)

    row = 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    ws.cell(row=row, column=1, value="リフォーム工事請負契約書").font = title_font
    ws.cell(row=row, column=1).alignment = Alignment(horizontal="center")
    row += 2

    items = [
        ("工事名称", koji_name),
        ("工事場所", koji_basho),
        ("工事予定", f"{koji_start} ～ {koji_end}"),
        ("工事内容", dm.get("reform_koji_naiyo", "")),
    ]
    for label, val in items:
        ws.cell(row=row, column=1, value=label).font = header_font
        ws.cell(row=row, column=1).border = bd
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=6)
        ws.cell(row=row, column=2, value=val).font = normal_font
        ws.cell(row=row, column=2).border = bd
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="注文者").font = header_font
    row += 1
    for label, val in [("氏名", chumonsha), ("住所", chumonsha_addr), ("TEL/FAX", chumonsha_tel)]:
        ws.cell(row=row, column=1, value=label).font = normal_font
        ws.cell(row=row, column=1).border = bd
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=6)
        ws.cell(row=row, column=2, value=val).font = normal_font
        ws.cell(row=row, column=2).border = bd
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="請負者").font = header_font
    row += 1
    for label, val in [
        ("氏名", "株式会社 Martial Arts"),
        ("住所", "東京都中央区日本橋人形町1-5-8 アトリウム日本橋人形町4階"),
        ("TEL/FAX", "03-6231-1113  03-6231-1114"),
    ]:
        ws.cell(row=row, column=1, value=label).font = normal_font
        ws.cell(row=row, column=1).border = bd
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=6)
        ws.cell(row=row, column=2, value=val).font = normal_font
        ws.cell(row=row, column=2).border = bd
        row += 1

    row += 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
    ws.cell(row=row, column=1, value="請負代金").font = Font(size=14, bold=True)
    ws.cell(row=row, column=1).border = bd
    ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=6)
    ws.cell(row=row, column=4, value=f"¥{total:,}（税込）" if total else "").font = Font(size=14, bold=True)
    ws.cell(row=row, column=4).border = bd
    row += 2

    inshi = H.ukeoi_inshi(total, H.UKEOI_REFORM)
    ws.cell(row=row, column=1, value=f"契約日: {keiyaku_date}").font = normal_font
    row += 1
    ws.cell(row=row, column=1, value=f"収入印紙: ¥{inshi:,}").font = normal_font
    row += 2

    ws.cell(row=row, column=1, value="◇請負条件◇").font = header_font
    row += 1
    for b in H.UKEOI_REFORM["biko"]:
        ws.cell(row=row, column=1, value=b).font = normal_font
        row += 1

    import io
    buf = io.BytesIO()
    wb.save(buf)
    xlsx = buf.getvalue()

    fname = f"リフォーム契約書_{chumonsha or '未定'}.xlsx"
    return GenerateResp(
        filename=fname,
        bukken="リフォーム",
        xlsx_base64=base64.b64encode(xlsx).decode("ascii"),
        warnings=[],
    )


def _generate_package(req: GenerateReq) -> GenerateResp:
    """重説シートと契約書シートを1つの本番ワークブックへ同時差込する。

    req.ab=重説JSON、req.ab_keiyaku=契約書JSON、req.template=変種、案件マスタを共用。
    本番ワークブック（両シートを含む）が必須。
    """
    template = _try_template_bytes(req)
    variant = _resolve_variant(req, template)  # 様式は明示指定 or A1から自動判定
    if template is None or variant not in cellmaps.JUYOJIKO_BUILDERS:
        raise HTTPException(
            status_code=400,
            detail="package には本番ワークブック（A1様式が36-1/37-1/38-1）が必要です。")
    # 契約書シートはA/B版で同一レイアウト（実WBで確認）。重説のみB版座標へ写像する。
    edition = _kubun_edition(template, variant)
    if req.ab is None:
        raise HTTPException(
            status_code=400, detail="package には ab（重説）が必要です。")
    try:
        bc_j = transform_ab_to_bc(Juyojiko.model_validate(req.ab), req.deal_master)
        if req.ab_keiyaku is not None:
            bc_k = transform_keiyaku_ab_to_bc(
                Keiyakusho.model_validate(req.ab_keiyaku), req.deal_master)
        else:
            bc_k = juyojiko_to_keiyakusho(bc_j, req.deal_master)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"ab の解析に失敗: {e}") from e

    sv_j, sc_j = cellmaps.build_juyojiko(variant, bc_j, edition=edition)
    sv_k, sc_k = cellmaps.build_keiyaku(variant, bc_k)
    av, ac = cellmaps.build_aux(bc_j)
    xv, xc = _aux_sheet_values(template, bc_j, req.deal_master or {})
    sheet_values = {**sv_j, **sv_k, **av, **xv}
    sheet_clear = {**sc_j, **sc_k, **ac, **xc}
    try:
        xlsx, _ = wb_fill.fill_workbook(template, sheet_values, sheet_clear)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"ワークブック差込に失敗: {e}") from e

    bukken = bc_j.bukken_type or (bc_j.fudosan.bukken_type if bc_j.fudosan else None) or "区分"
    return GenerateResp(
        filename=_filename(f"BC一式_{variant}", bukken, bc_j.fudosan, req.filename),
        bukken=bukken,
        xlsx_base64=base64.b64encode(xlsx).decode("ascii"),
        warnings=validate.validate_bc(juyojiko=bc_j, keiyaku=bc_k),
    )


@app.post("/generate", response_model=GenerateResp)
def generate(req: GenerateReq) -> GenerateResp:
    # ⓪ CRM連携: お客様名からdeal_masterを自動補完
    _crm_name = (req.deal_master or {}).get("bc_kainushi") or ""
    _bukken_hint = ""
    if req.ab and isinstance(req.ab, dict):
        fud = req.ab.get("fudosan") or {}
        _bukken_hint = fud.get("jukyo_hyoji") or ""
        if not _crm_name:
            _crm_name = (req.ab.get("kainushi") or {}).get("name", "")
    if _crm_name:
        req.deal_master = crm_lookup.enrich_deal_master(
            dict(req.deal_master or {}), _crm_name, _bukken_hint)
    # ① BC 販売価格の自動計算（明示指定があればそちら優先）
    price_calc = _auto_bc_price(req)
    # ①' 空欄の実務既定を補完（日付=今日/3ヶ月後月末・手付・違約金・印紙・仲介手数料）
    auto_defaults = _apply_generate_defaults(req)
    # ②③ 住所 → 法令制限（推定）／ハザード（国土地理院）で空欄を補完
    enrich = _enrich_from_address(req)

    if req.doc_type == "package":
        resp = _generate_package(req)
    elif req.doc_type == "keiyaku":
        resp = _generate_keiyaku(req)
    elif req.doc_type == "juyojiko":
        resp = _generate_juyojiko(req)
    elif req.doc_type == "reform":
        resp = _generate_reform(req)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"未知の doc_type: {req.doc_type}（juyojiko / keiyaku / package / reform）")

    resp.price_calc = price_calc
    resp.auto_defaults = auto_defaults or None
    if enrich:
        resp.geo_info = {k: enrich.get(k) for k in ("address", "geo", "horei", "hazard", "toshi_keikaku", "chika")}
    resp.warnings = (list(resp.warnings) + _geo_warnings(enrich)
                     + _amount_warnings(req) + _torikiishi_warnings()
                     + _aux_warnings(req))
    if auto_defaults:
        chukai = auto_defaults.get("仲介手数料") or {}
        parts = []
        if auto_defaults.get("契約締結日"):
            parts.append(f"契約日={auto_defaults['契約締結日']}")
        if auto_defaults.get("引渡・残代金日"):
            parts.append(f"引渡日={auto_defaults['引渡・残代金日']}(3ヶ月後月末)")
        if auto_defaults.get("手付金"):
            parts.append(f"手付金={int(auto_defaults['手付金']):,}円")
        if auto_defaults.get("違約金(%)") is not None:
            parts.append(f"違約金={auto_defaults['違約金(%)']}%")
        if auto_defaults.get("収入印紙"):
            parts.append(f"収入印紙={int(auto_defaults['収入印紙']):,}円")
        if chukai.get("total"):
            parts.append(f"仲介手数料={int(chukai['total']):,}円(税込)")
        if parts:
            resp.warnings.append({
                "level": "info", "field": "自動補完",
                "message": "空欄を既定値で補完しました（要確認）： " + " / ".join(parts),
            })
    if price_calc:
        resp.warnings.append({
            "level": "info", "field": "売買代金",
            "message": (f"BC売買代金を自動計算しました: {price_calc['bc_price']:,}円"
                        f"（仕入{price_calc['ab_price']:,} + リフォーム{price_calc['reform']:,}"
                        f" + 利幅{price_calc['target_margin']:,}"
                        f" + 諸経費{price_calc['fee']:,}）。金額は必ず確認してください。"),
        })
    _crm_info = (req.deal_master or {}).get("_crm_source")
    if _crm_info and _crm_info.get("matched"):
        resp.warnings.append({
            "level": "info", "field": "CRM連携",
            "message": f"CRMから顧客データを自動補完しました: {_crm_info.get('customer')} / {_crm_info.get('property', '')}",
        })
    photo_biko = (req.deal_master or {}).get("bc_photo_biko")
    if photo_biko:
        resp.warnings.append({
            "level": "warn", "field": "写真判定",
            "message": ("物件写真からのAI所見: " + str(photo_biko)
                        + " ／ 物件状況等報告書・付帯設備表は現地確認のうえ確定してください。"),
        })
    return resp


# ── /crm（CRM顧客検索・重複チェック）───────────────────
@app.get("/crm/search")
def crm_search(name: str = "", bukken: str = "") -> dict[str, Any]:
    """CRMからお客様名で検索。"""
    if not name:
        raise HTTPException(status_code=400, detail="name パラメータが必要です。")
    customer = crm_lookup.search_customer(name)
    if not customer:
        return {"found": False, "customer": None}
    prop = crm_lookup.find_property(customer, bukken)
    return {"found": True, "customer": customer.get("name"), "property": prop, "all_properties": customer.get("properties", [])}


@app.get("/crm/generated")
def crm_generated(name: str = "") -> dict[str, Any]:
    """生成済み書類の一覧。"""
    docs = crm_lookup.list_generated(name)
    return {"count": len(docs), "documents": docs}


@app.post("/crm/reload")
def crm_reload() -> dict[str, Any]:
    """CRMデータを再読込。"""
    count = crm_lookup.reload_crm()
    return {"reloaded": True, "customer_count": count}


# ── /geo（住所→法令制限・ハザードの単体取得。UI/確認用）─────────
class GeoReq(BaseModel):
    address: str


@app.post("/geo")
def geo(req: GeoReq) -> dict[str, Any]:
    if not (req.address or "").strip():
        raise HTTPException(status_code=400, detail="address が必要です。")
    try:
        import geo_horei
        return geo_horei.lookup(req.address)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"取得に失敗: {e}") from e


# ── /price（販売価格の試算のみ。UI のプレビュー用）───────────────
class PriceReq(BaseModel):
    ab_price: int
    reform: int | None = None
    margin: int | None = None
    fee_rate: float | None = None


@app.post("/price")
def price(req: PriceReq) -> dict[str, Any]:
    if req.ab_price is None or req.ab_price < 0:
        raise HTTPException(status_code=400, detail="ab_price が不正です。")
    return calc_bc_price(req.ab_price, req.reform, req.margin, req.fee_rate)


# ── /photos（物件写真をAIで判定：外観/内装の状態・付帯設備の有無）──────
_PHOTO_SYS = (
    "あなたは日本の中古住宅を写真から評価する不動産の専門家です。"
    "与えられた物件写真（外観・内装・設備）を見て、次のJSON構造で客観的に判定してください。"
    "写真から判断できない項目はnull、確信が持てない設備はpresence='不明'にすること。"
    "推測で断定しないこと。前置き不要、JSONのみ。\n\n"
    "{\n"
    '  "gaikan": "外観の状態の所見（例: 外壁に経年の汚れ、屋根は良好 等）",\n'
    '  "naiso": "内装の状態の所見（例: クロス日焼け、床に目立つ傷なし 等）",\n'
    '  "overall_condition": "良好|概ね良好|要補修|不明",\n'
    '  "equipment": [\n'
    '    {"name": "エアコン|照明器具|カーテン・レール|給湯器|キッチン|浴室|洗面台|'
    'トイレ|インターホン|物置|カーポート|その他", "presence": "有|無|不明",\n'
    '     "note": "設置箇所・状態などの補足"}\n'
    "  ],\n"
    '  "biko": "写真全体から特記すべき事項（要修繕箇所・残置物など）"\n'
    "}\n"
    "equipment は写真で確認できた設備を列挙。エアコン・照明・カーテンは特に注意して判定。"
)


class PhotoReq(BaseModel):
    images: list[str]                     # base64 画像（複数可）
    mimes: list[str] | None = None        # 各画像の MIME（省略時 image/jpeg）


class PhotoResp(BaseModel):
    analysis: dict[str, Any]
    warning: str = ""


def _analyze_photos(images: list[str], mimes: list[str] | None) -> tuple[dict[str, Any], str]:
    """物件写真をClaudeに見せて外観/内装/付帯設備を判定する。例外は投げない。"""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return {}, "写真解析ライブラリ(anthropic)が未導入です。"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}, "写真解析(APIキー)が未設定です。"
    if not images:
        return {}, "写真が指定されていません。"
    mimes = mimes or []
    pieces: list[dict[str, Any]] = [
        {"type": "text", "text": "次の物件写真から状態・付帯設備を判定してください。"}]
    for i, img in enumerate(images[:20]):   # 一度に送る枚数の上限（安全側）
        mt = mimes[i] if i < len(mimes) and mimes[i] else "image/jpeg"
        pieces.append({"type": "image", "source": {
            "type": "base64", "media_type": mt, "data": img}})
    try:
        from anthropic import Anthropic
        client = Anthropic(max_retries=3, timeout=180.0)
        msg = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, system=_PHOTO_SYS,
            messages=[{"role": "user", "content": pieces}],
        )
        raw = "".join(b.text for b in msg.content
                      if getattr(b, "type", None) == "text").strip()
        data = _parse_json_loose(raw)
    except Exception as e:  # noqa: BLE001
        return {}, f"写真解析に失敗しました（{_err_detail(e)}）。手入力で続行できます。"
    if not data:
        return {}, "写真から判定できませんでした。手入力で続行できます。"
    return data, ""


@app.post("/photos", response_model=PhotoResp)
def photos(req: PhotoReq) -> PhotoResp:
    """物件写真をAIで判定（外観/内装の状態・付帯設備の有無）。失敗しても200で返す。"""
    try:
        data, warning = _analyze_photos(req.images, req.mimes)
    except BaseException as e:  # noqa: BLE001 想定外でも落とさない
        data, warning = {}, f"写真解析で予期しない問題（{type(e).__name__}）。"
    return PhotoResp(analysis=data, warning=warning)


# ── /buai（歩合計算 v5.7.7）─────────────────────────────────────
class BuaiReq(BaseModel):
    gross_profit: int
    product: str = "不動産"                 # 不動産 / FGH / リフォーム / 私募債
    members: dict[str, str] | None = None    # {役割: 担当者名}


@app.post("/buai")
def buai_calc(req: BuaiReq) -> dict[str, Any]:
    if req.gross_profit is None or req.gross_profit < 0:
        raise HTTPException(status_code=400, detail="gross_profit が不正です。")
    import buai
    return buai.calc_buai(req.gross_profit, req.product, req.members)


# ── /ukeoi（工事請負契約の支払スケジュール・印紙）───────────────
class UkeoiReq(BaseModel):
    total: int
    kind: str = "新築"                       # 新築 / リフォーム


@app.post("/ukeoi")
def ukeoi(req: UkeoiReq) -> dict[str, Any]:
    import house_style as H
    doc = H.UKEOI_REFORM if req.kind == "リフォーム" else H.UKEOI_SHINCHIKU
    total = int(req.total or 0)
    return {
        "kind": req.kind,
        "doc_name": doc["doc_name"],
        "total": total,
        "inshi": H.ukeoi_inshi(total, doc),
        "pages": doc.get("pages"),
        "kouki": (f"{doc['kouki_months'][0]}〜{doc['kouki_months'][1]}ヶ月"
                  if "kouki_months" in doc else f"{doc.get('kouki_days')}日間"),
        "payments": H.ukeoi_payments(total, doc) if doc.get("payments") else [],
        "biko": doc["biko"],
    }


# ── /playbook（案件判定フロー・必須書類・書式・協会情報）─────────
@app.get("/playbook")
def playbook() -> dict[str, Any]:
    import house_style as H
    return {
        "deal_flow": H.DEAL_FLOW,
        "bc_required_docs": H.BC_REQUIRED_DOCS,
        "keiyaku_shoshiki": H.KEIYAKU_SHOSHIKI,
        "buai_rate": __import__("buai").BUAI_RATE,
        "chukai_fee_formula": "売買代金×3%+6万円+消費税",
        "juyojiko_checklist": JUYOJIKO_CHECKLIST,
        "setsumei_torikiishi": H.SELLER_B_TORIKIISHI,
        "zennichi": H.ZENNICHI,           # 認証情報は含まない
    }


# ── /judge_deal（案件1件の判定）─────────────────────────────────
class JudgeReq(BaseModel):
    pass_own_bank: bool | None = None
    pass_partner_bank: bool | None = None
    has_will: bool | None = True


@app.post("/judge_deal")
def judge_deal_ep(req: JudgeReq) -> dict[str, Any]:
    import house_style as H
    return H.judge_deal(req.pass_own_bank, req.pass_partner_bank, req.has_will)


# ── /extract ──────────────────────────────────────────────────
_EXTRACT_SYS = (
    "あなたは日本の不動産の重要事項説明書（35条書面）を構造化する最高精度の抽出エンジンです。"
    "【最重要ルール】\n""1. 画像の隅々まで1文字ずつ読め。手書き文字も読め。印鑑の下も読め。\n""2. 表の中のチェックボックス（■□）を見逃すな。■がチェック済み。\n""3. 金額は万円ではなく円で返せ。「1,990万円」→19900000。カンマは除去。\n""4. 住所は漢字・番地・号まで全て読め。「五丁目947番地194」のように。\n""5. 面積は小数点以下まで正確に。「231.00」なら231.00。\n""6. 売主・買主の氏名は姓と名の間にスペースを入れる。\n""7. 印鑑が押されていても文字を読め。印鑑の下の文字も読め。\n""8. 複数行にまたがるテキストは全て結合して読め。\n""9. 読み取れない項目はnullにしろ。推測するな。\n""10. 特約条項は1文字も省略するな。全文を返せ。"
    "数字（面積・金額・比率）は原文と1文字でも違ったら失格。必ず原文を見直してから出力しろ。"
    "住所の漢字・番地も原文通り。「丁目」を「-」に変えるな。原文そのまま。"
    "与えられた重説（PDF/画像/テキスト）から、次の JSON 構造で読み取れる項目を返してください。"
    "読み取れない項目は null、配列は空配列に。推測で埋めないこと。前置き不要、JSON のみ。\n\n"
    "{\n"
    '  "bukken_type": "戸建|区分",\n'
    '  "torihiki_taiyo": "取引態様（例: 売買・媒介）",\n'
    '  "gyosha": {"menkyo_no":..,"menkyo_date":..,"shozai":..,"tel":..,"shomei":..,"daihyo":..},\n'
    '  "torikiishi": {"toroku_no":..,"shimei":..,"jimusho":..,"jimusho_shozai":..,"tel":..},\n'
    '  "kainushi": {"address":"買主(説明を受けた人)の住所","name":"買主(説明を受けた人)の氏名"},\n'
    '  "urinushi": {"address":..,"name":..,"biko":..},\n'
    '  "baikai_gyosha": {"shomei":"媒介業者名(買主側)","menkyo_no":..,"shozai":..,"daihyo":..,"tel":..},\n'
    '  "baikai_torikiishi": {"shimei":"媒介業者側取引士","toroku_no":..},\n'
    '  "fudosan": {"bukken_type":..,"jukyo_hyoji":..,"fuzoku_tatemono":"附属建物の有無(有/無)",'
    '"fuzoku_tatemono_detail":"附属建物の詳細",'
    '"tochi":{"shozai":..,"chiban":"地番(例:12番5)","chimoku":..,"chiseki_toki":..,"chiseki_jissoku":..,"mochibun":"持分(例:全部,1/2)"},'
    '"tatemono":{"shozai":"建物所在(例:水戸市藤が原三丁目12番地5)","kaoku_bango":..,"shurui":..,"kozo":..,"yukamenseki":..,"chikujiki":..},'
    '"ittou_shozai":..,"ittou_kozo":..,"ittou_enshoumenseki":..,'
    '"senyuu":{"kaoku_bango":..,"meisho":..,"shurui":..,"kozo":..,"yukamenseki":..,"chikujiki":..},'
    '"shikichiken":[{"shozai":..,"chiban":..,"chiseki":..,"shikichiken_shurui":..,"wariai":..}]},\n'
    '  "touki_meigi": "登記名義人（所有者）",\n'
    '  "senyuusha_uchi": "第三者占有(賃借人)の有無・概要",\n'
    '  "horei": {"toshikeikaku_kuiki":"都市計画区域内|外",'
    '"kuiki_kubun":"市街化区域|市街化調整区域|区域区分のされていない区域",'
    f'"yoto":"用途地域(次のいずれか: {", ".join(YOTO_OPTIONS)})",'
    '"nijuni_jo":true/false,"boka":..,"kodo_chiku":..,"chiiki_chiku":[..],'
    '"kenpei":整数,"kenpei_kanwa":..,"yoseki":整数,"yoseki_zenmen_doro":..,'
    '"nisshido":..,"doro":"接面道路の概要(備考)",'
    '"doro_hoko":"接面道路の方向(南/南西等)","doro_haba":"幅員","doro_setsudo":"接道の長さ",'
    '"shikichi_saitei":"敷地面積の最低限度","suigai_shozai":"水害ハザード所在地の説明",'
    '"other_horei":[..]},\n'
    '  "setsubi": "飲用水・電気・ガス・排水の概要",\n'
    '  "setsubi_detail": {"suidou":"公営水道|私営水道|井戸",'
    '"gas":"都市ガス|個別プロパン|集中プロパン",'
    '"osui":"公共下水|個別浄化槽|集中浄化槽|汲取式",'
    '"zassui":"公共下水|個別浄化槽|集中浄化槽|側溝等|浸透式",'
    '"denryoku":"電力会社名","biko":"設備の備考"},\n'
    '  "saigai": {"zosei_bosai":"造成宅地防災区域 有=true/false",'
    '"dosha_keikai":"土砂災害警戒区域 有=true/false（イエローゾーン。チェック■や丸印や手書き○があればtrue）",'
    '"dosha_tokubetsu":"土砂災害特別警戒区域 有=true/false（レッドゾーン）",'
    '"tsunami_keikai":"津波災害警戒区域 有=true/false","tsunami_tokubetsu":true/false,'
    '"taishin_shindan":"耐震診断 有=true/false","sekimen_kiroku":"石綿調査記録 有=true/false",'
    '"kozui":"水害洪水ハザード 有=true/false（ハザードマップ浸水想定あり=true）",'
    '"naisui":"内水ハザード 有=true/false","takashio":"高潮 有=true/false"},\n'
    '  "kakunin": {"kenchiku_bango":"建築確認番号","kenchiku_date":"建築確認交付年月日",'
    '"kensa_bango":"検査済証番号","kensa_date":"検査済証交付年月日"},\n'
    '  "touki": {"tochi_shoyusha_jusho":"土地所有者住所","tochi_shoyusha_shimei":"土地所有者氏名",'
    '"tochi_otsuku":"土地乙区","tatemono_shoyusha_jusho":"建物所有者住所",'
    '"tatemono_shoyusha_shimei":"建物所有者氏名","tatemono_otsuku":"建物乙区"},\n'
    '  "kanri": {"kanrihi_getsugaku":整数,"shuzen_getsugaku":整数,"shuzen_tsumitate":整数,'
    '"kanrihi_taino":整数,"shuzen_taino":整数,"kanri_kumiai":..,"kanri_keitai":..,'
    '"kanri_itakusaki":..,"yoto_seigen":..,"pet_seigen":..},\n'
    '  "shakuchi": {"shakuchiken_shurui":"普通/一般定期/事業用定期/建物譲渡特約付",'
    '"toki_umu":"有/無","sonzoku_kikan":..,"keiyaku_shiki":..,"keiyaku_manryo":..,'
    '"jidai_kingaku":整数,"jidai_tani":"月額/年額","jidai_shiharai":..,"jidai_kaitei":..,'
    '"koshin_ryo":..,"joto_shodaku":..,"kenchiku_seigen":..,'
    '"teichi_shoyusha_jusho":..,"teichi_shoyusha_shimei":..,"biko":..},'
    "  # ↑借地権付き建物のときのみ。所有権物件では省略可\n"
    '  "joken": {"baibai_daikin":整数,"tochi_kakaku":"うち土地価格(整数)",'
    '"tatemono_kakaku":"うち建物価格(整数)","shohizei":整数,"tetsuke":整数,'
    '"seisan_kisanbi":..,"iyakukin_wariai":整数,"tanpo_sekinin":..,'
    '"zankin":"残代金(整数。売買代金-手付金-内金)","zankin_date":"残代金支払期日(例:令和8年1月30日)",'
    '"hikiwatashi_date":"引渡し日(例:令和8年1月30日)",'
    '"loan_tokuyaku":true/false,"loan_bank":"融資先金融機関名",'
    '"loan_kingaku":"融資金額(整数)","loan_kinri":"融資金利(数値)",'
    '"loan_nensu":"融資期間(年数)","loan_shonin_date":"融資承認期日",'
    '"loan_kaijo_date":"融資特約に基づく契約解除期日"},\n'
    '  "seisan_biko": "公租公課の清算に関する備考",\n'
    '  "yonin_jiko": ["容認事項..."],\n'
    '  "tokuyaku": ["特約事項..."]\n'
    "}\n"
    "金額は円の整数（カンマ無し）。建蔽率・容積率は%の整数。"
    "\n\n重要な注意事項（精度向上）:\n"
    "1. 地番と住居表示を混同しないこと。登記簿は地番、重説は住居表示の場合がある\n"
    "2. 面積は㎡単位。坪数が書いてあっても㎡に変換しない（原文のまま抽出）\n"
    "3. 抵当権・根抵当権がある場合、touki.tochi_otsukuとtatemono_otsukuに記載内容を全て抽出\n"
    "4. 用途地域は正式名称で（例:第一種低層住居専用地域）。略称を使わない\n"
    "5. 建蔽率・容積率の緩和がある場合、kanwaフィールドに理由を記載\n"
    "6. 接面道路は幅員(m)・方向・種類(公道/私道/位置指定)を正確に\n"
    "7. 設備は必ずsetsubi_detailに分類して入れる（水道/ガス/汚水/雑排水/電力）\n"
    "8. 災害情報は全項目チェック。ハザードマップの浸水想定区域・土砂警戒区域を見逃すな\n"
    "9. 特約条項・容認事項は省略せず全文を抽出\n"
    "10. 売買代金・手付金・違約金の金額は1円単位で正確に\n"
    "11. 複数ページにまたがる情報は全ページを通して読み取ること\n"
    "12. 手書き部分がある場合も可能な限り読み取る\n"
    "13. AB間の重説の場合、売主=現所有者、MAは中間取得者。この関係を理解した上で抽出する\n"
    "14. 建物所在(tatemono.shozai)と家屋番号(tatemono.kaoku_bango)は必ず読め。重説の「不動産の表示」欄にある\n"
    "15. 築年月(chikujiki)は「新築/増築年月」「建築時期」等の欄から読め。「平成30年11月新築」のように\n"
    "16. 手付金(tetsuke)・残代金(zankin)・土地価格(tochi_kakaku)・建物価格(tatemono_kakaku)は売買代金の内訳表にある。重説の最後のページか別添の売買契約書にある場合が多い。見つけたら必ず抽出しろ\n"
    "17. 買主名(kainushi.name)は「説明を受けた人」「買主」欄から読め。手書きでも読み取れ\n"
    "18. 融資情報(loan_bank/loan_kingaku)は「融資利用の特約」欄から読め\n"
    "19. 契約日は署名欄の日付から読め\n"
    "20. 買主住所(kainushi.address)は最重要項目。「説明を受けた人」「買主(譲受人)」欄の住所を必ず読め。手書きでも1文字ずつ読め。マンション名・部屋番号まで全て含めろ\n"
    "21. 土地が複数筆ある場合、fudosan.tochiは1筆目、追加の筆はfudosan.tochi_2, tochi_3...として追加\n"
    "22. 床面積(yukamenseki)は「1階 69.50㎡・2階 56.00㎡ 計125.50㎡」のように原文のまま。detail形式も可\n"
    "23. 用途地域の建蔽率(kenpei)と容積率(yoseki)は%の整数。「80%」→80。「300%」→300\n"
    "24. 法22条区域(hou22jou)のチェックを見逃すな。チェック■があればtrue\n"
    "25. 接面道路の幅員・方向・接道長さは全て読め。「南側 約15.00m 約16m」のように"
)


_EXTRACT_SYS_KEIYAKU = (
    "あなたは日本の不動産売買契約書（FRK標準書式）を構造化する最高精度の抽出エンジンです。"
    "【最重要ルール】\n""1. 画像の隅々まで1文字ずつ読め。手書き文字も読め。印鑑の下も読め。\n""2. 表の中のチェックボックス（■□）を見逃すな。■がチェック済み。\n""3. 金額は万円ではなく円で返せ。「1,990万円」→19900000。カンマは除去。\n""4. 住所は漢字・番地・号まで全て読め。「五丁目947番地194」のように。\n""5. 面積は小数点以下まで正確に。「231.00」なら231.00。\n""6. 売主・買主の氏名は姓と名の間にスペースを入れる。\n""7. 印鑑が押されていても文字を読め。印鑑の下の文字も読め。\n""8. 複数行にまたがるテキストは全て結合して読め。\n""9. 読み取れない項目はnullにしろ。推測するな。\n""10. 特約条項は1文字も省略するな。全文を返せ。"
    "金額は1円単位で正確に。住所は原文通り。漢字を変えるな。"
    "与えられた契約書（PDF/画像/テキスト）から、次の JSON 構造で読み取れる項目を返してください。"
    "読み取れない項目は null、配列は空配列に。推測で埋めないこと。前置き不要、JSON のみ。\n\n"
    "{\n"
    '  "bukken_type": "戸建|区分",\n'
    '  "urinushi": {"address":..,"name":..},\n'
    '  "kainushi": {"address":..,"name":..},\n'
    '  "gyosha": {"shomei":..,"shozai":..,"tel":..,"daihyo":..},\n'
    '  "torikiishi": {"shimei":..,"toroku_no":..},\n'
    '  "fudosan": {"bukken_type":..,"jukyo_hyoji":..,'
    '"tochi":{"shozai":..,"chiban":"地番(例:12番5)","chimoku":..,"chiseki_toki":..,"chiseki_jissoku":..,"mochibun":"持分(例:全部,1/2)"},'
    '"tatemono":{"shozai":"建物所在(例:水戸市藤が原三丁目12番地5)","kaoku_bango":..,"shurui":..,"kozo":..,"yukamenseki":..,"chikujiki":..},'
    '"ittou_shozai":..,"senyuu":{"kaoku_bango":..,"yukamenseki":..},'
    '"shikichiken":[{"shozai":..,"chiban":..,"chiseki":..,"wariai":..}]},\n'
    '  "daikin": {"baibai_daikin":整数,"shohizei":整数,"tetsuke":整数,'
    '"uchikin1":整数,"uchikin1_date":..,"uchikin2":整数,"uchikin2_date":..,'
    '"zankin":整数,"zankin_date":..,"iyakukin_wariai":"違約金の額(売買代金の%。整数)"},\n'
    '  "hikiwatashi_date": "引渡し日","seisan_kisanbi":"公租公課の清算起算日",'
    '"keiyaku_date":"契約締結日",\n'
    '  "loan_tokuyaku": true/false,"loan_kingaku":整数,"loan_shonin_date":..,'
    '"loan_kaijo_date":"融資特約に基づく契約解除期日",\n'
    '  "tokuyaku": ["特約事項..."],\n'
    '  "jokan": [{"jo":"第1条","midashi":"見出し","honbun":"本文"}]\n'
    "}\n"
    "金額は円の整数（カンマ無し）。約款(jokan)は条ごとに分けて、本文も読み取れる範囲で含める。"
    "\n\n精度向上のための追加指示:\n"
    "1. 建物所在(tatemono.shozai)は必ず読め。「不動産の表示」の建物の欄にある\n"
    "2. 家屋番号(tatemono.kaoku_bango)も必ず読め\n"
    "3. 売買代金のうち土地代金・建物代金の内訳があれば daikin.tochi_kakaku, daikin.tatemono_kakaku に入れろ\n"
    "4. 買主住所(kainushi.address)は手書きでも読み取れ\n"
    "5. 融資利用(loan_tokuyaku)の有無・金額・銀行名(loan_bank)を必ず読め\n"
    "6. 引渡日(hikiwatashi_date)・清算起算日(seisan_kisanbi)・契約日(keiyaku_date)を必ず読め\n"
    "7. 建物の構造・種類・床面積・築年月も正確に\n"
    "8. 買主住所(kainushi.address)は最重要。署名欄・末尾の住所を手書きでも必ず読め。マンション名・部屋番号まで\n"
    "9. 売主住所(urinushi.address)も同様に手書きでも読め\n"
    "10. 違約金割合(iyakukin_wariai)は「売買代金の20%」なら20\n"
    "11. 土地代金(tochi_kakaku)・建物代金(tatemono_kakaku)の内訳が表にあれば必ず抽出"
)


# 直接PDF送信の上限（Anthropic の 32MB/100頁 とリクエスト全体上限に対し安全側）。
# 超えたら本文テキスト送信 or 頁分割にフォールバックする。
_MAX_DIRECT_BYTES = 18 * 1024 * 1024
_MAX_DIRECT_PAGES = 95
_BATCH_MAX_BYTES = 15 * 1024 * 1024
_MAX_SPLIT_BATCHES = 12   # 頁分割時の最大処理ブロック数（延々API呼び出しを続けない）


def _instruction(doc_type: str) -> dict[str, Any]:
    doc = "売買契約書" if doc_type == "keiyaku" else "重要事項説明書"
    return {"type": "text", "text": f"次の{doc}から項目を抽出してください。"}


def _pdf_stats(b64: str) -> tuple[bytes, int, int | None, str]:
    """PDFの (バイト列, サイズ, 頁数, 抽出テキスト) を返す。pypdf 不在/破損時は頁数None・空文字。"""
    raw = base64.b64decode(b64)
    pages: int | None = None
    text = ""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        pages = len(reader.pages)
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except BaseException:  # noqa: BLE001 破損PDF・暗号化・pypdf不備(pyo3 panic等)でも落とさない
        pass
    return raw, len(raw), pages, text


def _slice_pdf_b64(raw: bytes, start: int, end: int) -> str | None:
    """PDFの [start, end) 頁だけの小PDFをbase64で返す。失敗時 None。"""
    try:
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(io.BytesIO(raw))
        writer = PdfWriter()
        for i in range(start, min(end, len(reader.pages))):
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except BaseException:  # noqa: BLE001 pypdf不備(pyo3 panic等)でも落とさない
        return None


def _err_detail(e: BaseException) -> str:
    """API例外を診断しやすい短文にする（HTTPステータス＋種別＋メッセージ）。"""
    status = getattr(e, "status_code", None)
    head = f"HTTP {status} " if status else ""
    return f"{head}{type(e).__name__}: {str(e)[:180]}"


def _call_claude_json(doc_type: str, pieces: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Claudeを1回呼び、JSONを返す。API失敗/JSON崩れは None（呼び出し側で継続）。"""
    from anthropic import Anthropic
    system = _EXTRACT_SYS_KEIYAKU if doc_type == "keiyaku" else _EXTRACT_SYS
    client = Anthropic(max_retries=4, timeout=180.0)
    msg = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS, system=system,
        messages=[{"role": "user", "content": [_instruction(doc_type), *pieces]}],
    )
    raw = "".join(b.text for b in msg.content
                  if getattr(b, "type", None) == "text").strip()
    return _parse_json_loose(raw)


def _parse_json_loose(raw: str) -> dict[str, Any] | None:
    """コードフェンス除去＋最初の {...} 抽出まで試すゆるいJSON解析。無理なら None。"""
    if not raw:
        return None
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        raw = raw[4:] if raw.lstrip().startswith("json") else raw
    raw = raw.strip()
    for candidate in (raw, raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else ""):
        try:
            obj = json.loads(candidate)
            return obj if isinstance(obj, dict) else None
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _merge_extracted(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """2つの抽出結果を統合。スカラは先勝ち（先の非空を保持）、リストは連結重複除去、辞書は再帰。"""
    out = dict(a)
    for k, v in b.items():
        if k not in out or out[k] in (None, "", [], {}):
            out[k] = v
        elif isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge_extracted(out[k], v)
        elif isinstance(out[k], list) and isinstance(v, list):
            seen = {json.dumps(x, ensure_ascii=False, sort_keys=True) for x in out[k]}
            out[k] += [x for x in v
                       if json.dumps(x, ensure_ascii=False, sort_keys=True) not in seen]
    return out






def _extract_amounts(file_base64: str) -> dict:
    """金額情報だけを特化して読み取る（2段階目）"""
    from anthropic import Anthropic
    import base64, io, json
    
    try:
        from pdf2image import convert_from_bytes
        pdf_bytes = base64.b64decode(file_base64)
        images = convert_from_bytes(pdf_bytes, first_page=1, last_page=1, dpi=200)
        buf = io.BytesIO()
        images[0].save(buf, format="JPEG", quality=95)
        img_b64 = base64.b64encode(buf.getvalue()).decode()
        # メモリ解放（1頁目の画像・元PDFを溜めない）
        del images, buf, pdf_bytes
    except Exception:
        img_b64 = file_base64  # 画像の場合はそのまま
    
    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1000,
            system=(
                "この不動産売買契約書の画像から金額を正確に読み取ってJSONで返せ。"
                "読み取れない項目はnull。金額は円の整数（カンマなし）。\n"
                '{"baibai_daikin":整数,"tochi_kakaku":整数,"tatemono_kakaku":整数,'
                '"shohizei":整数,"tetsuke":整数,"zankin":整数,"zankin_date":"日付",'
                '"iyakukin_wariai":整数,"yuushi":true/false,"yuushi_kingaku":整数,"yuushi_bank":"銀行名"}'
            ),
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "売買契約書から金額情報を全て読み取って。表の中の数字を1円単位で正確に。"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}}
            ]}]
        )
        text = resp.content[0].text
        # JSON部分を抽出
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        return {}

# 全ページ画像読取の1回あたり最大処理頁数（延々とAPIを呼ばない安全上限）。
_MULTIPAGE_MAX = 40


def _extract_multipage(file_base64: str, doc_type: str, mime: str = "application/pdf"
                       ) -> tuple[dict[str, Any], str]:
    """PDF全ページを1頁ずつ画像化 → 各頁をClaudeへ送信 → 結果をマージして返す。

    メモリ管理: 画像はディスク(一時フォルダ)へ書き出し（paths_only）、1頁ずつ読み込んで
    処理し、処理後に必ず del / ファイル削除する（全頁の画像をメモリに溜めない）。
    どんな入力でも例外は投げず (結果, 警告) を返す。
    """
    import tempfile
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        return {}, "pdf2imageが未インストールです。"
    try:
        pdf_bytes = base64.b64decode(file_base64)
    except Exception as e:  # noqa: BLE001
        return {}, f"PDFデコード失敗: {type(e).__name__}"

    merged: dict[str, Any] = {}
    warnings: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        # 画像はメモリではなくディスクへ（paths_only）。全頁を一度にメモリへ載せない。
        try:
            paths = convert_from_bytes(
                pdf_bytes, dpi=150, fmt="jpeg",
                output_folder=tmpdir, paths_only=True)
        except Exception as e:  # noqa: BLE001
            return {}, f"PDF画像変換失敗: {type(e).__name__}"
        finally:
            del pdf_bytes                       # 元PDFバイト列を即解放
        if not paths:
            return {}, "PDFにページがありません。"

        total = len(paths)
        capped = paths[:_MULTIPAGE_MAX]
        for i, p in enumerate(capped):
            page_b64 = None
            try:
                with open(p, "rb") as fh:
                    page_b64 = base64.b64encode(fh.read()).decode("ascii")
            except Exception:  # noqa: BLE001
                warnings.append(f"{i + 1}頁 読込失敗")
                continue
            finally:
                try:
                    os.remove(p)                # 使い終えた画像ファイルを削除
                except OSError:
                    pass
            try:
                page_data = _call_claude_json(doc_type, [{"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg", "data": page_b64}}])
                if page_data:
                    merged = _merge_extracted(merged, page_data)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"{i + 1}頁 読取エラー({type(e).__name__})")
            finally:
                del page_b64                    # 頁画像(base64)をメモリから解放
        if total > _MULTIPAGE_MAX:
            warnings.append(f"全{total}頁中、先頭{_MULTIPAGE_MAX}頁のみ読み取りました（要確認）")

    return merged, (" / ".join(warnings) if warnings else "")

def _deep_merge(base: dict, extra: dict) -> dict:
    """2つのdictを深くマージ（extraで上書きしない、補完のみ）"""
    result = dict(base)
    for k, v in extra.items():
        if v is None or v == {} or v == [] or v == "":
            continue
        if k not in result or result[k] is None or result[k] == {} or result[k] == [] or result[k] == "":
            result[k] = v
        elif isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        elif isinstance(result[k], list) and isinstance(v, list):
            # リストは結合（重複排除）
            for item in v:
                if item not in result[k]:
                    result[k].append(item)
    return result

def _extract_robust(req: ExtractReq) -> tuple[dict[str, Any], str]:
    """(抽出結果, 警告) を返す。どんな入力でも例外を投げない。
    重い/大きいPDFは 直接送信→本文テキスト→頁分割 の順に自動フォールバックし、
    一部が失敗しても取れた分を返す。全滅時は空dict＋理由（手入力で続行可能）。"""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return {}, "自動読取ライブラリ(anthropic)が未導入のため、手入力で作成してください。"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}, "自動読取(APIキー)が未設定のため、手入力で作成してください。"

    text_piece = ([{"type": "text", "text": req.text}]
                  if req.text and req.text.strip() else [])

    # 画像はそのまま1回だけ試す（分割不可）。
    if req.file_base64 and req.mime.startswith("image/"):
        piece = [{"type": "image", "source": {
            "type": "base64", "media_type": req.mime, "data": req.file_base64}}]
        try:
            data = _call_claude_json(req.doc_type, piece + text_piece)
        except Exception as e:  # noqa: BLE001
            return {}, f"画像の自動読取に失敗しました（{type(e).__name__}）。手入力で続行できます。"
        return (data, "") if data else ({}, "画像から項目を読み取れませんでした。手入力で続行できます。")

    # PDF
    if req.file_base64 and req.mime == "application/pdf":
        raw, size, pages, text = _pdf_stats(req.file_base64)
        api_err = ""   # 最後に掴んだAPI例外（全滅時に原因として警告へ載せる）

        def _doc(b64: str) -> list[dict[str, Any]]:
            return [{"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf", "data": b64}}]

        small = size <= _MAX_DIRECT_BYTES and (pages is None or pages <= _MAX_DIRECT_PAGES)
        # 各ページ平均40字以上（最低500字）あれば「テキスト層が十分」＝画像化不要。
        # 少なければスキャン/画像PDFとみなし、全ページをPDF（＝画像）として送る。
        good_text = bool(text and len(text.strip()) >= max(500, 40 * (pages or 1)))

        # ① テキスト層が十分: 全ページ分のテキストをまとめて送る（軽く・全頁を確実に読む）。
        if good_text:
            try:
                data = _call_claude_json(
                    req.doc_type, [{"type": "text", "text": text[:200_000]}] + text_piece)
                if data:
                    return data, ""
            except Exception as e:  # noqa: BLE001 テキスト送信失敗 → 画像PDFへ
                api_err = _err_detail(e)

        # ② テキストが薄い（スキャンPDF）or ①失敗: PDF全体を送る。
        #    Anthropic の document は**全ページを画像として解釈**するため、全頁が読まれる。
        if small:
            # ① 全ページを1頁ずつ画像化 → 各頁をClaudeへ → マージ（メインの画像PDF経路）。
            try:
                data, mp_warn = _extract_multipage(req.file_base64, req.doc_type, req.mime)
            except Exception as e:  # noqa: BLE001
                data, mp_warn = None, ""
                api_err = _err_detail(e)
            if data:
                return data, (mp_warn or ("" if good_text else
                              "テキスト層が薄いPDFのため全ページを画像として読み取りました（要確認）。"))
            if mp_warn:
                api_err = api_err or mp_warn
            # ② 全ページ画像化が使えない/失敗 → PDF全体を1回で直接送信（document）。
            try:
                data = _call_claude_json(req.doc_type, _doc(req.file_base64) + text_piece)
                if data:
                    note = ("" if good_text else
                            "テキスト層が薄いPDFのため全ページを画像として読み取りました（要確認）。")
                    return data, note
            except Exception as e:  # noqa: BLE001 大きめ等で失敗 → 下のフォールバックへ
                api_err = _err_detail(e)

        # フォールバック: 頁分割して全ページを1バッチずつ処理し、取れた分を統合。
        # 巨大資料でAPI呼び出しが延々続かないよう、処理バッチ数に上限を設ける
        # （超過分は先頭から重説の要点が入る前提で打ち切り、警告で明示）。
        if pages and pages > 0:
            per = max(1, min(_MAX_DIRECT_PAGES,
                             int(pages * _BATCH_MAX_BYTES / size) if size else pages))
            starts = list(range(0, pages, per))
            capped = starts[:_MAX_SPLIT_BATCHES]
            merged: dict[str, Any] = {}
            fail = 0
            for start in capped:
                sub = _slice_pdf_b64(raw, start, start + per)
                if not sub:
                    fail += 1
                    continue
                try:
                    part = _call_claude_json(req.doc_type, _doc(sub))
                except Exception as e:  # noqa: BLE001
                    part = None
                    api_err = _err_detail(e)
                if part:
                    merged = _merge_extracted(merged, part)
                else:
                    fail += 1
            skipped = len(starts) - len(capped)
            if merged:
                if fail == 0 and skipped == 0:
                    note = "資料が大きいため分割して読み取りました（要確認）。"
                else:
                    miss = fail + skipped
                    note = (f"資料が大きく一部（{miss}ブロック）は読み取っていません。"
                            "取れた範囲を反映しました（要確認・不足は手入力）。")
                return merged, note

        # テキスト層があるなら最後の望みで送る（分割も失敗した場合）。
        if text and text.strip():
            try:
                data = _call_claude_json(
                    req.doc_type, [{"type": "text", "text": text[:120_000]}])
                if data:
                    return data, "資料が重いためテキスト抽出で読み取りました（要確認）。"
            except Exception as e:  # noqa: BLE001
                api_err = _err_detail(e)
        tail = f" [原因: {api_err}]" if api_err else ""
        return {}, ("資料が重い/読みにくいため自動読取できませんでした。手入力で続行できます。"
                    + tail)

    # テキストのみ
    if text_piece:
        try:
            data = _call_claude_json(req.doc_type, text_piece)
            return (data, "") if data else ({}, "テキストから項目を読み取れませんでした。手入力で続行できます。")
        except Exception as e:  # noqa: BLE001
            return {}, f"自動読取に失敗しました。手入力で続行できます。 [原因: {_err_detail(e)}]"

    return {}, "読み取る資料（PDF/画像/テキスト）が指定されていません。"


def _normalize_company_names(data: dict[str, Any]) -> dict[str, Any]:
    """会社名表記ゆれ・全角スラッシュを正規化。"""
    def _walk(obj: Any) -> Any:
        if isinstance(obj, str):
            obj = obj.replace("株式会社 Martial Arts", "株式会社Martial Arts")
            obj = obj.replace("／", "/")
        elif isinstance(obj, dict):
            return {k: _walk(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_walk(v) for v in obj]
        return obj
    return _walk(data) if data else data


@app.post("/extract", response_model=ExtractResp)
def extract(req: ExtractReq) -> ExtractResp:
    """AB書類を自動読取する。**どんな資料・重さでも 500 で止めない**：
    読み取れなければ空データ＋警告を返し、UI 側で手入力に切り替えられる。"""
    try:
        data, warning = _extract_robust(req)
        data = _normalize_company_names(data)
        data = _normalize_extracted(data)
    except BaseException as e:  # noqa: BLE001 想定外(panic含む)も握りつぶし手入力へ誘導（絶対に落とさない）
        data, warning = {}, f"自動読取で予期しない問題（{type(e).__name__}）。手入力で続行できます。"
    try:
        data = _normalize_company_names(data)
    except BaseException:  # noqa: BLE001 正規化失敗も無視（生データで返す）
        pass
    # ② 金額（売買代金）が取れていなければ、1頁目画像で金額特化の再読取（2段階目）。
    joken = data.get("joken") or {}
    if not joken.get("baibai_daikin") and req.file_base64:
        try:
            amounts = _extract_amounts(req.file_base64)
            if amounts:
                jk = data.setdefault("joken", {})
                for k, val in amounts.items():
                    if val in (None, "", 0):
                        continue
                    if not jk.get(k):        # 既に取れている値は上書きしない
                        jk[k] = val
        except Exception:  # noqa: BLE001 2段階目失敗でも1段階目の結果を返す
            pass
    try:
        data = _post_process_extracted(data)
    except BaseException:  # noqa: BLE001
        pass
    gc.collect()  # メモリ解放
    return ExtractResp(extracted=data, warning=warning)


class ExtractToukiResp(BaseModel):
    kind: str = "不明"                 # 建物 / 土地 / 不明
    fill: dict[str, str] = {}          # Web UI の入力欄 id(mp_*) → 値（取れた欄のみ）
    fudosan_bango: str | None = None   # 不動産番号（参考情報）
    warning: str = ""                  # 非致命メッセージ（画像・様式外など）


@app.post("/extract_touki", response_model=ExtractToukiResp)
def extract_touki(req: ExtractReq) -> ExtractToukiResp:
    """登記PDF（機械可読テキスト）から物件マスタ欄を自動転記する。

    画像スキャンの登記はテキストが取れないため、AIを使わず警告を返して手入力へ誘導する。
    どんな入力でも 500 で止めない（読めなければ warning＋空 fill を返す）。
    """
    text = (req.text or "").strip()
    if not text and req.file_base64:
        try:
            _, _, _, text = _pdf_stats(req.file_base64)
        except BaseException:  # noqa: BLE001 破損PDF等でも落とさない
            text = ""
    text = (text or "").strip()
    if not text:
        return ExtractToukiResp(
            warning="この登記PDFは画像（スキャン）のため自動取り込みできません。"
                    "お手数ですが、物件欄は手入力してください。")
    if not touki_parser.looks_like_touki(text):
        return ExtractToukiResp(
            warning="登記事項証明書として認識できませんでした。ファイルをご確認のうえ手入力してください。")
    try:
        res = touki_parser.parse_touki_text(text)
    except BaseException as e:  # noqa: BLE001 解析失敗も手入力へ誘導
        return ExtractToukiResp(
            warning=f"登記の解析中に問題が発生しました（{type(e).__name__}）。手入力してください。")
    return ExtractToukiResp(
        kind=res.get("kind", "不明"),
        fill=res.get("fill", {}),
        fudosan_bango=res.get("fudosan_bango"),
        warning=" ".join(res.get("notes") or []))



# ── Manus連携エンドポイント ──────────────────────────────────

class ManusToukiReq(BaseModel):
    address: str
    prop_type: str = "both"
    wait: bool = False

class ManusToukiResp(BaseModel):
    ok: bool = False
    task_id: str = ""
    task_url: str = ""
    status: str = ""
    credit_usage: int = 0
    result_text: str = ""
    parsed: dict | None = None
    error: str = ""

@app.post("/manus/touki", response_model=ManusToukiResp)
def manus_touki(req: ManusToukiReq) -> ManusToukiResp:
    """Manus AIで登記情報提供サービスから登記情報を取得する."""
    if manus_client is None:
        return ManusToukiResp(error="Manus連携モジュールが利用できません")
    try:
        r = manus_client.fetch_touki(req.address, req.prop_type, wait=req.wait)
        return ManusToukiResp(**{k: v for k, v in r.items() if k in ManusToukiResp.model_fields})
    except Exception as e:
        return ManusToukiResp(error=f"{type(e).__name__}: {e}")


class ManusReinsReq(BaseModel):
    address: str
    wait: bool = False

class ManusReinsResp(BaseModel):
    ok: bool = False
    task_id: str = ""
    task_url: str = ""
    status: str = ""
    credit_usage: int = 0
    result_text: str = ""
    parsed: dict | None = None
    error: str = ""

@app.post("/manus/reins", response_model=ManusReinsResp)
def manus_reins(req: ManusReinsReq) -> ManusReinsResp:
    """Manus AIでレインズから成約事例・売出情報を取得する."""
    if manus_client is None:
        return ManusReinsResp(error="Manus連携モジュールが利用できません")
    try:
        r = manus_client.fetch_reins(req.address, wait=req.wait)
        return ManusReinsResp(**{k: v for k, v in r.items() if k in ManusReinsResp.model_fields})
    except Exception as e:
        return ManusReinsResp(error=f"{type(e).__name__}: {e}")


class ManusHoujinToukiReq(BaseModel):
    company_name: str
    wait: bool = False

class ManusHoujinToukiResp(BaseModel):
    ok: bool = False
    task_id: str = ""
    task_url: str = ""
    status: str = ""
    credit_usage: int = 0
    result_text: str = ""
    parsed: dict | None = None
    error: str = ""

@app.post("/manus/houjin-touki", response_model=ManusHoujinToukiResp)
def manus_houjin_touki(req: ManusHoujinToukiReq) -> ManusHoujinToukiResp:
    """Manus AIで登記情報提供サービスから法人登記簿を取得する."""
    if manus_client is None:
        return ManusHoujinToukiResp(error="Manus連携モジュールが利用できません")
    try:
        r = manus_client.fetch_corporate_touki(req.company_name, wait=req.wait)
        return ManusHoujinToukiResp(**{k: v for k, v in r.items() if k in ManusHoujinToukiResp.model_fields})
    except Exception as e:
        return ManusHoujinToukiResp(error=f"{type(e).__name__}: {e}")


class ManusCheckReq(BaseModel):
    task_id: str

@app.post("/manus/check", response_model=ManusToukiResp)
def manus_check(req: ManusCheckReq) -> ManusToukiResp:
    """Manusタスクの進行状況を確認する."""
    if manus_client is None:
        return ManusToukiResp(error="Manus連携モジュールが利用できません")
    try:
        r = manus_client.check_task(req.task_id)
        return ManusToukiResp(**{k: v for k, v in r.items() if k in ManusToukiResp.model_fields})
    except Exception as e:
        return ManusToukiResp(error=f"{type(e).__name__}: {e}")


# ── 周辺施設検索 ──────────────────────────────────

class NearbyReq(BaseModel):
    lat: float
    lon: float
    radius: int = 1000

@app.post("/nearby")
def nearby_facilities(req: NearbyReq) -> dict:
    """Nominatim + Overpass APIで周辺施設を検索する."""
    import urllib.request, urllib.parse
    facilities: dict[str, list] = {}
    queries = {
        "station": '[out:json];node(around:{r},{lat},{lon})["railway"="station"];out body 5;',
        "school": '[out:json];(node(around:{r},{lat},{lon})["amenity"="school"];way(around:{r},{lat},{lon})["amenity"="school"];);out body 5;',
        "hospital": '[out:json];(node(around:{r},{lat},{lon})["amenity"="hospital"];way(around:{r},{lat},{lon})["amenity"="hospital"];);out body 3;',
        "convenience": '[out:json];node(around:{r},{lat},{lon})["shop"="convenience"];out body 3;',
        "supermarket": '[out:json];(node(around:{r},{lat},{lon})["shop"="supermarket"];way(around:{r},{lat},{lon})["shop"="supermarket"];);out body 3;',
        "park": '[out:json];(node(around:{r},{lat},{lon})["leisure"="park"];way(around:{r},{lat},{lon})["leisure"="park"];);out body 3;',
    }
    for cat, q in queries.items():
        try:
            query = q.format(r=req.radius, lat=req.lat, lon=req.lon)
            url = "https://overpass-api.de/api/interpreter"
            data = urllib.parse.urlencode({"data": query}).encode()
            r = urllib.request.Request(url, data=data, headers={"User-Agent": "BC-Pipeline/1.0"})
            with urllib.request.urlopen(r, timeout=15) as resp:
                import json as _json
                result = _json.loads(resp.read())
                items = []
                for el in result.get("elements", []):
                    tags = el.get("tags", {})
                    name = tags.get("name", tags.get("name:ja", ""))
                    if not name:
                        continue
                    elat = el.get("lat") or el.get("center", {}).get("lat")
                    elon = el.get("lon") or el.get("center", {}).get("lon")
                    dist = None
                    if elat and elon:
                        import math as _math
                        dlat = _math.radians(elat - req.lat)
                        dlon = _math.radians(elon - req.lon)
                        a = _math.sin(dlat/2)**2 + _math.cos(_math.radians(req.lat)) * _math.cos(_math.radians(elat)) * _math.sin(dlon/2)**2
                        dist = int(6371000 * 2 * _math.atan2(_math.sqrt(a), _math.sqrt(1-a)))
                    items.append({"name": name, "distance_m": dist})
                items.sort(key=lambda x: x.get("distance_m") or 99999)
                facilities[cat] = items
        except Exception:
            facilities[cat] = []
    return {"ok": True, "facilities": facilities}


# ── ワンクリック全自動エンリッチ ──────────────────────────────────

class AutoEnrichReq(BaseModel):
    address: str

@app.post("/auto_enrich")
def auto_enrich(req: AutoEnrichReq) -> dict:
    """住所1つで法令・ハザード・地価・周辺施設を一括取得する."""
    result: dict[str, Any] = {"address": req.address}

    # 1. geo_horei: 法令・ハザード・都市計画・地価
    try:
        import geo_horei
        info = geo_horei.lookup(req.address)
        result["geo"] = info.get("geo")
        result["horei"] = info.get("horei")
        result["hazard"] = info.get("hazard")
        result["toshi_keikaku"] = info.get("toshi_keikaku")
        result["chika"] = info.get("chika")
    except Exception as e:
        result["geo_error"] = str(e)

    # 2. 周辺施設
    geo = result.get("geo") or {}
    lat = geo.get("lat")
    lon = geo.get("lon")
    if lat and lon:
        try:
            nr = NearbyReq(lat=lat, lon=lon)
            nb = nearby_facilities(nr)
            result["nearby"] = nb.get("facilities", {})
        except Exception:
            result["nearby"] = {}
        result["map_url"] = f"https://www.google.com/maps?q={lat},{lon}&z=17"
    else:
        result["nearby"] = {}

    return result


def _normalize_company_name(name: str) -> str:
    """会社名を正規化（全角→半角、スペース統一）"""
    if not name:
        return name
    # 全角英数を半角に
    result = name
    zen = 'ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ０１２３４５６７８９'
    han = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    for z, h in zip(zen, han):
        result = result.replace(z, h)
    # 全角スペースを半角に
    result = result.replace('　', ' ')
    # 連続スペースを1つに
    while '  ' in result:
        result = result.replace('  ', ' ')
    return result.strip()

def _normalize_extracted(data: dict[str, Any]) -> dict[str, Any]:
    """抽出結果の表記ゆれを正規化する（法令名→正式名称・用途地域→正式名称）。"""
    import horei_master

    horei = data.get("horei")
    if isinstance(horei, dict):
        laws = horei.get("other_horei")
        if isinstance(laws, list):
            horei["other_horei"] = [horei_master.normalize_horei(x) for x in laws]
        if horei.get("yoto"):
            horei["yoto"] = normalize_yoto(horei["yoto"])
        for k in ("yoseki_zenmen_doro", "yoseki_etc"):
            if isinstance(horei.get(k), (int, float)):
                horei[k] = str(horei[k])
    return data


def _post_process_extracted(data: dict[str, Any]) -> dict[str, Any]:
    """抽出後の自動補完・表記ゆれ修正。精度90%→95%+を狙う。"""
    if not data:
        return data
    import re

    # --- 1. 残代金の自動計算 (zankin = baibai_daikin - tetsuke) ---
    joken = data.get("joken")
    if isinstance(joken, dict):
        baibai = _safe_int(joken.get("baibai_daikin"))
        tetsuke = _safe_int(joken.get("tetsuke"))
        zankin = _safe_int(joken.get("zankin"))
        if baibai and tetsuke and not zankin:
            joken["zankin"] = baibai - tetsuke
    daikin = data.get("daikin")
    if isinstance(daikin, dict):
        baibai_d = _safe_int(daikin.get("baibai_daikin"))
        tetsuke_d = _safe_int(daikin.get("tetsuke"))
        zankin_d = _safe_int(daikin.get("zankin"))
        if baibai_d and tetsuke_d and not zankin_d:
            daikin["zankin"] = baibai_d - tetsuke_d

    # --- 2. 建物構造の表記ゆれ正規化 ---
    for section in ("tatemono", "senyuu"):
        t = data.get(section)
        if isinstance(t, dict) and t.get("kozo"):
            t["kozo"] = _normalize_kozo(t["kozo"])
    if isinstance(data.get("ittou_kozo"), str):
        data["ittou_kozo"] = _normalize_kozo(data["ittou_kozo"])

    # --- 3. 築年月の「新築」サフィックス補完 ---
    for section in ("tatemono", "senyuu"):
        t = data.get(section)
        if isinstance(t, dict) and t.get("chikujiki"):
            t["chikujiki"] = _normalize_chikujiki(t["chikujiki"])

    # --- 4. fudosan配下のtatemono/tochiも同様に正規化 ---
    fudosan = data.get("fudosan")
    if isinstance(fudosan, dict):
        for section in ("tatemono", "kubun_tatemono"):
            t = fudosan.get(section)
            if isinstance(t, dict):
                if t.get("kozo"):
                    t["kozo"] = _normalize_kozo(t["kozo"])
                if t.get("chikujiki"):
                    t["chikujiki"] = _normalize_chikujiki(t["chikujiki"])

    # --- 5. jokenとdaikinの相互補完 ---
    joken = data.get("joken")
    daikin = data.get("daikin")
    if isinstance(joken, dict) and isinstance(daikin, dict):
        _cross_fill(joken, daikin, "baibai_daikin")
        _cross_fill(joken, daikin, "tetsuke")
        _cross_fill(joken, daikin, "zankin")
        _cross_fill(joken, daikin, "tochi_kakaku")
        _cross_fill(joken, daikin, "tatemono_kakaku")
        _cross_fill(joken, daikin, "shohizei")
        _cross_fill(joken, daikin, "iyakukin_wariai")

    # --- 6. keiyaku_dateの補完（トップレベル↔joken） ---
    if data.get("keiyaku_date") and isinstance(joken, dict) and not joken.get("keiyaku_date"):
        joken["keiyaku_date"] = data["keiyaku_date"]

    # --- 7. 面積の文字列正規化 ---
    for section_key in ("tochi", "fudosan"):
        sect = data.get(section_key)
        if isinstance(sect, dict):
            for area_key in ("chiseki", "chiseki_toki", "chiseki_jissoku", "menseki"):
                if sect.get(area_key):
                    sect[area_key] = _normalize_area(str(sect[area_key]))
            tochi_inner = sect.get("tochi")
            if isinstance(tochi_inner, dict):
                for area_key in ("chiseki", "chiseki_toki", "chiseki_jissoku"):
                    if tochi_inner.get(area_key):
                        tochi_inner[area_key] = _normalize_area(str(tochi_inner[area_key]))

    # --- 8. 金額フィールドを数値に正規化 ---
    for container in (joken, daikin):
        if not isinstance(container, dict):
            continue
        for money_key in ("baibai_daikin", "tetsuke", "zankin", "tochi_kakaku",
                          "tatemono_kakaku", "shohizei", "loan_kingaku"):
            v = container.get(money_key)
            if isinstance(v, str) and v.strip():
                parsed = _safe_int(v)
                if parsed is not None:
                    container[money_key] = parsed

    return data


def _safe_int(val: Any) -> int | None:
    if val is None or val == "" or val == 0:
        return None
    try:
        s = str(val).replace(",", "").replace("円", "").replace("¥", "").strip()
        return int(float(s)) if s else None
    except (ValueError, TypeError):
        return None


def _cross_fill(a: dict, b: dict, key: str) -> None:
    """a[key]が空ならb[key]で補完、逆も同様。"""
    va, vb = a.get(key), b.get(key)
    if not va and va != 0 and vb:
        a[key] = vb
    elif not vb and vb != 0 and va:
        b[key] = va


def _normalize_area(val: str) -> str:
    """面積の㎡表記を統一。「180.02 ㎡」→「180.02」"""
    if not val:
        return val
    return val.replace("㎡", "").replace("m²", "").replace("m2", "").strip()


def _normalize_kozo(kozo: str) -> str:
    """「木造 / 瓦ぶき / 2階建」→「木造瓦ぶき2階建」"""
    if not kozo:
        return kozo
    import re
    result = kozo.replace(" / ", "").replace("／", "").replace("/ ", "").replace(" /", "")
    result = result.replace("　", "").strip()
    result = re.sub(r'(\d)\s*階\s*建', r'\1階建', result)
    return result


def _normalize_chikujiki(val: str) -> str:
    """「平成30年11月」→「平成30年11月新築」"""
    if not val:
        return val
    import re
    val = val.strip()
    if re.search(r'(新築|増築|改築)$', val):
        return val
    if re.search(r'年\d{1,2}月$', val):
        return val + "新築"
    if re.search(r'年$', val):
        return val + "新築"
    return val


# ===== 生成済みファイルの保存・管理 =====
import shutil
from pathlib import Path

_SAVED_DIR = Path(__file__).parent / "saved_docs"
_SAVED_DIR.mkdir(exist_ok=True)

class SaveReq(BaseModel):
    filename: str
    xlsx_base64: str
    tanto: str = ""       # 担当
    doko: str = ""        # 同行
    sakusei: str = ""     # 作成者
    shunin_check: bool = False   # 主任者確認
    notes: str = ""       # メモ

@app.post("/save")
def save_doc(req: SaveReq) -> dict[str, Any]:
    """生成したExcelを保存"""
    import base64, json, datetime
    
    # ファイル保存
    safe_name = req.filename.replace("/", "_").replace("\\", "_")
    xlsx_path = _SAVED_DIR / safe_name
    xlsx_bytes = base64.b64decode(req.xlsx_base64)
    xlsx_path.write_bytes(xlsx_bytes)
    
    # メタデータ保存
    meta = {
        "filename": safe_name,
        "created": datetime.datetime.now().isoformat(),
        "tanto": req.tanto,
        "doko": req.doko,
        "sakusei": req.sakusei,
        "shunin_check": req.shunin_check,
        "notes": req.notes,
        "size": len(xlsx_bytes),
    }
    meta_path = _SAVED_DIR / (safe_name + ".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return {"status": "ok", "filename": safe_name, "path": str(xlsx_path)}

@app.get("/saved")
def list_saved() -> list[dict[str, Any]]:
    """保存済みファイル一覧"""
    import json
    docs = []
    for meta_file in sorted(_SAVED_DIR.glob("*.meta.json"), reverse=True):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            docs.append(meta)
        except Exception:
            pass
    return docs

@app.get("/saved/{filename}")
def download_saved(filename: str):
    """保存済みファイルをダウンロード"""
    safe_name = filename.replace("/", "_").replace("\\", "_")
    xlsx_path = _SAVED_DIR / safe_name
    if not xlsx_path.exists():
        raise HTTPException(status_code=404, detail="ファイルが見つかりません。")
    import base64
    return {"filename": safe_name, "xlsx_base64": base64.b64encode(xlsx_path.read_bytes()).decode()}

@app.delete("/saved/{filename}")
def delete_saved(filename: str) -> dict[str, str]:
    """保存済みファイルを削除"""
    safe_name = filename.replace("/", "_").replace("\\", "_")
    xlsx_path = _SAVED_DIR / safe_name
    meta_path = _SAVED_DIR / (safe_name + ".meta.json")
    if xlsx_path.exists():
        xlsx_path.unlink()
    if meta_path.exists():
        meta_path.unlink()
    return {"status": "deleted", "filename": safe_name}




# ===== Slack投稿API（議事録フォームから使用）=====
class SlackPostReq(BaseModel):
    channel: str
    text: str
    thread_ts: str | None = None

@app.post("/api/slack-post")
def slack_post(req: SlackPostReq) -> dict:
    import urllib.request as _ur
    _TOKEN = os.environ.get("BC_SLACK_TOKEN", "")
    if not _TOKEN:
        return {"ok": False, "error": "BC_SLACK_TOKEN not configured", "ts": ""}
    _body = {"channel": req.channel, "text": req.text}
    if req.thread_ts:
        _body["thread_ts"] = req.thread_ts
    _d = json.dumps(_body).encode()
    _r = _ur.Request("https://slack.com/api/chat.postMessage",
        data=_d, headers={"Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json"})
    with _ur.urlopen(_r, timeout=15) as _resp:
        _j = json.loads(_resp.read())
    return {"ok": _j.get("ok", False), "error": _j.get("error", ""), "ts": _j.get("ts", "")}

# CORS対応（フォームからのアクセスを許可）
from starlette.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://mac-mini.tail6336ea.ts.net", "http://localhost:8800", "http://127.0.0.1:8800"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)



class SlackSearchReq(BaseModel):
    channel: str
    query: str

@app.post("/api/slack-search")
def slack_search(req: SlackSearchReq) -> dict:
    import urllib.request as _ur
    _TOKEN = os.environ.get("BC_SLACK_TOKEN", "")
    if not _TOKEN:
        return {"found": False}
    _url = f"https://slack.com/api/conversations.history?channel={req.channel}&limit=50"
    _r = _ur.Request(_url, headers={"Authorization": f"Bearer {_TOKEN}"})
    with _ur.urlopen(_r, timeout=15) as _resp:
        _j = json.loads(_resp.read())
    if _j.get("ok"):
        for m in _j.get("messages", []):
            if req.query in m.get("text", ""):
                return {"found": True, "ts": m["ts"], "preview": m["text"][:100]}
    return {"found": False}

# ── /api/customers（CRM顧客一覧。WebUIの顧客選択ドロップダウン用）──
_CRM_PATH = os.environ.get(
    "CRM_JSON_PATH",
    os.path.expanduser("~/.openclaw/workspace/data/crm_customers.json"),
)

_crm_cache: dict[str, Any] = {}


def _load_crm() -> list[dict[str, Any]]:
    import time
    mtime = 0.0
    try:
        mtime = os.path.getmtime(_CRM_PATH)
    except OSError:
        return []
    if _crm_cache.get("mtime") == mtime and _crm_cache.get("data"):
        return _crm_cache["data"]
    try:
        with open(_CRM_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        customers = raw.get("customers", [])
        _crm_cache["data"] = customers
        _crm_cache["mtime"] = mtime
        return customers
    except Exception:
        return []


@app.get("/api/customers")
def api_customers(q: str = "", limit: int = 50) -> dict[str, Any]:
    all_c = _load_crm()
    if q:
        q_lower = q.lower()
        filtered = [
            c for c in all_c
            if q_lower in (c.get("name") or "").lower()
            or q_lower in (c.get("customer_id") or "").lower()
            or q_lower in (c.get("phone") or "").lower()
            or any(q_lower in (p.get("property") or "").lower()
                   for p in (c.get("properties") or []))
        ]
    else:
        filtered = all_c
    results = []
    for c in filtered[:limit]:
        props = c.get("properties") or []
        results.append({
            "id": c.get("customer_id") or c.get("id"),
            "name": c.get("name"),
            "phone": c.get("phone"),
            "rank": c.get("rank"),
            "rep": c.get("rep"),
            "properties": [
                {"address": p.get("property"), "price": p.get("price")}
                for p in props[:5]
            ],
        })
    return {"total": len(filtered), "customers": results}


@app.get("/api/torikiishi")
def api_torikiishi() -> dict[str, Any]:
    import datetime
    import house_style as H
    today = datetime.date.today()
    active = []
    retired = []
    for t in H.SELLER_B_TORIKIISHI:
        rd = t.get("retire_date")
        info = {
            "shimei": t["shimei"],
            "toroku_no": t["toroku_no"],
            "jimusho": t.get("jimusho", "株式会社Martial Arts 本店"),
        }
        if rd:
            try:
                d = datetime.date.fromisoformat(rd)
                if today >= d:
                    info["status"] = "retired"
                    info["retire_date"] = rd
                    retired.append(info)
                    continue
            except ValueError:
                pass
        info["status"] = "active"
        active.append(info)
    return {"active": active, "retired": retired, "recommended": active[0] if active else None}


@app.get("/api/validate-preview")
def api_validate_preview(
    buyer_name: str = "",
    bc_price: int = 0,
    bc_tetsuke: int = 0,
    template: str = "36-1",
) -> dict[str, Any]:
    issues = []
    if not buyer_name.strip():
        issues.append({"level": "error", "field": "買主C", "message": "買主C（お客様）氏名が未入力です。"})
    if bc_price <= 0:
        issues.append({"level": "error", "field": "売買代金", "message": "BC売買代金が未入力です。"})
    if bc_tetsuke <= 0:
        issues.append({"level": "warning", "field": "手付金", "message": "手付金が未入力です。既定値が適用されます。"})
    if bc_price > 0 and bc_tetsuke > bc_price:
        issues.append({"level": "error", "field": "手付金", "message": "手付金が売買代金を超えています。"})
    if bc_price > 0 and bc_tetsuke > 0:
        zankin = bc_price - bc_tetsuke
        issues.append({"level": "info", "field": "残代金", "message": f"残代金: {zankin:,}円"})
    errors = [i for i in issues if i["level"] == "error"]
    return {"valid": len(errors) == 0, "issues": issues}


# ── 営業プレイブック（closing-book） ─────────────────────────────
_PLAYBOOK_PAGES = {
    "index", "01", "02", "03", "04", "05", "06", "07", "08", "09",
    "rank-sheet", "chapter1",
}


@app.get("/sales-playbook", response_class=HTMLResponse)
def sales_playbook_index() -> HTMLResponse:
    """営業プレイブック インデックス画面。"""
    f = _PLAYBOOK_DIR / "index.html"
    if f.exists():
        return HTMLResponse(f.read_text(encoding="utf-8"))
    raise HTTPException(404, "playbook/index.html が見つかりません。")


@app.get("/sales-playbook/{page}", response_class=HTMLResponse)
def sales_playbook_page(page: str) -> HTMLResponse:
    """営業プレイブック 各章。"""
    if page not in _PLAYBOOK_PAGES:
        raise HTTPException(404, "ページが見つかりません。")
    f = _PLAYBOOK_DIR / f"{page}.html"
    if f.exists():
        return HTMLResponse(f.read_text(encoding="utf-8"))
    raise HTTPException(404, f"playbook/{page}.html が見つかりません。")


@app.get("/checklist", response_class=HTMLResponse)
def checklist_page() -> HTMLResponse:
    """書類チェックリスト画面。"""
    if _CHECKLIST.exists():
        return HTMLResponse(_CHECKLIST.read_text(encoding="utf-8"))
    raise HTTPException(404, "checklist.html が見つかりません。")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("BC_PORT", "8800")))
