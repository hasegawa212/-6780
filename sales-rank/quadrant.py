"""09「誰に誰を当てるか」→ 年収 × 貯蓄の4象限.

年収は「いくら借りられるか」、貯蓄は「諸費用を自己資金で出せるか」を決める。
この2つは独立していて、片方だけでは提案が組めない。

貯蓄150万が分かれ目になるのは、諸費用が物件価格の7〜10%だから。
物件2,000万なら140〜200万。ここを自己資金で出せるかどうかで、
選べる金融機関が変わる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

INCOME_HIGH_MAN = 500.0
"""年収の境界。

資料は「年収500万↑」と「年収350-450万」で割っており、450〜500万に隙間がある。
表記揺れなので、実装では500万を境界として隙間を埋めた。
"""

SAVINGS_HIGH_MAN = 150.0
"""貯蓄の境界。諸費用を自己資金で出せるかの実務上の線。"""

CLOSING_COST_LOW = 0.07
CLOSING_COST_HIGH = 0.10
"""諸費用の目安。物件価格の7〜10%。"""


class CustomerType(Enum):
    A = "TYPE A"
    B = "TYPE B"
    C = "TYPE C"
    D = "TYPE D"


@dataclass(frozen=True)
class QuadrantResult:
    type: CustomerType | None
    focus: str = ""
    reasons: list[str] = field(default_factory=list)


_GUIDE: dict[CustomerType, tuple[str, list[str]]] = {
    CustomerType.A: (
        "物件のグレードで攻める",
        [
            "年収・貯蓄とも高く、選択肢が最も広い",
            "諸費用を自己資金で払えるので金融機関を選べる",
            "ランクS/Aとして最優先でCMOを当てる",
        ],
    ),
    CustomerType.B: (
        "諸費用込みで組める金融機関を先に押さえる。金融機関の選定が勝負",
        [
            "借りる力はあるが初期費用が出せない",
            "ここで落とすと属性がもったいない",
            "レベル3の知識が要る案件",
        ],
    ),
    CustomerType.C: (
        "上限まで提案しない。頭金で月々を家賃以下に落とす",
        [
            "貯蓄を頭金に回せば月々を確実に下げられる",
            "頭金100万で月々2,823円下がる",
            "持ち出し合意が最も作りやすい層",
        ],
    ),
    CustomerType.D: (
        "物件価格を絞る。諸費用込みで組める先を探す",
        [
            "借入枠より諸費用がボトルネックになる",
            "勤続年数と既存借入がここで効く",
            "電話5問で先に確認しておくこと",
        ],
    ),
}


def classify(
    annual_income_man: float | None, savings_man: float | None
) -> QuadrantResult:
    """年収と貯蓄から顧客タイプを返す。どちらか未確認なら分類しない。"""
    if annual_income_man is None or savings_man is None:
        unknown = []
        if annual_income_man is None:
            unknown.append("年収")
        if savings_man is None:
            unknown.append("貯蓄")
        return QuadrantResult(
            None,
            "",
            [f"{'・'.join(unknown)}が未確認のため分類できない",
             "在宅訪問で取ること（電話では聞かない）"],
        )

    high_income = annual_income_man >= INCOME_HIGH_MAN
    high_savings = savings_man >= SAVINGS_HIGH_MAN
    t = {
        (True, True): CustomerType.A,
        (True, False): CustomerType.B,
        (False, True): CustomerType.C,
        (False, False): CustomerType.D,
    }[(high_income, high_savings)]
    focus, reasons = _GUIDE[t]
    return QuadrantResult(t, focus, list(reasons))


def closing_costs_man(property_price_man: float) -> tuple[int, int]:
    """諸費用の目安（万円）の下限・上限。物件価格の7〜10%。"""
    if property_price_man <= 0:
        return (0, 0)
    return (
        round(property_price_man * CLOSING_COST_LOW),
        round(property_price_man * CLOSING_COST_HIGH),
    )


def affordable_down_payment_man(
    *, savings_man: float, property_price_man: float
) -> int:
    """頭金に回せる額（万円）。

    諸費用は自己資金から出る。貯蓄200万で頭金200万を入れると諸費用が払えない。
    **安全側に倒し、諸費用の上限（10%）を引いた残り**を返す。
    手元に残す生活費はここに含まれていないので、実務ではさらに引くこと。
    """
    _, high = closing_costs_man(property_price_man)
    return max(round(savings_man - high), 0)
