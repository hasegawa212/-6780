"""BC書類（重説・契約書）の発行前チェック（「ミスない契約書作成」の要）.

生成した BC データを発行前に決定論的に検査し、記入漏れ・不整合・御社標準からの
逸脱を **error / warning** として列挙する。openpyxl 等に依存しない純データ検査。

- error … そのまま発行すると書類として成立しない（買主未設定・代金未設定 等）
- warning … 発行は可能だが要確認（御社標準と違う・内訳が総額と合わない 等）

使い方:
    issues = validate_bc(juyojiko=bc_j, keiyaku=bc_k)
    errors = [i for i in issues if i["level"] == "error"]
"""

from __future__ import annotations

from typing import Any

import house_style

SELLER_B = house_style.SELLER_B_MASTER["shomei"]
STD_IYAKUKIN = house_style.KEIYAKU_DEFAULTS["iyakukin_wariai"]


def _g(obj: Any, *path: str) -> Any:
    for p in path:
        if obj is None:
            return None
        obj = getattr(obj, p, None) if not isinstance(obj, dict) else obj.get(p)
    return obj


def _issue(level: str, doc: str, field: str, msg: str) -> dict[str, str]:
    return {"level": level, "doc": doc, "field": field, "message": msg}


def _check_party_price(doc: str, urinushi: Any, kainushi: Any,
                       daikin: Any) -> list[dict[str, str]]:
    """当事者・代金まわりの共通チェック（重説/契約書で共用）。"""
    out: list[dict[str, str]] = []
    uname = _g(urinushi, "name")
    if not uname:
        out.append(_issue("error", doc, "売主", "売主（B）が未設定です。"))
    elif SELLER_B not in str(uname):
        out.append(_issue("warning", doc, "売主",
                          f"売主が御社（{SELLER_B}）ではありません: {uname}"))
    if not _g(kainushi, "name"):
        out.append(_issue("error", doc, "買主C", "買主C（最終買主）が未設定です。"))

    daikin_v = _g(daikin, "baibai_daikin")
    if not daikin_v:
        out.append(_issue("error", doc, "売買代金", "売買代金が未設定です。"))
    else:
        tochi = _g(daikin, "tochi_kakaku")
        tate = _g(daikin, "tatemono_kakaku")
        # 内訳（土地＋建物）が総額と一致するか
        if tochi is not None and tate is not None and tochi + tate != daikin_v:
            out.append(_issue("warning", doc, "代金内訳",
                              f"土地{tochi:,}＋建物{tate:,}＝{tochi + tate:,} が"
                              f"売買代金{daikin_v:,}と一致しません。"))
        # 消費税の整合（建物価格×10/110 ≒ 消費税）
        shohizei = _g(daikin, "shohizei")
        if tate and shohizei:
            expect = round(tate * 10 / 110)
            if abs(expect - shohizei) > max(2, tate * 0.001):
                out.append(_issue("warning", doc, "消費税",
                                  f"消費税{shohizei:,}が建物価格{tate:,}の税相当"
                                  f"（約{expect:,}）と乖離しています。"))
        tetsuke = _g(daikin, "tetsuke")
        if tetsuke is not None and tetsuke > daikin_v:
            out.append(_issue("error", doc, "手付金",
                              f"手付金{tetsuke:,}が売買代金{daikin_v:,}を超えています。"))
    return out


def validate_juyojiko(bc: Any) -> list[dict[str, str]]:
    """BC重説の発行前チェック。"""
    out = _check_party_price("重説", _g(bc, "urinushi"), _g(bc, "kainushi"),
                             _g(bc, "joken"))
    # 三為特約が入っているか（御社BCの必須）
    toku = "\n".join(_g(bc, "tokuyaku") or [])
    if "四者間取引の特約" not in toku and "他人物売買" not in toku:
        out.append(_issue("warning", "重説", "特約",
                          "三為特約（四者間取引の特約）が見当たりません。"))
    # 物件の表示
    if not _g(bc, "fudosan", "tochi", "shozai") and not _g(bc, "fudosan", "tatemono", "shozai"):
        out.append(_issue("error", "重説", "物件", "不動産の所在が未設定です。"))
    return out


def validate_keiyaku(bc: Any) -> list[dict[str, str]]:
    """BC契約書の発行前チェック。"""
    d = _g(bc, "daikin")
    out = _check_party_price("契約書", _g(bc, "urinushi"), _g(bc, "kainushi"), d)
    # 違約金は御社標準＝20%
    iw = _g(d, "iyakukin_wariai")
    if iw is None:
        out.append(_issue("error", "契約書", "違約金", "違約金（%）が未設定です。"))
    elif iw != STD_IYAKUKIN:
        out.append(_issue("warning", "契約書", "違約金",
                          f"違約金が{iw}%です（御社標準は{STD_IYAKUKIN}%）。"))
    # 残代金・引渡日
    if _g(d, "zankin") is None:
        out.append(_issue("warning", "契約書", "残代金", "残代金が未設定です。"))
    if not _g(bc, "hikiwatashi_date") and not _g(d, "zankin_date"):
        out.append(_issue("warning", "契約書", "引渡日", "引渡日／残代金支払日が未設定です。"))
    if not _g(bc, "seisan_kisanbi"):
        out.append(_issue("warning", "契約書", "公租公課",
                          "公租公課の清算起算日が未設定です（御社標準は1月1日）。"))
    return out


def validate_bc(juyojiko: Any = None, keiyaku: Any = None) -> list[dict[str, str]]:
    """重説・契約書をまとめて検査し、両書類間の整合も確認する。"""
    out: list[dict[str, str]] = []
    if juyojiko is not None:
        out += validate_juyojiko(juyojiko)
    if keiyaku is not None:
        out += validate_keiyaku(keiyaku)
    # 重説と契約書の突き合わせ（同一案件なら一致すべき）
    if juyojiko is not None and keiyaku is not None:
        jc = _g(juyojiko, "kainushi", "name")
        kc = _g(keiyaku, "kainushi", "name")
        if jc and kc and jc != kc:
            out.append(_issue("error", "整合", "買主C",
                              f"重説の買主C「{jc}」と契約書「{kc}」が不一致です。"))
        jd = _g(juyojiko, "joken", "baibai_daikin")
        kd = _g(keiyaku, "daikin", "baibai_daikin")
        if jd and kd and jd != kd:
            out.append(_issue("error", "整合", "売買代金",
                              f"重説の売買代金{jd:,}と契約書{kd:,}が不一致です。"))
    return out


def summarize(issues: list[dict[str, str]]) -> str:
    """人が読めるサマリ（Slack/アプリ表示用）。"""
    if not issues:
        return "✅ チェックOK：記入漏れ・不整合は見つかりませんでした。"
    errs = [i for i in issues if i["level"] == "error"]
    warns = [i for i in issues if i["level"] == "warning"]
    lines = []
    if errs:
        lines.append(f"🛑 要修正 {len(errs)}件")
        lines += [f"  ・[{i['doc']}/{i['field']}] {i['message']}" for i in errs]
    if warns:
        lines.append(f"⚠️ 要確認 {len(warns)}件")
        lines += [f"  ・[{i['doc']}/{i['field']}] {i['message']}" for i in warns]
    return "\n".join(lines)
