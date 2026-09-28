"""住宅ローンの計算。営業プレイブック9冊に出てくる全数値の一次情報源.

すべて **元利均等・35年** が既定。資料側の数字と1円/1万円単位で一致する。

単位の規約（取り違えが最も起きやすいので接尾辞で必ず区別する）::

    *_man   万円
    *_yen   円

金利は2種類あり、混ぜてはいけない::

    EXECUTION_RATE  実行金利。月々いくら払うか
    SCREENING_RATE  審査金利。いくらまで借りられるか

実行金利で借入上限を出すと年収400万で4,133万（年収の10.3倍）が出て、
事前審査で落ちる。「年収の7〜8倍」は独立したルールではなく、
返済比率35%を審査金利3.0〜3.5%で計算した結果である。
"""

from __future__ import annotations

# --- 前提条件 -----------------------------------------------------------
EXECUTION_RATE = 0.01
"""実行金利。月々の返済額を出すときはこちら。"""

SCREENING_RATE = 0.035
"""審査金利。借入上限を出すときはこちら。金融機関により 3.0〜3.5%。"""

REPAYMENT_RATIO = 0.35
"""返済比率。年収に対する年間返済額の上限。"""

TERM_YEARS = 35
"""返済年数。"""

PROPOSAL_FACTOR = 0.7
"""提案上限の係数。審査上限の7割が、持ち出しを維持できる線。"""


def payment_factor(annual_rate: float, years: int = TERM_YEARS) -> float:
    """元利均等の返済係数。元本1円あたりの毎月返済額を返す。

    金利0%のときは単純に回数で割る（ゼロ除算を避ける）。
    """
    months = years * 12
    if months <= 0:
        raise ValueError("years must be positive")
    if annual_rate == 0:
        return 1 / months
    r = annual_rate / 12
    return r / (1 - (1 + r) ** -months)


def monthly_payment_yen(
    principal_man: float,
    annual_rate: float = EXECUTION_RATE,
    years: int = TERM_YEARS,
) -> int:
    """借入額（万円）に対する毎月返済額（円）。四捨五入。"""
    if principal_man <= 0:
        return 0
    return round(principal_man * 10_000 * payment_factor(annual_rate, years))


PER_500MAN_YEN = monthly_payment_yen(500)
"""借入500万あたりの月々（14,114円）。借入額に依存しない定数。

元利均等の返済額は元本に比例するので、どの起点から500万足しても差は同じ。
現場の暗算はこの性質に依存している。
"""

PER_100MAN_YEN = monthly_payment_yen(100)
"""借入100万あたりの月々（2,823円）。頭金100万で下がる額でもある。

注意: 「年収400万の借入上限 2,823万」と数字が一致するが、**無関係**である。
単位が違う（円 と 万円）。
"""


def max_borrowing_man(
    annual_income_man: float,
    *,
    screening_rate: float = SCREENING_RATE,
    repayment_ratio: float = REPAYMENT_RATIO,
    years: int = TERM_YEARS,
    existing_debt_monthly_yen: float = 0,
) -> int:
    """審査上限（万円）。四捨五入。

    既存借入（車ローン・奨学金・カードの月払い）は、返済可能額から差し引く。
    年収350万に車ローン月2万があるだけで、枠は 2,470万 → 1,986万 と484万減る。

    切り捨てではなく四捨五入を使う。切り捨てにすると年収400万・500万・850万の
    3件が資料の記載と1万ずれるため、印刷済みの数字を正とした。
    """
    if annual_income_man <= 0:
        return 0
    capacity_yen = annual_income_man * 10_000 * repayment_ratio / 12
    capacity_yen -= existing_debt_monthly_yen
    if capacity_yen <= 0:
        return 0
    return round(capacity_yen / payment_factor(screening_rate, years) / 10_000)


def proposal_cap_man(
    annual_income_man: float,
    *,
    factor: float = PROPOSAL_FACTOR,
    **kwargs: float,
) -> int:
    """提案上限（万円）＝ 審査上限 × 0.7。

    審査上限は「借りられる額」であって「持ち出しが変わらない額」ではない。
    上限一杯で提案すると、初訪で取った「月々変わらないならやりたい」という
    YESがその場で無効になる。しかもこの月々には固定資産税・火災保険・
    修繕費が乗っていない。
    """
    return round(max_borrowing_man(annual_income_man, **kwargs) * factor)


def remaining_balance_man(
    principal_man: float,
    *,
    months_paid: int,
    annual_rate: float = EXECUTION_RATE,
    years: int = TERM_YEARS,
) -> int:
    """指定回数の返済後に残っている元本（万円）。四捨五入。

    借入2,000万・35年・金利1%で120回（10年）返済すると 1,498万 残る。
    「10年で600万払って手元に何も残らない賃貸」との比較に使う数字。
    """
    months = years * 12
    if months_paid >= months:
        return 0
    if months_paid <= 0:
        return round(principal_man)

    balance_yen = principal_man * 10_000
    payment_yen = balance_yen * payment_factor(annual_rate, years)
    r = annual_rate / 12
    for _ in range(months_paid):
        balance_yen = balance_yen * (1 + r) - payment_yen
    return round(max(balance_yen, 0) / 10_000)
