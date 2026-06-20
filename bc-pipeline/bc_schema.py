"""BC（物件概要書）テンプレートのスキーマ定義.

実物の Excel テンプレ（36-1 戸建 / 37-1 区分所有）はバイナリで配布されるが、
本パイプラインではこのスキーマ定義から白紙テンプレを *再生成* できるようにして、
ファイルが手元に無くても端から端まで動くようにしている。

- ``fill_engine.py``          … このスキーマに従って値を差し込む
- ``make_blank_templates.py`` … このスキーマから ``blank_36-1.xlsx`` 等を生成する

差し込み元のフィールド名（``extracted`` / ``deal_master``）は
``案件マスタ_スキーマ.md`` と一致させること。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── 用途地域の選択肢（都市計画法 第8条）──────────────────────────
# extracted.yoto は略称（例 "第1種中高層"）で来ることがあるため、
# fill_engine 側で前方一致＋別名でこの正式名称に寄せる。
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


@dataclass(frozen=True)
class Cell:
    """ラベルセルと値セルの組（ラベルは白紙テンプレ生成にだけ使う）."""

    key: str           # 差し込み元フィールド名（extracted / deal_master 共通名）
    label: str         # 帳票上の項目名
    label_cell: str    # 例 "B4"
    value_cell: str    # 例 "C4"
    suffix: str = ""   # 値の後ろに付ける単位（例 "%", " 円"）
    source: str = "extracted"  # "extracted" / "deal_master"


@dataclass(frozen=True)
class TemplateSpec:
    key: str            # "戸建" / "区分"
    template_file: str  # "blank_36-1.xlsx"
    sheet: str
    title: str
    cells: list[Cell]
    nijuni_jo_cell: str             # 法22条区域フラグの値セル
    yoto_checkbox_name: str         # チェックボックス群の論理名（36-1: checkbox_361）
    yoto_header_cell: str           # "用途地域" 見出しセル
    yoto_option_start_row: int      # 選択肢チェックの開始行
    yoto_option_col: str = "B"      # チェック記号を置く列
    yoto_label_col: str = "C"       # 選択肢名を置く列


def _common_cells() -> list[Cell]:
    """戸建・区分で共通の物件概要セル群."""
    return [
        Cell("shozai", "所在地", "B4", "C4", source="extracted"),
        Cell("kuiki", "区域区分", "B5", "C5", source="extracted"),
        Cell("yoto", "用途地域", "B6", "C6", source="extracted"),
        Cell("nijuni_jo", "法22条区域", "B7", "C7", source="extracted"),
        Cell("kenpei", "建蔽率", "B8", "C8", suffix="%", source="extracted"),
        Cell("yoseki", "容積率", "B9", "C9", suffix="%", source="extracted"),
        Cell("buyer_C", "買主C", "B11", "C11", source="deal_master"),
        Cell("bc_baibai_daikin", "BC売買代金", "B12", "C12", suffix=" 円",
             source="deal_master"),
    ]


# ── テンプレート一覧 ───────────────────────────────────────────
TEMPLATES: dict[str, TemplateSpec] = {
    "戸建": TemplateSpec(
        key="戸建",
        template_file="blank_36-1.xlsx",
        sheet="BC戸建",
        title="物件概要書（戸建 36-1）",
        cells=_common_cells(),
        nijuni_jo_cell="C7",
        yoto_checkbox_name="checkbox_361",
        yoto_header_cell="B14",
        yoto_option_start_row=15,
    ),
    "区分": TemplateSpec(
        key="区分",
        template_file="blank_37-1.xlsx",
        sheet="BC区分",
        title="物件概要書（区分所有 37-1）",
        cells=_common_cells(),
        nijuni_jo_cell="C7",
        # 36-1 の checkbox_361 に相当する区分版（残タスク対応）
        yoto_checkbox_name="checkbox_371",
        yoto_header_cell="B14",
        yoto_option_start_row=15,
    ),
}

# 物件種別の別名 → 正規キー
BUKKEN_ALIASES: dict[str, str] = {
    "戸建": "戸建",
    "戸建て": "戸建",
    "一戸建": "戸建",
    "一戸建て": "戸建",
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
    if "指定" in s and "なし" in s or s in ("なし", "無", "指定なし"):
        return "用途地域の指定なし"
    for alias, full in YOTO_ALIASES.items():
        if s.startswith(alias):
            return full
    for opt in YOTO_OPTIONS:
        # "第1種中高層" のような前方一致
        if opt.startswith(s) or s.startswith(opt):
            return opt
    return s
