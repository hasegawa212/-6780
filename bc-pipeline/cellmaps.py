"""本番ワークブックのセルマップ（テンプレ変種ごと）.

座標は同一テンプレの実例を差分比較して特定したもの（個人情報ではない）。
「間違いないように」のため、**確証の取れたセルのみ**を定義する。未確認のセルは
入れない（誤差し込みを避ける）。新しいテンプレ/シートは実例2通の差分（wb_diff.py）で
増やせる。

変種:
  36-1 … 土地建物（戸建）売主宅建業者用
  37-1 … 区分所有建物（敷地権）
  38-1 … 区分所有建物（非敷地権）
"""

from __future__ import annotations

from typing import Any

from keiyaku_schema import Keiyakusho

CONTRACT_SHEET = "不動産売買契約書"


def _g(obj: Any, *path: str) -> Any:
    for p in path:
        if obj is None:
            return None
        obj = getattr(obj, p, None) if not isinstance(obj, dict) else obj.get(p)
    return obj


def _build_keiyaku_36_1(bc: Keiyakusho) -> tuple[dict[str, Any], list[str]]:
    """36-1（土地建物）契約書シートの (差込値, 追加クリアセル) を返す。

    差分検証済みセル:
      当事者 E123(売主)/AB123(買主)、代金 AE45/内訳AE47・AE49・AE51/手付AE53/残代金AE59、
      土地 F11(所在)/AF11(地目)/AL11(地積)、建物 I23(所在)/AK23(家屋番号)/AE25(構造)/AR29(床面積)、
      特記 D31。
    """
    d = bc.daikin
    f = bc.fudosan
    tochi = _g(f, "tochi")
    tate = _g(f, "tatemono")
    values: dict[str, Any] = {
        "E123": _g(bc, "urinushi", "name"),
        "AB123": _g(bc, "kainushi", "name"),
        "AE45": _g(d, "baibai_daikin"),
        "AE47": _g(d, "tochi_kakaku"),
        "AE49": _g(d, "tatemono_kakaku"),
        "AE51": _g(d, "shohizei"),
        "AE53": _g(d, "tetsuke"),
        "AE59": _g(d, "zankin"),
        "F11": _g(tochi, "shozai"),
        "AF11": _g(tochi, "chimoku"),
        "AL11": _g(tochi, "chiseki_toki"),
        "I23": _g(tate, "shozai") or _g(tochi, "shozai"),
        "AK23": _g(tate, "kaoku_bango"),
        "AE25": _g(tate, "kozo"),
        "AR29": _g(tate, "yukamenseki"),
        "D31": "\n".join(bc.tokuyaku) if bc.tokuyaku else None,
    }
    # 旧案件の値が残らないようクリアする（差込しない地番・日付・備考の分割セル）
    clear_extra = ["X11", "AC11", "S59", "W59", "AA59",
                   "O71", "S71", "W71", "AH81", "AL81", "AP81", "B100"]
    return values, clear_extra


# 変種 → 契約書ビルダー
KEIYAKU_BUILDERS = {
    "36-1": _build_keiyaku_36_1,
}


def build_keiyaku(variant: str, bc: Keiyakusho) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """(sheet_values, sheet_clear) を返す。wb_fill.fill_workbook にそのまま渡せる。"""
    builder = KEIYAKU_BUILDERS.get(variant)
    if builder is None:
        raise KeyError(f"未対応のテンプレ変種: {variant}（対応: {list(KEIYAKU_BUILDERS)}）")
    values, clear_extra = builder(bc)
    clear = list(values.keys()) + clear_extra
    return {CONTRACT_SHEET: values}, {CONTRACT_SHEET: clear}
