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

import juyojiko_excel
from bc_schema import YOTO_OPTIONS, normalize_yoto, resolve_bukken
from bc_transform import transform_ab_to_bc
from juyojiko_schema import (
    FudosanHyoji,
    HoreiSeigen,
    Juyojiko,
    TatemonoHyoji,
    TochiHyoji,
    TorihikiJoken,
)

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")
MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "4000"))

app = FastAPI(title="BC自動生成サービス", version="0.2.0")


# ── リクエスト/レスポンス ─────────────────────────────────────
class GenerateReq(BaseModel):
    model_config = ConfigDict(extra="allow")

    # 新方式: AB 重説の構造化 JSON（/extract の出力）
    ab: dict[str, Any] | None = None
    # 旧方式（手順書 curl 互換）: 最小フィールド
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
    bukken: str | None = None
    text: str | None = None
    file_base64: str | None = None
    mime: str = "application/pdf"


class ExtractResp(BaseModel):
    extracted: dict[str, Any]


# ── /health ───────────────────────────────────────────────────
@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "model": MODEL, "bukken": ["戸建", "区分"], "version": "0.2.0"}


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


def _filename(bukken: str, j: Juyojiko, override: str | None) -> str:
    if override:
        return override
    shozai = (j.fudosan.jukyo_hyoji if j.fudosan else None) or \
        (j.fudosan.ittou_shozai if j.fudosan else None) or "物件"
    safe = "".join(c for c in str(shozai) if c not in r'\/:*?"<>|').strip()
    return f"BC重説_{bukken}_{safe[:40]}.xlsx"


@app.post("/generate", response_model=GenerateResp)
def generate(req: GenerateReq) -> GenerateResp:
    # AB 重説の取得（新方式 ab / 旧方式 extracted）
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

    # AB→BC 変換（当事者・代金を差し替え、物件事実は引き継ぐ）
    bc = transform_ab_to_bc(ab, req.deal_master)
    bukken = bc.bukken_type or (bc.fudosan.bukken_type if bc.fudosan else None) or "区分"

    try:
        xlsx = juyojiko_excel.render(bc)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"重説生成に失敗: {e}") from e

    return GenerateResp(
        filename=_filename(bukken, bc, req.filename),
        bukken=bukken,
        xlsx_base64=base64.b64encode(xlsx).decode("ascii"),
    )


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


def _build_content(req: ExtractReq) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {"type": "text", "text": "次の重要事項説明書から項目を抽出してください。"}
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
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=_EXTRACT_SYS,
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
    return ExtractResp(extracted=_extract_with_claude(req))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("BC_PORT", "8800")))
