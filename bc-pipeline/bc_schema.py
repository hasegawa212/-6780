"""用途地域・物件種別の正規化ユーティリティ.

重説（`juyojiko_schema` / `juyojiko_excel`）と抽出（`bc_service`）から共用する。
"""

from __future__ import annotations

# ── 用途地域の選択肢（都市計画法 第8条 / 建築基準法の重説様式）──────
# extracted.yoto は略称（例 "第1種中高層"）で来ることがあるため、
# 前方一致＋別名でこの正式名称に寄せる。最後の「指定なし」は様式 14 番。
YOTO_OPTIONS: list[str] = [
    "第1種低層住居専用地域",
    "第2種低層住居専用地域",
    "第1種中高層住居専用地域",
    "第2種中高層住居専用地域",
    "第1種住居地域",
    "第2種住居地域",
    "準住居地域",
    "田園住居地域",
    "近隣商業地域",
    "商業地域",
    "準工業地域",
    "工業地域",
    "工業専用地域",
    "用途地域の指定なし",
]

# よくある略記 → 正式名称（前方一致で拾えないものを補う）
YOTO_ALIASES: dict[str, str] = {
    "1低": "第1種低層住居専用地域",
    "2低": "第2種低層住居専用地域",
    "1中高": "第1種中高層住居専用地域",
    "2中高": "第2種中高層住居専用地域",
    "1住": "第1種住居地域",
    "2住": "第2種住居地域",
    "準住": "準住居地域",
    "近商": "近隣商業地域",
    "準工": "準工業地域",
    "工専": "工業専用地域",
}

# 物件種別の別名 → 正規キー
BUKKEN_ALIASES: dict[str, str] = {
    "戸建": "戸建",
    "戸建て": "戸建",
    "一戸建": "戸建",
    "一戸建て": "戸建",
    "土地建物": "戸建",
    "36-1": "戸建",
    "区分": "区分",
    "区分所有": "区分",
    "マンション": "区分",
    "37-1": "区分",
}


def resolve_bukken(bukken: str) -> str:
    """物件種別の表記ゆれを正規キー（戸建 / 区分）に寄せる."""
    key = (bukken or "").strip()
    if key in BUKKEN_ALIASES:
        return BUKKEN_ALIASES[key]
    raise KeyError(f"未知の物件種別: {bukken!r}（戸建 / 区分 のいずれか）")


def normalize_yoto(raw: str | None) -> str | None:
    """用途地域の略称を正式名称に寄せる（一致しなければそのまま返す）."""
    if not raw:
        return raw
    s = str(raw).strip()
    if s in YOTO_OPTIONS:
        return s
    if ("指定" in s and "なし" in s) or s in ("なし", "無", "指定なし"):
        return "用途地域の指定なし"
    for alias, full in YOTO_ALIASES.items():
        if s.startswith(alias):
            return full
    for opt in YOTO_OPTIONS:
        if opt.startswith(s) or s.startswith(opt):
            return opt
    return s
