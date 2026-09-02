"""登記事項証明書（登記情報提供サービスのテキストPDF）を物件マスタ欄へ自動転記するためのパーサ.

対象は「機械可読テキスト」の登記PDF（罫線＝箱文字の全部事項）。画像スキャンの登記には
非対応（呼び出し側で「テキストが取れない＝画像」を判定し、手入力へ誘導する想定）。

戸建（土地＋建物）を主対象とし、表題部から以下を抽出する:
  建物: 所在・家屋番号・種類・構造・床面積(延床)・築年月・不動産番号・所有者
  土地: 所在地番・地目・地積・不動産番号・所有者

返り値は Web UI の入力欄 id（mp_*）に直接対応する dict（値が取れた欄のみ）。
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["parse_touki_text", "looks_like_touki"]

_ERA = "明治|大正|昭和|平成|令和"


def _z2h(s: str) -> str:
    """全角英数記号を半角へ。全角スペースは半角スペースへ。"""
    if not s:
        return ""
    out = unicodedata.normalize("NFKC", s)
    return out


def _collapse(s: str) -> str:
    """内部の空白を全て除去（登記の字間スペースを詰める）。"""
    return re.sub(r"\s+", "", s or "")


def _cells(line: str) -> list[str]:
    """罫線（┃│）で列に分割し、各セルを前後trimして返す。"""
    parts = re.split(r"[┃│]", line)
    return [p.strip("　 \t") for p in parts]


def looks_like_touki(text: str) -> bool:
    """登記事項証明書らしいテキストかの簡易判定（字間スペースを詰めて照合）."""
    if not text:
        return False
    t = _collapse(text)
    hit = sum(k in t for k in ("表題部", "不動産番号", "権利部", "家屋番号", "地積", "床面積"))
    return hit >= 2


def _find_fudosan_bango(text: str) -> str | None:
    m = re.search(r"不動産番号[^0-9０-９]*([0-9０-９]{10,})", text)
    return _z2h(m.group(1)) if m else None


def _find_shozai(text: str) -> str | None:
    """「所　在」欄の値（字間スペースを詰め、ラベルが厳密に「所在」の行だけを対象）。

    「所在図番号」など他ラベルを誤検出しないよう、ラベルセルが完全一致「所在」のときだけ拾う。
    """
    for line in text.splitlines():
        cells = _cells(line)
        labels = [_collapse(_z2h(c)) for c in cells]
        for i, lab in enumerate(labels):
            if lab == "所在":
                for j in range(i + 1, len(cells)):
                    val = _collapse(_z2h(cells[j]))
                    if val:
                        return val
    return None


def _find_kaoku_bango(text: str) -> str | None:
    for line in text.splitlines():
        if "家屋番号" in line:
            cells = _cells(line)
            for i, c in enumerate(cells):
                if "家屋番号" in c and i + 1 < len(cells):
                    val = _collapse(_z2h(cells[i + 1]))
                    if val:
                        return val
    return None


def _find_chikujiki(text: str) -> str | None:
    """原因及びその日付の「新築」から築年月（元号＋年月）を取り出す。"""
    m = re.search(rf"({_ERA})\s*([0-9０-９]+)\s*年\s*([0-9０-９]+)\s*月[0-9０-９]*\s*日?\s*新築", text)
    if m:
        return f"{m.group(1)}{_z2h(m.group(2))}年{_z2h(m.group(3))}月"
    return None


_AREA_RE = r"([0-9０-９]+)\s*[：:．.]\s*([0-9０-９]+)"


def _tatemono_block(lines: list[str]) -> list[str]:
    """「①種類 ②構造 ③床面積」ヘッダ以降〜箱の下端（or 権利部）までを返す。"""
    start = None
    for i, ln in enumerate(lines):
        c = _collapse(ln)
        if "種類" in c and "構造" in c and "床面積" in c:
            start = i + 1
            break
    if start is None:
        return []
    block: list[str] = []
    for ln in lines[start:]:
        if "┗" in ln or "権利部" in _collapse(ln):
            break
        block.append(ln)
    return block


def _parse_tatemono(text: str) -> dict[str, str]:
    """建物の表題部から 種類・構造・床面積(延床) を取り出す。

    表題部ブロック内で小数（数字：数字）を持つのは床面積のみなので、全階を合算して延床とする。
    """
    out: dict[str, str] = {}
    block = _tatemono_block(text.splitlines())
    if not block:
        return out
    # 床面積：ブロック内の全「数字：数字」を合算（＝延床）。
    floors: list[float] = []
    for ln in block:
        for m in re.finditer(_AREA_RE, ln):
            floors.append(float(f"{_z2h(m.group(1))}.{_z2h(m.group(2))}"))
    if floors:
        out["mp_menseki"] = f"{round(sum(floors), 2):g}㎡"
    # 種類・構造：最初に床面積を含むデータ行から（面積セルの手前2列）。
    for ln in block:
        if not re.search(_AREA_RE, ln):
            continue
        ne = [c for c in _cells(ln) if c]
        area_i = next((j for j, c in enumerate(ne) if re.search(_AREA_RE, c)), None)
        if area_i is None or area_i < 1:
            continue
        out["mp_shurui"] = _collapse(_z2h(ne[0]))
        if area_i >= 2:
            out["mp_kozo"] = _z2h(ne[1]).strip()
        break
    return out


def _parse_tochi(text: str) -> dict[str, str]:
    """土地の表題部から 地目・地積 を取り出す（地番は所在に含めて扱う）。"""
    out: dict[str, str] = {}
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ("地" in ln and "番" in ln) and ("地" in ln and "目" in ln) and ("地" in ln and "積" in ln):
            start = i + 1
            break
    if start is None:
        return out
    for ln in lines[start:]:
        if "┗" in ln or "権　利　部" in ln or "権利部" in ln:
            break
        cells = [c for c in _cells(ln) if c != ""]
        if len(cells) < 3:
            continue
        # [地番, 地目, 地積, 原因] を想定。地積＝数字（：小数）を含むセル。
        chimoku = None
        chiseki = None
        for c in cells:
            if chimoku is None and re.search(r"(宅地|田|畑|山林|雑種地|原野|公衆用道路|保安林|ため池|墓地)", c):
                chimoku = _collapse(_z2h(c))
            if chiseki is None:
                m = re.search(r"([0-9０-９]+)\s*[：:．.]\s*([0-9０-９]+)", c)
                if m and "階" not in c:
                    chiseki = f"{_z2h(m.group(1))}.{_z2h(m.group(2))}㎡"
        if chimoku:
            out["mp_chimoku"] = chimoku
        if chiseki:
            out["mp_chiseki"] = chiseki
        if chimoku or chiseki:
            break
    return out


def _find_owner(text: str) -> dict[str, str]:
    """甲区の最新「所有者」の氏名・住所（ベストエフォート）。"""
    out: dict[str, str] = {}
    lines = text.splitlines()
    # セルが「所有者　…」で始まる行のみ（脚注の「（所有者）」等を除外）。
    owner_line_idx = []
    for i, ln in enumerate(lines):
        for c in _cells(ln):
            cs = c.lstrip("　 ")
            if re.match(r"所有者[　\s]", cs):
                owner_line_idx.append(i)
                break
    if not owner_line_idx:
        return out
    idx = owner_line_idx[-1]  # 最新の所有権登記
    # 住所＝「所有者」以降の同一行テキスト。
    m = re.search(r"所有者[\s　]*([^┃│]+)", lines[idx])
    if m:
        addr = _collapse(_z2h(m.group(1)))
        if addr:
            out["mp_touki_jusho"] = addr
    # 氏名＝直後の行の「最終“非空”列」（字間スペースを詰める）。移記/登記等のメタ行はスキップ。
    for ln in lines[idx + 1: idx + 4]:
        ne = [c for c in _cells(ln) if _collapse(c)]
        cand = _collapse(_z2h(ne[-1])) if ne else ""
        if not cand:
            continue
        if re.search(r"(移記|登記|番号|年|月|日|号|共同担保|順位|昭和|平成|令和)", cand):
            continue
        if re.fullmatch(r"[一-龥ぁ-んァ-ヶー々]+", cand):
            out["mp_touki"] = cand
            break
    return out


def parse_touki_text(text: str) -> dict:
    """登記テキスト → {kind, fill(mp_*), notes}. fill は取れた欄のみ。"""
    notes: list[str] = []
    is_tatemono = ("主である建物" in text) or ("建物の表示" in text) or ("家屋番号" in text)
    is_tochi = ("土地の表示" in text) or ("地　積" in text and not is_tatemono)
    kind = "建物" if is_tatemono else ("土地" if is_tochi else "不明")

    fill: dict[str, str] = {}
    shozai = _find_shozai(text)
    if shozai:
        fill["mp_shozai"] = shozai
    fb = _find_fudosan_bango(text)

    if is_tatemono:
        kaoku = _find_kaoku_bango(text)
        if kaoku:
            fill["mp_kaoku"] = kaoku
        fill.update(_parse_tatemono(text))
        chiku = _find_chikujiki(text)
        if chiku:
            fill["mp_chiku"] = chiku
    if is_tochi:
        fill.update(_parse_tochi(text))

    owner = _find_owner(text)
    # 氏名のみUI欄へ（住所欄はUIに無いので参考情報）。
    if owner.get("mp_touki"):
        fill["mp_touki"] = owner["mp_touki"]

    if kind == "不明":
        notes.append("建物／土地の判別ができませんでした。手入力で補ってください。")
    if not fill:
        notes.append("項目を読み取れませんでした。様式が想定外の可能性があります。")

    return {"kind": kind, "fill": fill, "fudosan_bango": fb, "notes": notes}
