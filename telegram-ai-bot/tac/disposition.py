"""通話結果ラベル（Disposition）＋ DNC ワンクリック登録。

架電の結果（成約/検討/不在/拒否 など）を架電記録に残す。結果が「拒否」のときは
自動で DNC（発信禁止）に登録し、以後の再発信を仕組みで防ぐ（特定商取引法の
再勧誘禁止への配慮）。add_dnc を明示すれば自動判定を上書きできる。

依存は calllog / dnc のみ（flask 非依存でテスト可能）。
"""

from __future__ import annotations

# 断り（この結果なら既定で DNC に登録する）
DECLINE = "拒否"


def record(to: str, result: str, add_dnc: bool | None = None) -> dict:
    """架電結果を記録する。

    to      : 相手の番号（E.164）
    result  : 結果ラベル（成約/検討/不在/拒否 等の任意文字列）
    add_dnc : None=結果が「拒否」なら自動でDNC登録。True/False で明示上書き。
    戻り値  : {ok, to, result, dnc_added}
    """
    from . import calllog, dnc

    to = (to or "").strip()
    if not to:
        return {"ok": False, "error": "発信先 to が空です"}
    result = (result or "").strip()

    should_add = (result == DECLINE) if add_dnc is None else bool(add_dnc)
    dnc_added = False
    if should_add:
        dnc.add(to)
        dnc_added = dnc.contains(to)

    calllog.append("outbound", to, "disposition", disposition=result, dnc_added=dnc_added)
    return {"ok": True, "to": to, "result": result, "dnc_added": dnc_added}
