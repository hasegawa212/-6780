"""06「電話で聞く5問」→ 仮ランク.

電話では年収を聞かない（警戒されて切られる）。取れるのは家賃・勤続年数・
転職転勤・同居家族・購入動機の5つだけ。したがってここで出るのは **仮ランク**
であり、Sは存在しない。確定ランクは在宅で属性を取ってから :mod:`rank` で付け直す。

判定の順序が仕様の本体である::

    1. 見送り判定（家賃0円 かつ 購入動機なし）
    2. 保留判定（勤続1年未満 または 転職検討中）
    3. 残りを充足数で A / B / C

除外を先に置くのは、加点式にすると「勤続1年未満」のような絶対条件が
他の高得点に埋もれるため。年収が高くても勤続1年未満なら金融機関が通さない。

未回答（None）は「条件を満たさない」ではなく「まだ聞けていない」として扱う。
聞き忘れただけの相手を見送りにしてはいけない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

TENURE_HOLD_YEARS = 1.0
"""これ未満は保留。勤続年数がリセットされていると金融機関が通さない。"""

TENURE_GOOD_YEARS = 3.0
"""これ以上あれば勤続の条件を満たす。"""


class JobChange(Enum):
    """転職・転勤の予定。

    転勤と転職は別物である。転勤は**不安の問題**でトークで解消できる
    （「持ち家でも貸せる」で実証済み）。転職は**制度の問題**で、勤続年数が
    リセットされるため営業力ではどうにもならない。
    """

    NONE = "なし"
    TRANSFER_POSSIBLE = "転勤の可能性あり"
    CONSIDERING = "転職を検討中"


class ProvisionalRank(Enum):
    """電話時点の仮ランク。確定ランク（:class:`rank.Rank`）とは別の型。"""

    A = "A"
    B = "B"
    C = "C"
    HOLD = "保留"
    DECLINE = "見送り"


@dataclass(frozen=True)
class PhoneAnswers:
    """電話5問の答え。未確認は ``None``。"""

    rent_yen: int | None = None
    """Q1 家賃（円）。0円は主力トーク「家賃→資産」が使えないことを意味する。"""

    tenure_years: float | None = None
    """Q2 勤続年数。"""

    job_change: JobChange | None = None
    """Q3 転職・転勤の予定。"""

    decision_maker_present: bool | None = None
    """Q4 決裁権者（配偶者・親）の同席が取れるか。"""

    has_motivation: bool | None = None
    """Q5 購入動機があるか。"""


@dataclass(frozen=True)
class ScreeningResult:
    rank: ProvisionalRank
    reasons: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    is_provisional: bool = True


def _missing_fields(a: PhoneAnswers) -> list[str]:
    names = ("rent_yen", "tenure_years", "job_change", "decision_maker_present", "has_motivation")
    return [n for n in names if getattr(a, n) is None]


def provisional_rank(answers: PhoneAnswers) -> ScreeningResult:
    """電話5問の答えから仮ランクを返す。"""
    missing = _missing_fields(answers)

    # --- 1. 見送り: 主力トークが使えず、動機もない ---------------------
    if answers.rent_yen == 0 and answers.has_motivation is False:
        return ScreeningResult(
            ProvisionalRank.DECLINE,
            [
                "家賃0円のため「家賃→資産」の変換トークが使えない",
                "購入動機も確認できていない",
                "断りではなく順番の問題。情報提供だけして次へ",
            ],
            missing,
        )

    # --- 2. 保留: 制度の壁。営業力では動かない -------------------------
    holds: list[str] = []
    if answers.tenure_years is not None and answers.tenure_years < TENURE_HOLD_YEARS:
        holds.append(f"勤続{answers.tenure_years:g}年（1年未満）で事前審査に乗らない")
    if answers.job_change is JobChange.CONSIDERING:
        holds.append("転職を検討中。勤続年数がリセットされ金融機関が通さない")
    if holds:
        holds.append("訪問しない。半年後フォローの台帳へ。再架電の時期をメモすること")
        return ScreeningResult(ProvisionalRank.HOLD, holds, missing)

    # --- 3. 充足数で A / B / C ----------------------------------------
    unmet: list[str] = []
    if answers.rent_yen is None:
        unmet.append("家賃が未確認")
    elif answers.rent_yen <= 0:
        unmet.append("家賃0円。主力トークが効かない")

    if answers.tenure_years is None:
        unmet.append("勤続年数が未確認")
    elif answers.tenure_years < TENURE_GOOD_YEARS:
        unmet.append(f"勤続{answers.tenure_years:g}年。金融機関を第2候補まで用意する")

    if answers.job_change is None:
        unmet.append("転職・転勤の予定が未確認")

    if answers.decision_maker_present is None:
        unmet.append("決裁権者の同席可否が未確認")
    elif not answers.decision_maker_present:
        unmet.append("決裁権者の同席が取れていない。持ち帰りは8割が「やめよう」になる")

    if answers.has_motivation is None:
        unmet.append("購入動機が未確認")
    elif not answers.has_motivation:
        unmet.append("購入動機が確認できていない")

    if not unmet:
        return ScreeningResult(
            ProvisionalRank.A,
            ["5問すべて条件を満たす。その場で日程を確定し、1週間以内に訪問"],
            missing,
        )
    rank = ProvisionalRank.B if len(unmet) == 1 else ProvisionalRank.C
    if rank is ProvisionalRank.B:
        unmet.append("日程は取る。欠けた条件を訪問前に埋めること")
    else:
        unmet.append("日程は取るが優先度は下げる。A・Bを先に埋める")
    return ScreeningResult(rank, unmet, missing)
