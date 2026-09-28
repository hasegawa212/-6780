"""07「アポが取れる相手」→ 3軸による確定ランク.

旧基準は「会える確度」1軸だけで A〜C- を付けていた。その結果、
**年収650万・勤続9年の案件がCランクに埋もれていた**（倉林様）。
測り方が壊れていただけで、案件が悪かったわけではない。

必要な軸は3つ::

    会える確度   アポが成立するか。従来測っていた唯一の軸
    属性の質     年収・貯蓄・借入。いくらの物件を提案できるか
    審査適性     勤続年数・既存借入。そもそもローンが通るか

勤続年数を属性から独立させているのは性質が違うため。年収が高くても
勤続1年未満なら金融機関が通さない。営業力でも属性の良さでも埋まらない
**制度の壁**なので、独立した軸にする。

これは運用改善ではなく **基準の入れ替え** である。旧Bと新Bは別物なので、
全員が同じ日から切り替えないと週次の集計が読めなくなる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import loan
from screening import TENURE_GOOD_YEARS, TENURE_HOLD_YEARS, JobChange

INCOME_S_MAN = 650.0
INCOME_A_MAN = 500.0
INCOME_B_MAN = 350.0

MINOR_DEBT_RATIO = 0.20
"""「軽微な借入」の定義。返済可能額のこの割合までなら軽微とみなす。

資料は「軽微な借入あり」とだけ書いており、金額の線が引かれていない。
金額で決め打ちすると年収によって重さが変わってしまうため、
**返済可能額に対する比率**で定義した。

元利均等では借入上限が返済可能額に比例するので、
「返済可能額の何割を食うか」＝「借入枠の何割が消えるか」になる。
ロープレ設定A（年収350万・車ローン月2万）はちょうど 19.6% で、
枠は 2,470万 → 1,986万 と 19.6% 減る。この案件がBに収まる線。
"""


class Rank(Enum):
    """確定ランク。仮ランク（:class:`screening.ProvisionalRank`）とは別の型。"""

    S = "S"
    A = "A"
    B = "B"
    C = "C"
    HOLD = "保留"
    DECLINE = "見送り"


@dataclass(frozen=True)
class CustomerProfile:
    """電話5問＋在宅訪問で取れた情報。未確認は ``None``。"""

    annual_income_man: float | None = None
    savings_man: float | None = None
    tenure_years: float | None = None
    existing_debt_monthly_yen: float = 0
    rent_yen: int | None = None
    job_change: JobChange | None = None
    decision_maker_present: bool | None = None
    has_motivation: bool | None = None
    can_meet: bool = True
    """会える見込みがあるか。資料のCは「会える」が前提なので、
    会えていない相手は表の外側。ランクを付ける前にアポ取りが先。"""


@dataclass(frozen=True)
class RankResult:
    rank: Rank
    reasons: list[str] = field(default_factory=list)
    axes: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    max_borrowing_man: int | None = None
    proposal_cap_man: int | None = None
    is_provisional: bool = False


_TRACKED = (
    "annual_income_man",
    "savings_man",
    "tenure_years",
    "rent_yen",
    "job_change",
    "decision_maker_present",
    "has_motivation",
)


def _is_minor_debt(p: CustomerProfile) -> bool:
    """既存借入が「軽微」か。年収が不明なら判定できないので False。"""
    if p.existing_debt_monthly_yen <= 0:
        return True
    if not p.annual_income_man:
        return False
    capacity_yen = p.annual_income_man * 10_000 * loan.REPAYMENT_RATIO / 12
    return p.existing_debt_monthly_yen <= capacity_yen * MINOR_DEBT_RATIO


def _axes(p: CustomerProfile) -> dict[str, str]:
    if p.decision_maker_present is True:
        meet = "決裁権者の同席が取れる"
    elif p.decision_maker_present is False:
        meet = "決裁権者の同席が取れていない。持ち帰りは8割が「やめよう」になる"
    else:
        meet = "決裁権者の同席可否が未確認"
    if not p.can_meet:
        meet = "まだ会えていない。" + meet

    if p.annual_income_man is None:
        quality = "年収が未確認。在宅訪問で取ること（電話では聞かない）"
    else:
        quality = f"年収{p.annual_income_man:g}万"
        if p.savings_man is None:
            quality += "／貯蓄が未確認"
        else:
            quality += f"／貯蓄{p.savings_man:g}万"

    if p.tenure_years is None:
        fit = "勤続年数が未確認"
    elif p.tenure_years < TENURE_HOLD_YEARS:
        fit = f"勤続{p.tenure_years:g}年。1年未満は事前審査に乗らない"
    elif p.tenure_years < TENURE_GOOD_YEARS:
        fit = f"勤続{p.tenure_years:g}年。3年未満のため金融機関を第2候補まで用意する"
    else:
        fit = f"勤続{p.tenure_years:g}年。審査要件を満たす"
    if p.existing_debt_monthly_yen > 0:
        weight = "軽微" if _is_minor_debt(p) else "重い"
        fit += f"／既存借入 月{p.existing_debt_monthly_yen:,.0f}円（{weight}）"

    return {"会える確度": meet, "属性の質": quality, "審査適性": fit}


def final_rank(profile: CustomerProfile) -> RankResult:
    """3軸から確定ランクを返す。除外を先に評価する。"""
    p = profile
    missing = [n for n in _TRACKED if getattr(p, n) is None]
    axes = _axes(p)

    cap = borrow = None
    if p.annual_income_man:
        borrow = loan.max_borrowing_man(
            p.annual_income_man, existing_debt_monthly_yen=p.existing_debt_monthly_yen
        )
        cap = loan.proposal_cap_man(
            p.annual_income_man, existing_debt_monthly_yen=p.existing_debt_monthly_yen
        )

    def result(rank: Rank, reasons: list[str]) -> RankResult:
        return RankResult(rank, reasons, axes, missing, borrow, cap)

    # --- 見送り -------------------------------------------------------
    if p.rent_yen == 0 and p.has_motivation is False:
        return result(
            Rank.DECLINE,
            [
                "家賃0円のため「家賃→資産」の変換トークが使えない",
                "購入動機も確認できていない",
                "断りではなく順番の問題。情報提供だけして終える",
            ],
        )

    # --- 保留（制度の壁） ---------------------------------------------
    holds: list[str] = []
    if p.tenure_years is not None and p.tenure_years < TENURE_HOLD_YEARS:
        holds.append(f"勤続{p.tenure_years:g}年（1年未満）。営業力では動かせない")
    if p.job_change is JobChange.CONSIDERING:
        holds.append("転職を検討中。勤続年数がリセットされ金融機関が通さない")
    if holds:
        holds.append("訪問しない。半年後フォローへ。母数からは消さずランクだけ下げる")
        return result(Rank.HOLD, holds)

    # --- 会えていない -------------------------------------------------
    if not p.can_meet:
        return result(
            Rank.C,
            ["まだ会えていないため、表の判定対象外。まずアポを取ること"],
        )

    income = p.annual_income_man
    no_debt = p.existing_debt_monthly_yen <= 0
    tenure_ok = p.tenure_years is not None and p.tenure_years >= TENURE_GOOD_YEARS

    # --- S / A / B / C ------------------------------------------------
    if income is not None and income >= INCOME_S_MAN and no_debt and tenure_ok:
        if p.decision_maker_present is True:
            return result(
                Rank.S,
                [f"年収{income:g}万・借入なし・勤続{p.tenure_years:g}年・同席可",
                 "CMO同行を最優先で当てる"],
            )
        return result(
            Rank.A,
            [f"年収{income:g}万・借入なし・勤続{p.tenure_years:g}年",
             "決裁権者の同席が取れればS。同席を握ってから訪問する"],
        )

    if income is not None and income >= INCOME_A_MAN and no_debt and tenure_ok:
        return result(
            Rank.A,
            [f"年収{income:g}万・借入なし・勤続{p.tenure_years:g}年", "1週間以内に訪問"],
        )

    # B は「欠陥を1つ持つ層」ではなく、勤続1〜3年と軽微な借入を **許容する** 枠。
    # 資料の B 行は「年収350万↑ / 勤続1〜3年、または軽微な借入あり」と、
    # 欠陥が必須であるかのように書かれているが、それだと年収350万・借入なし・
    # 勤続9年という無欠陥の案件がCに落ちる。07 自身がターゲット層を
    # 「年収350〜650万」と定義し、そこがほぼ100%アポにつながるとしているので、
    # その読みは自己矛盾する。許容条件として実装した。
    tenure_clears_wall = p.tenure_years is not None and p.tenure_years >= TENURE_HOLD_YEARS
    if income is not None and income >= INCOME_B_MAN and tenure_clears_wall and _is_minor_debt(p):
        notes = [f"年収{income:g}万"]
        if not tenure_ok:
            notes.append(f"勤続{p.tenure_years:g}年（3年未満）")
        if p.existing_debt_monthly_yen > 0:
            notes.append(f"既存借入 月{p.existing_debt_monthly_yen:,.0f}円（軽微）")
        return result(
            Rank.B,
            ["・".join(notes), "訪問する。金融機関を第2候補まで用意する"],
        )

    reasons = ["S・A・Bのいずれの条件も満たさない", "優先度を下げる。S・Aを先に埋める"]
    if income is None:
        reasons.insert(0, "年収が未確認のため上位ランクを名乗れない")
    return result(Rank.C, reasons)
