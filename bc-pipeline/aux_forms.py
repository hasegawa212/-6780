"""付属書式ジェネレータ: 全宅連の付属書式(覚書・精算書等)へ当事者・物件・日付を差込む。
各様式のセル座標は blank テンプレの結合セル解析で確認済み(未照合は当てない)。
様式固有の金額・期間は案件依存のため手入力（差込しない＝安全側）。"""
from __future__ import annotations
import io
from typing import Any
from openpyxl import load_workbook
from openpyxl.utils import coordinate_to_tuple, get_column_letter


def _anchor(ws, coord: str) -> str:
    """結合セル内の座標を、書込可能な左上アンカーへ解決する。"""
    r, c = coordinate_to_tuple(coord)
    for m in ws.merged_cells.ranges:
        if m.min_row <= r <= m.max_row and m.min_col <= c <= m.max_col:
            return f"{get_column_letter(m.min_col)}{m.min_row}"
    return coord


# form_key -> {detect: A1/名称の一部, cells: {field: [座標...]}}
AUX_MAPS: dict[str, dict[str, Any]] = {
    "112-3": {  # 固定資産税等の清算に関する覚書③(通知到着後清算)
        "detect": "固定資産税等の清算に関する覚書",
        "sheet_index": 0,
        "cells": {
            "urinushi_name": ["E16", "Z59"],   # 冒頭＋署名欄
            "urinushi_addr": ["Z56"],
            "kainushi_name": ["R16", "Z66"],
            "kainushi_addr": ["Z63"],
            "bukken": ["B70"],                  # 末尾「不動産の表示」
        },
    },
    "116-3": {  # 売買契約書の内容を一部変更する覚書
        "detect": "一部変更する覚書",
        "sheet_index": 0,
        "cells": {
            "urinushi_name": ["E14", "Z53"],   # 冒頭「売主」名＋署名（売主）氏名
            "urinushi_addr": ["Z50"],
            "kainushi_name": ["Z60"],           # 署名（買主）氏名
            "kainushi_addr": ["Z57"],
        },  # 原契約日・変更前/変更後・物件表示は手入力
    },
    "311-3": {  # 本人確認書類(個人、確認用) 犯収法の取引時確認記録
        "detect": "本人確認",
        "sheet_index": 0,
        "cells": {
            "kainushi_name": ["K32"],   # 氏名(D32)
            "kainushi_addr": ["W36"],   # 住居の住所文字列枠(W36:AW37。〒/番地枠は別)
        },  # フリガナ・生年月日・職業・確認書類は手入力(差込しない)
    },
    "334": {  # 契約残金明細書(A3版)
        "detect": "契約残金",
        "sheet_index": 0,
        "cells": {
            "urinushi_name": ["H16"],   # 「売主」欄(E17)の名前枠 H16:Y18
            "kainushi_name": ["H19"],   # 「買主」欄(E20)の名前枠 H19:Y21
            "kainushi_addr": ["CG15"],  # 宛先(様)の住所枠 CG15:CV16
        },  # 金種内訳・金額・日付・連絡先は案件依存で手入力(差込しない)
    },
}


def detect_form(wb_bytes: bytes) -> str | None:
    wb = load_workbook(io.BytesIO(wb_bytes), read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            a1 = ws["A1"].value
            b2 = ws["B2"].value
            # タイトルがセルでなくシート名に入る様式もあるため ws.title も含める
            head = " ".join(str(x) for x in (ws.title, a1, b2) if x)
            for key, m in AUX_MAPS.items():
                if m["detect"] in head:
                    return key
    finally:
        wb.close()
    return None


def fill_aux(wb_bytes: bytes, form_key: str, data: dict[str, Any]) -> tuple[bytes, int]:
    """data のうち AUX_MAPS[form_key].cells にある項目だけを差込む。"""
    m = AUX_MAPS.get(form_key)
    if not m:
        raise KeyError(f"未対応の付属書式: {form_key}（対応: {list(AUX_MAPS)}）")
    wb = load_workbook(io.BytesIO(wb_bytes), data_only=False)
    ws = wb.worksheets[m.get("sheet_index", 0)]
    n = 0
    for field, coords in m["cells"].items():
        val = data.get(field)
        if val in (None, ""):
            continue
        for coord in coords:
            a = _anchor(ws, coord)
            cur = ws[a].value
            # 既にラベル等が入っている枠は上書きしない（ラベル破壊防止・安全側）
            if isinstance(cur, str) and cur.strip():
                continue
            ws[a] = val
            n += 1
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), n
