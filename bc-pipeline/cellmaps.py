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

import re
from typing import Any

from bc_schema import YOTO_OPTIONS, normalize_yoto
from keiyaku_schema import Keiyakusho
from juyojiko_schema import Juyojiko

CONTRACT_SHEET = "不動産売買契約書"
JUYOJIKO_SHEET = "重要事項説明書"


def _split_wareki(date_str: str | None) -> tuple[int, int, int] | None:
    """日付文字列を (令和年, 月, 日) に分解する。読めなければ None。

    対応: "令和7年1月1日" / "2025年4月10日" / "2025-04-10" / "2025/4/10"。
    令和 = 西暦 − 2018（令和元年=2019）。
    """
    if not date_str:
        return None
    s = str(date_str).strip()
    m = re.search(r"令和\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", s)
    if m:
        return int(m[1]), int(m[2]), int(m[3])
    m = re.search(r"(\d{4})\s*年\s*(\d+)\s*月\s*(\d+)\s*日", s)
    if m:
        return int(m[1]) - 2018, int(m[2]), int(m[3])
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        return int(m[1]) - 2018, int(m[2]), int(m[3])
    return None


def _date_cells(date_str: str | None, y: str, mo: str, d: str) -> dict[str, int]:
    """日付を 令和年/月/日 の3セルに分けた {coord: 数値} を返す（読めなければ空）。"""
    parsed = _split_wareki(date_str)
    if not parsed:
        return {}
    return {y: parsed[0], mo: parsed[1], d: parsed[2]}


def _split_chiban(shozai: str | None) -> tuple[str, str | None, str | None]:
    """所在地を (所在の前置き, 番, 番地) に分解する。

    例 "○○市○○町12番5" → ("○○市○○町", "12", "5")。
    末尾に「N番M」「N番地M」「N番」が無ければ (全体, None, None)。
    """
    if not shozai:
        return "", None, None
    s = str(shozai).strip()
    m = re.search(r"(\d+)\s*番(?:地)?\s*(\d+)?\s*$", s)
    if not m:
        return s, None, None
    return s[:m.start()].strip(), m.group(1), m.group(2)


def _chiban_cells(shozai: str | None, prefix: str, ban: str, banchi: str) -> dict[str, Any]:
    """土地の所在を 所在/番/番地 の3セルに分けた {coord: 値} を返す。"""
    if not shozai:
        return {}
    pre, b, bc = _split_chiban(shozai)
    out: dict[str, Any] = {prefix: pre}
    if b:
        out[ban] = b
    if bc:
        out[banchi] = bc
    return out

ON, OFF = "■", "□"

# 区域区分のチェックセル（変種別）
KUIKI_MARKS = {
    "36-1": {"市街化区域": "T331", "市街化調整区域": "AA331",
             "区域区分のされていない区域": "AJ331"},
    "区分": {"市街化区域": "T335", "市街化調整区域": "AA335",
             "区域区分のされていない区域": "AJ335"},
}
# 用途地域14選択肢のチェックセル（YOTO_OPTIONS の順）。変種別。
YOTO_MARKS = {
    "36-1": ["C356", "C358", "C360", "C362", "C364", "R356", "R358",
             "R360", "R362", "R364", "AG356", "AG358", "AG360", "AG362"],
    "区分": ["C360", "C362", "C364", "C366", "C368", "R360", "R362",
             "R364", "R366", "R368", "AG360", "AG362", "AG364", "AG366"],
}
# 防火関係（相互排他）。変種別。
BOKA_MARKS = {
    "36-1": {"防火地域": "C368", "準防火地域": "C370", "新たな防火規制区域": "C372"},
    "区分": {"防火地域": "C372", "準防火地域": "C374", "新たな防火規制区域": "C376"},
}
# 建築基準法第22条区域（独立チェック）。変種別。
NIJUNI_MARK = {"36-1": "C374", "区分": "C378"}
# 高度地区（独立チェック）。変種別。
KODO_MARK = {"36-1": "C376", "区分": "C380"}


def _norm_boka(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if "新た" in s:
        return "新たな防火規制区域"
    if "準防火" in s:
        return "準防火地域"
    if "防火" in s:
        return "防火地域"
    return None


def _norm_kuiki(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if "調整" in s:
        return "市街化調整区域"
    if "されていない" in s or "非線引" in s or "未線引" in s:
        return "区域区分のされていない区域"
    if "市街化区域" in s:
        return "市街化区域"
    return s


def _checkbox(option_to_coord: dict[str, str], selected: str | None) -> dict[str, str]:
    """選択肢→セルの対応から {coord: ■/□} を返す。selected が無ければ空（テンプレ非改変）。"""
    if not selected:
        return {}
    return {coord: (ON if opt == selected else OFF)
            for opt, coord in option_to_coord.items()}


def _juyojiko_checkboxes(variant_key: str, h: Any) -> dict[str, str]:
    """区域区分・用途地域・地域地区のチェック差込値をまとめて返す。"""
    out: dict[str, str] = {}
    out.update(_checkbox(KUIKI_MARKS[variant_key], _norm_kuiki(_g(h, "kuiki_kubun"))))
    yoto_map = dict(zip(YOTO_OPTIONS, YOTO_MARKS[variant_key]))
    out.update(_checkbox(yoto_map, normalize_yoto(_g(h, "yoto"))))
    # 防火関係（相互排他）
    out.update(_checkbox(BOKA_MARKS[variant_key], _norm_boka(_g(h, "boka"))))
    # 建築基準法第22条区域（独立。True/False が分かるときだけ）
    nijuni = _g(h, "nijuni_jo")
    if nijuni is not None:
        out[NIJUNI_MARK[variant_key]] = ON if nijuni else OFF
    # 高度地区（独立。値があるときだけ ■）
    if _g(h, "kodo_chiku"):
        out[KODO_MARK[variant_key]] = ON
    return out


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
    # 土地所在の分割差込（所在 F11 / 番 X11 / 番地 AC11）
    values.update(_chiban_cells(_g(tochi, "shozai"), "F11", "X11", "AC11"))
    # 日付の分割差込（令和年/月/日）。残代金支払日 S59・融資承認取得期日 O71（実例検証済み）
    values.update(_date_cells(_g(d, "zankin_date"), "S59", "W59", "AA59"))
    values.update(_date_cells(_g(bc, "loan_shonin_date"), "O71", "S71", "W71"))
    # 旧案件の値が残らないようクリアする（差込しない地番・日付・備考の分割セル）
    clear_extra = ["X11", "AC11", "S59", "W59", "AA59",
                   "O71", "S71", "W71", "AH81", "AL81", "AP81", "B100"]
    return values, clear_extra


def _build_keiyaku_kubun(bc: Keiyakusho) -> tuple[dict[str, Any], list[str]]:
    """区分（37-1=敷地権 / 38-1=非敷地権）契約書シートの (差込値, 追加クリアセル)。

    37-1・38-1 は契約書シートのレイアウトが同一（実例で確認）。
    実例検証済みセル:
      当事者 E128(売主)/AB128(買主)、
      一棟 I11(所在)/I13(名称)/I15(構造)/AO15(延床)、
      専有 G19(家屋番号)/AA19(建物名称)/AQ19(種類)/G21(構造)/AP21(床面積)、
      敷地権 D29(所在)/R29(地番)/Z29(地目)/AF29(地積)/AL29(種類)/AR29(割合)、
      代金 AE54(売買代金)/AE58(手付)/AE64(残代金)/AE66(引渡日)。
    """
    d = bc.daikin
    f = bc.fudosan
    se = _g(f, "senyuu")
    sk = (f.shikichiken[0] if (f and f.shikichiken) else None)
    values: dict[str, Any] = {
        "E128": _g(bc, "urinushi", "name"),
        "AB128": _g(bc, "kainushi", "name"),
        "AE54": _g(d, "baibai_daikin"),
        "AE58": _g(d, "tetsuke"),
        "AE64": _g(d, "zankin"),
        "AE66": _g(bc, "hikiwatashi_date"),
        "I11": _g(f, "ittou_shozai"),
        "I13": _g(f, "ittou_meisho"),
        "I15": _g(f, "ittou_kozo"),
        "AO15": _g(f, "ittou_enshoumenseki"),
        "G19": _g(se, "kaoku_bango"),
        "AA19": _g(se, "meisho"),
        "AQ19": _g(se, "shurui"),
        "G21": _g(se, "kozo"),
        "AP21": _g(se, "yukamenseki"),
        "D29": _g(sk, "shozai"),
        "R29": _g(sk, "chiban"),
        "Z29": _g(sk, "chimoku"),
        "AF29": _g(sk, "chiseki"),
        "AL29": _g(sk, "shikichiken_shurui"),
        "AR29": _g(sk, "wariai"),
    }
    # 旧案件の金額（消費税・内金）をクリア
    clear_extra = ["AE56", "AE60", "AE62"]
    return values, clear_extra


# 変種 → 契約書ビルダー
KEIYAKU_BUILDERS = {
    "36-1": _build_keiyaku_36_1,
    "37-1": _build_keiyaku_kubun,
    "38-1": _build_keiyaku_kubun,
}


def build_keiyaku(variant: str, bc: Keiyakusho) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """(sheet_values, sheet_clear) を返す。wb_fill.fill_workbook にそのまま渡せる。"""
    builder = KEIYAKU_BUILDERS.get(variant)
    if builder is None:
        raise KeyError(f"未対応のテンプレ変種: {variant}（対応: {list(KEIYAKU_BUILDERS)}）")
    values, clear_extra = builder(bc)
    clear = list(values.keys()) + clear_extra
    return {CONTRACT_SHEET: values}, {CONTRACT_SHEET: clear}


def _build_juyojiko_36_1(bc: Juyojiko) -> tuple[dict[str, Any], list[str]]:
    """36-1（土地建物）重説シートの (差込値, 追加クリアセル) を返す。

    差分・実例検証済みセル:
      売主 F261(住所)/F263(氏名)/F265(備考)、買主 F7、
      土地 D194(所在)/AF194(地目)/AL194(地積)、
      建物 G236(所在)/AK236(家屋番号)/G238(住居表示)/AC240(構造)/AR244(床面積)、
      指定建蔽率 Q384、指定容積率 Q398。
    """
    f = bc.fudosan
    tochi = _g(f, "tochi")
    tate = _g(f, "tatemono")
    h = bc.horei
    values: dict[str, Any] = {
        "F7": _g(bc, "kainushi", "name"),
        "F261": _g(bc, "urinushi", "address"),
        "F263": _g(bc, "urinushi", "name"),
        "F265": "\n".join(bc.tokuyaku) if bc.tokuyaku else None,
        "AF194": _g(tochi, "chimoku"),
        "AL194": _g(tochi, "chiseki_toki"),
        "G236": _g(tate, "shozai") or _g(tochi, "shozai"),
        "AK236": _g(tate, "kaoku_bango"),
        "G238": _g(f, "jukyo_hyoji"),
        "AC240": _g(tate, "kozo"),
        "AR244": _g(tate, "yukamenseki"),
        "Q384": _g(h, "kenpei"),
        "Q398": _g(h, "yoseki"),
    }
    # 土地所在の分割差込（所在 D194 / 番 X194 / 番地 AC194）
    values.update(_chiban_cells(_g(tochi, "shozai"), "D194", "X194", "AC194"))
    # 宅建業者・取引士欄（BC側媒介。案件マスタ由来。検証済みセル）
    g = bc.gyosha
    t = bc.torikiishi
    values["H23"] = _g(g, "shozai")          # 業者 主たる事務所の所在地
    values["AF31"] = _g(g, "daihyo")         # 業者 代表者氏名
    values["H35"] = _g(t, "shimei")          # 取引士 氏名
    values["H39"] = _g(t, "jimusho_shozai")  # 取引士 事務所所在地
    values["R406"] = _g(h, "shikichi_saitei")  # 敷地面積の最低限度
    values.update(_juyojiko_checkboxes("36-1", h))  # 区域区分・用途地域の■/□
    # 旧案件の値が残らないようクリア（差込しない地番・床面積の分割セル）
    clear_extra = ["X194", "AC194", "P242", "X242"]
    return values, clear_extra


def _build_juyojiko_kubun(bc: Juyojiko) -> tuple[dict[str, Any], list[str]]:
    """区分（37-1 / 38-1）重説シートの (差込値, 追加クリアセル)。

    37-1・38-1 は重説シートのレイアウトが同一（実例で確認）。
    実例検証済みセル:
      売主 F265(住所)/F267(氏名)、買主 F7、
      一棟 I194(名称)/L201(所在)/L205(延床)、専有 L207(家屋番号)/AL207(建物名称)、
      指定建蔽率 Q388、指定容積率 Q402、
      修繕積立金 月額 L864 / 積立累計 U868 / 滞納 V872、管理費 月額 L884 / 滞納 V888、
      売買代金 H1116、手付金 V1129。
    """
    f = bc.fudosan
    se = _g(f, "senyuu")
    h = bc.horei
    k = bc.kanri
    j = bc.joken
    values: dict[str, Any] = {
        "F7": _g(bc, "kainushi", "name"),
        "F265": _g(bc, "urinushi", "address"),
        "F267": _g(bc, "urinushi", "name"),
        "I194": _g(f, "ittou_meisho"),
        "L201": _g(f, "ittou_shozai"),
        "L205": _g(f, "ittou_enshoumenseki"),
        "L207": _g(se, "kaoku_bango"),
        "AL207": _g(se, "meisho"),
        "Q388": _g(h, "kenpei"),
        "Q402": _g(h, "yoseki"),
        "L864": _g(k, "shuzen_getsugaku"),
        "U868": _g(k, "shuzen_tsumitate"),
        "V872": _g(k, "shuzen_taino"),
        "L884": _g(k, "kanrihi_getsugaku"),
        "V888": _g(k, "kanrihi_taino"),
        "H1116": _g(j, "baibai_daikin"),
        "V1129": _g(j, "tetsuke"),
    }
    values.update(_juyojiko_checkboxes("区分", h))  # 区域区分・用途地域の■/□
    return values, []


# 変種 → 重説ビルダー
JUYOJIKO_BUILDERS = {
    "36-1": _build_juyojiko_36_1,
    "37-1": _build_juyojiko_kubun,
    "38-1": _build_juyojiko_kubun,
}


def build_juyojiko(variant: str, bc: Juyojiko) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """(sheet_values, sheet_clear) を返す。wb_fill.fill_workbook にそのまま渡せる。"""
    builder = JUYOJIKO_BUILDERS.get(variant)
    if builder is None:
        raise KeyError(f"未対応のテンプレ変種: {variant}（対応: {list(JUYOJIKO_BUILDERS)}）")
    values, clear_extra = builder(bc)
    clear = list(values.keys()) + clear_extra
    return {JUYOJIKO_SHEET: values}, {JUYOJIKO_SHEET: clear}
