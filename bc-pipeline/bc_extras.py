"""BC実務データ: 印紙税額の自動判定・特約文例集。既存の重説/契約書生成には非干渉の追加機能。"""
from __future__ import annotations
import json, os
from typing import Any

# 不動産の譲渡に関する契約書(売買契約書) 軽減税率 ～令和9年3月31日
# 出典: 2026年版不動産手帳付録 印紙税額一覧表(令和7年5月1日現在)
INSHI_BRACKETS = [
    (10_000, 0), (500_000, 200), (1_000_000, 500), (5_000_000, 1_000),
    (10_000_000, 5_000), (50_000_000, 10_000), (100_000_000, 30_000),
    (500_000_000, 60_000), (1_000_000_000, 160_000), (5_000_000_000, 320_000),
]
INSHI_OVER = 480_000  # 50億円超
INSHI_NO_AMOUNT = 200  # 金額の記載なし


def inshi_zei(amount: int | float | None) -> dict[str, Any]:
    """売買代金(円)から印紙税額(軽減後・1通)を返す。"""
    if amount is None or amount == "":
        return {"amount": None, "inshi": INSHI_NO_AMOUNT,
                "note": "金額の記載なし", "reduced": True}
    a = int(float(amount))
    tax = INSHI_OVER
    for upto, t in INSHI_BRACKETS:
        if a <= upto:
            tax = t
            break
    return {"amount": a, "inshi": tax, "reduced": True,
            "note": "軽減税率(令和9年3月31日まで)。不動産売買契約書1通あたり",
            "source": "2026年版不動産手帳付録 印紙税額一覧表(令和7年5月1日現在)"}


_CLAUSES: dict | None = None


def special_clauses() -> dict:
    """特約文例集(全宅連 181.条項例集より)。"""
    global _CLAUSES
    if _CLAUSES is None:
        path = os.path.join(os.path.dirname(__file__), "special_clauses.json")
        try:
            _CLAUSES = json.load(open(path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _CLAUSES = {}
    return _CLAUSES
