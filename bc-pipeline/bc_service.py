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
import json
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

import approval
import bundle
import cellmaps
import juyojiko_excel
import keiyaku_excel
import wb_fill
from bc_schema import YOTO_OPTIONS, normalize_yoto, resolve_bukken
from bc_transform import transform_ab_to_bc, transform_keiyaku_ab_to_bc
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
MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "4000"))

app = FastAPI(title="BC自動生成サービス", version="0.2.0")


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


class ExtractReq(BaseModel):
    doc_type: str = "juyojiko"          # juyojiko（重説） / keiyaku（売買契約書）
    bukken: str | None = None
    text: str | None = None
    file_base64: str | None = None
    mime: str = "application/pdf"


class ExtractResp(BaseModel):
    extracted: dict[str, Any]


# ── /health ───────────────────────────────────────────────────
@app.get("/health")
def health() -> dict[str, Any]:
    tdir = os.environ.get("BC_TEMPLATE_DIR", "templates")
    templates = sorted(
        p.stem for p in __import__("pathlib").Path(tdir).glob("*.xlsx")
    ) if os.path.isdir(tdir) else []
    return {
        "status": "ok",
        "version": "0.2.0",
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

    return {
        "yoto": horei_master.YOTO_OPTIONS,
        "chiiki_chiku": horei_master.CHIIKI_CHIKU,
        "other_horei": horei_master.OTHER_HOREI_LAWS,
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
    return {"decision": decision, "approved": decision == "approve", "reaction": reaction}


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
    if template is not None and req.template in cellmaps.JUYOJIKO_BUILDERS:
        try:
            sv, sc = cellmaps.build_juyojiko(req.template, bc)
            xlsx, _ = wb_fill.fill_workbook(template, sv, sc)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"ワークブック差込に失敗: {e}") from e
        prefix = f"BC重説_{req.template}"
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
    )


def _try_template_bytes(req: GenerateReq) -> bytes | None:
    """本番ワークブックのテンプレ実体を返す（無ければ None）。"""
    if req.template_base64:
        return base64.b64decode(req.template_base64)
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
    if template is not None and req.template in cellmaps.KEIYAKU_BUILDERS:
        try:
            sv, sc = cellmaps.build_keiyaku(req.template, bc)
            xlsx, _ = wb_fill.fill_workbook(template, sv, sc)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"ワークブック差込に失敗: {e}") from e
        prefix = f"BC契約書_{req.template}"
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
    )


def _generate_package(req: GenerateReq) -> GenerateResp:
    """重説シートと契約書シートを1つの本番ワークブックへ同時差込する。

    req.ab=重説JSON、req.ab_keiyaku=契約書JSON、req.template=変種、案件マスタを共用。
    本番ワークブック（両シートを含む）が必須。
    """
    template = _try_template_bytes(req)
    if template is None or req.template not in cellmaps.JUYOJIKO_BUILDERS:
        raise HTTPException(
            status_code=400,
            detail="package には template（36-1/37-1/38-1）と本番ワークブックが必要です。")
    if req.ab is None or req.ab_keiyaku is None:
        raise HTTPException(
            status_code=400, detail="package には ab（重説）と ab_keiyaku（契約書）が必要です。")
    try:
        bc_j = transform_ab_to_bc(Juyojiko.model_validate(req.ab), req.deal_master)
        bc_k = transform_keiyaku_ab_to_bc(
            Keiyakusho.model_validate(req.ab_keiyaku), req.deal_master)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"ab の解析に失敗: {e}") from e

    sv_j, sc_j = cellmaps.build_juyojiko(req.template, bc_j)
    sv_k, sc_k = cellmaps.build_keiyaku(req.template, bc_k)
    sheet_values = {**sv_j, **sv_k}      # 重説シート + 契約書シート
    sheet_clear = {**sc_j, **sc_k}
    try:
        xlsx, _ = wb_fill.fill_workbook(template, sheet_values, sheet_clear)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"ワークブック差込に失敗: {e}") from e

    bukken = bc_j.bukken_type or (bc_j.fudosan.bukken_type if bc_j.fudosan else None) or "区分"
    return GenerateResp(
        filename=_filename(f"BC一式_{req.template}", bukken, bc_j.fudosan, req.filename),
        bukken=bukken,
        xlsx_base64=base64.b64encode(xlsx).decode("ascii"),
    )


@app.post("/generate", response_model=GenerateResp)
def generate(req: GenerateReq) -> GenerateResp:
    if req.doc_type == "package":
        return _generate_package(req)
    if req.doc_type == "keiyaku":
        return _generate_keiyaku(req)
    if req.doc_type == "juyojiko":
        return _generate_juyojiko(req)
    raise HTTPException(
        status_code=400,
        detail=f"未知の doc_type: {req.doc_type}（juyojiko / keiyaku / package）")


# ── /extract ──────────────────────────────────────────────────
_EXTRACT_SYS = (
    "あなたは日本の不動産の重要事項説明書（35条書面）を構造化する抽出エンジンです。"
    "与えられた重説（PDF/画像/テキスト）から、次の JSON 構造で読み取れる項目を返してください。"
    "読み取れない項目は null、配列は空配列に。推測で埋めないこと。前置き不要、JSON のみ。\n\n"
    "{\n"
    '  "bukken_type": "戸建|区分",\n'
    '  "torihiki_taiyo": "取引態様（例: 売買・媒介）",\n'
    '  "gyosha": {"menkyo_no":..,"menkyo_date":..,"shozai":..,"tel":..,"shomei":..,"daihyo":..},\n'
    '  "torikiishi": {"toroku_no":..,"shimei":..,"jimusho":..,"jimusho_shozai":..,"tel":..},\n'
    '  "urinushi": {"address":..,"name":..,"biko":..},\n'
    '  "fudosan": {"bukken_type":..,"jukyo_hyoji":..,'
    '"tochi":{"shozai":..,"chimoku":..,"chiseki_toki":..,"chiseki_jissoku":..},'
    '"tatemono":{"kaoku_bango":..,"shurui":..,"kozo":..,"yukamenseki":..,"chikujiki":..},'
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
    '"nisshido":..,"doro":..,"other_horei":[..]},\n'
    '  "setsubi": "飲用水・電気・ガス・排水の概要",\n'
    '  "kanri": {"kanrihi_getsugaku":整数,"shuzen_getsugaku":整数,"shuzen_tsumitate":整数,'
    '"kanrihi_taino":整数,"shuzen_taino":整数,"kanri_kumiai":..,"kanri_keitai":..,'
    '"kanri_itakusaki":..,"yoto_seigen":..,"pet_seigen":..},\n'
    '  "joken": {"baibai_daikin":整数,"shohizei":整数,"tetsuke":整数,'
    '"seisan_kisanbi":..,"iyakukin_wariai":整数,"tanpo_sekinin":..,"loan_tokuyaku":true/false},\n'
    '  "yonin_jiko": ["容認事項..."],\n'
    '  "tokuyaku": ["特約事項..."]\n'
    "}\n"
    "金額は円の整数（カンマ無し）。建蔽率・容積率は%の整数。"
)


_EXTRACT_SYS_KEIYAKU = (
    "あなたは日本の不動産売買契約書（FRK標準書式）を構造化する抽出エンジンです。"
    "与えられた契約書（PDF/画像/テキスト）から、次の JSON 構造で読み取れる項目を返してください。"
    "読み取れない項目は null、配列は空配列に。推測で埋めないこと。前置き不要、JSON のみ。\n\n"
    "{\n"
    '  "bukken_type": "戸建|区分",\n'
    '  "urinushi": {"address":..,"name":..},\n'
    '  "kainushi": {"address":..,"name":..},\n'
    '  "gyosha": {"shomei":..,"shozai":..,"tel":..,"daihyo":..},\n'
    '  "torikiishi": {"shimei":..,"toroku_no":..},\n'
    '  "fudosan": {"bukken_type":..,"jukyo_hyoji":..,'
    '"tochi":{"shozai":..,"chimoku":..,"chiseki_toki":..,"chiseki_jissoku":..},'
    '"tatemono":{"kaoku_bango":..,"shurui":..,"kozo":..,"yukamenseki":..,"chikujiki":..},'
    '"ittou_shozai":..,"senyuu":{"kaoku_bango":..,"yukamenseki":..},'
    '"shikichiken":[{"shozai":..,"chiban":..,"chiseki":..,"wariai":..}]},\n'
    '  "daikin": {"baibai_daikin":整数,"shohizei":整数,"tetsuke":整数,'
    '"uchikin1":整数,"uchikin1_date":..,"uchikin2":整数,"uchikin2_date":..,'
    '"zankin":整数,"zankin_date":..},\n'
    '  "hikiwatashi_date": "引渡し日",\n'
    '  "loan_tokuyaku": true/false,"loan_kingaku":整数,"loan_shonin_date":..,\n'
    '  "tokuyaku": ["特約事項..."],\n'
    '  "jokan": [{"jo":"第1条","midashi":"見出し","honbun":"本文"}]\n'
    "}\n"
    "金額は円の整数（カンマ無し）。約款(jokan)は条ごとに分けて、本文も読み取れる範囲で含める。"
)


def _build_content(req: ExtractReq) -> list[dict[str, Any]]:
    doc = "売買契約書" if req.doc_type == "keiyaku" else "重要事項説明書"
    content: list[dict[str, Any]] = [
        {"type": "text", "text": f"次の{doc}から項目を抽出してください。"}
    ]
    if req.file_base64:
        if req.mime == "application/pdf":
            content.append({"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf", "data": req.file_base64}})
        elif req.mime.startswith("image/"):
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": req.mime, "data": req.file_base64}})
        else:
            raise HTTPException(
                status_code=400,
                detail=f"未対応の mime: {req.mime}（application/pdf か image/* のみ）")
    if req.text:
        content.append({"type": "text", "text": req.text})
    return content


def _extract_with_claude(req: ExtractReq) -> dict[str, Any]:
    try:
        from anthropic import Anthropic
    except ImportError as e:  # pragma: no cover
        raise HTTPException(
            status_code=500, detail="anthropic SDK 未導入です（pip install anthropic）。") from e
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY 未設定です。")

    client = Anthropic(max_retries=4, timeout=180.0)
    system = _EXTRACT_SYS_KEIYAKU if req.doc_type == "keiyaku" else _EXTRACT_SYS
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": _build_content(req)}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        raw = raw[4:].strip() if raw.lstrip().startswith("json") else raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"抽出 JSON の解析に失敗: {raw[:200]}") from e


@app.post("/extract", response_model=ExtractResp)
def extract(req: ExtractReq) -> ExtractResp:
    if not (req.text and req.text.strip()) and not req.file_base64:
        raise HTTPException(
            status_code=400, detail="text または file_base64 のいずれかが必要です。")
    data = _extract_with_claude(req)
    return ExtractResp(extracted=_normalize_extracted(data))


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
    return data


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("BC_PORT", "8800")))
