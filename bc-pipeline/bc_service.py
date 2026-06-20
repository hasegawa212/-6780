"""BC 自動生成サービス（FastAPI）.

エンドポイント:
  GET  /health   … 死活監視
  POST /extract  … 物件資料テキスト → 構造化 JSON（Claude 抽出）
  POST /generate … 抽出 JSON ＋案件マスタ → BC(.xlsx) を base64 で返す

環境変数:
  ANTHROPIC_API_KEY  … Claude（/extract のみ必須）
  ANTHROPIC_BASE_URL … 社内 LiteLLM プロキシ等に向ける場合（任意）
  CLAUDE_MODEL       … 既定 claude-opus-4-8
  CLAUDE_MAX_TOKENS  … 既定 2000
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

import fill_engine
from bc_schema import YOTO_OPTIONS, resolve_bukken

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")
MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "2000"))

app = FastAPI(title="BC自動生成サービス", version="0.1.0")


# ── スキーマ ───────────────────────────────────────────────────
class Extracted(BaseModel):
    # 既知フィールド以外も保持できるようにする（テンプレ拡張に追従）
    model_config = ConfigDict(extra="allow")

    shozai: str | None = None      # 所在地
    kuiki: str | None = None       # 区域区分（市街化区域 等）
    yoto: str | None = None        # 用途地域
    nijuni_jo: bool | None = None  # 法22条区域
    kenpei: int | None = None      # 建蔽率(%)
    yoseki: int | None = None      # 容積率(%)


class DealMaster(BaseModel):
    model_config = ConfigDict(extra="allow")

    buyer_C: str | None = None             # 買主C
    bc_baibai_daikin: int | None = None    # BC売買代金(円)


class GenerateReq(BaseModel):
    bukken: str
    extracted: Extracted
    deal_master: DealMaster = DealMaster()
    filename: str | None = None


class GenerateResp(BaseModel):
    filename: str
    bukken: str
    fields_filled: int
    xlsx_base64: str


class ExtractReq(BaseModel):
    bukken: str | None = None
    # いずれか1つ以上。実運用では AB 側重説（スキャン PDF が多い）を file_base64 で渡す。
    text: str | None = None         # 物件資料・登記簿・チラシ等の生テキスト
    file_base64: str | None = None  # PDF / 画像（重説スキャン等）の base64
    mime: str = "application/pdf"   # file_base64 のメディアタイプ


class ExtractResp(BaseModel):
    extracted: dict[str, Any]


# ── /health ───────────────────────────────────────────────────
@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "model": MODEL, "bukken": ["戸建", "区分"]}


# ── /generate ─────────────────────────────────────────────────
@app.post("/generate", response_model=GenerateResp)
def generate(req: GenerateReq) -> GenerateResp:
    try:
        resolve_bukken(req.bukken)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    extracted = req.extracted.model_dump(exclude_none=True)
    deal_master = req.deal_master.model_dump(exclude_none=True)

    try:
        xlsx, filled = fill_engine.fill(req.bukken, extracted, deal_master)
    except Exception as e:  # noqa: BLE001 - クライアントへ理由を返す
        raise HTTPException(status_code=500, detail=f"差し込み失敗: {e}") from e

    filename = req.filename or fill_engine.default_filename(req.bukken, extracted)
    return GenerateResp(
        filename=filename,
        bukken=resolve_bukken(req.bukken),
        fields_filled=filled,
        xlsx_base64=base64.b64encode(xlsx).decode("ascii"),
    )


# ── /extract ──────────────────────────────────────────────────
_EXTRACT_SYS = (
    "あなたは日本の不動産資料から物件概要を構造化する抽出エンジンです。"
    "与えられたテキストから次のフィールドだけを JSON で返してください。"
    "値が読み取れない項目は null にし、推測で埋めないこと。前置き・説明は不要。\n"
    "フィールド:\n"
    "  shozai (string|null): 所在地（地番・住居表示）\n"
    "  kuiki (string|null): 区域区分（例: 市街化区域 / 市街化調整区域）\n"
    f"  yoto (string|null): 用途地域。次のいずれかに正規化: {', '.join(YOTO_OPTIONS)}\n"
    "  nijuni_jo (boolean|null): 法22条区域なら true\n"
    "  kenpei (integer|null): 建蔽率(%)\n"
    "  yoseki (integer|null): 容積率(%)\n"
)


def _build_content(req: ExtractReq) -> list[dict[str, Any]]:
    """Claude へ渡す content ブロックを組み立てる（テキスト / PDF / 画像）."""
    content: list[dict[str, Any]] = [
        {"type": "text", "text": "次の不動産資料から物件概要を抽出してください。"}
    ]
    if req.file_base64:
        if req.mime == "application/pdf":
            # 重説スキャン PDF はそのまま document ブロックで渡す（Claude が OCR+抽出）
            content.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": req.file_base64,
                },
            })
        elif req.mime.startswith("image/"):
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": req.mime,
                    "data": req.file_base64,
                },
            })
        else:
            raise HTTPException(
                status_code=400,
                detail=f"未対応の mime: {req.mime}（application/pdf か image/* のみ）",
            )
    if req.text:
        content.append({"type": "text", "text": req.text})
    return content


def _extract_with_claude(req: ExtractReq) -> dict[str, Any]:
    try:
        from anthropic import Anthropic
    except ImportError as e:  # pragma: no cover
        raise HTTPException(
            status_code=500,
            detail="anthropic SDK 未導入です（pip install anthropic）。",
        ) from e

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY 未設定です。")

    client = Anthropic(max_retries=4, timeout=180.0)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=_EXTRACT_SYS,
        messages=[{"role": "user", "content": _build_content(req)}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    raw = raw.strip()
    # ```json フェンスが付くことがあるので剥がす
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1].lstrip("json").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=502, detail=f"抽出 JSON の解析に失敗: {raw[:200]}"
        ) from e


@app.post("/extract", response_model=ExtractResp)
def extract(req: ExtractReq) -> ExtractResp:
    if not (req.text and req.text.strip()) and not req.file_base64:
        raise HTTPException(
            status_code=400, detail="text または file_base64 のいずれかが必要です。"
        )
    data = _extract_with_claude(req)
    return ExtractResp(extracted=data)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("BC_PORT", "8800")))
