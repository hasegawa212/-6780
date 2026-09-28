"""loan.py のテスト — 資料9冊に印刷済みの数字がそのまま仕様."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import loan  # noqa: E402


class TestMonthlyPayment:
    """実行金利1%・35年・元利均等での月々返済額."""

    @pytest.mark.parametrize(
        ("principal_man", "expected_yen"),
        [
            (100, 2_823),
            (500, 14_114),
            (1_800, 50_811),
            (2_000, 56_457),
        ],
    )
    def test_資料の記載と一致する(self, principal_man, expected_yen):
        assert loan.monthly_payment_yen(principal_man) == expected_yen

    def test_借入ゼロなら月々ゼロ(self):
        assert loan.monthly_payment_yen(0) == 0

    def test_500万あたりの差は借入額に依存しない(self):
        """「借入500万＝月14,114円」が暗算に使える根拠。

        元利均等の返済額は元本に比例するので、どの起点から500万足しても差は同じ。
        この性質が崩れたら、現場の暗算が全部狂う。
        """
        bases = [0, 1_000, 2_000, 3_500]
        diffs = {
            loan.monthly_payment_yen(b + 500) - loan.monthly_payment_yen(b) for b in bases
        }
        assert diffs == {loan.PER_500MAN_YEN}
        assert loan.PER_500MAN_YEN == 14_114

    def test_頭金100万で月々2823円下がる(self):
        assert loan.PER_100MAN_YEN == 2_823
        assert (
            loan.monthly_payment_yen(2_000) - loan.monthly_payment_yen(1_900)
            == loan.PER_100MAN_YEN
        )


class TestMaxBorrowing:
    """審査金利ベースの借入上限（返済比率35%・35年）."""

    @pytest.mark.parametrize(
        ("income_man", "expected_man"),
        [
            (350, 2_470),
            (400, 2_823),
            (500, 3_529),
            (650, 4_587),
            (850, 5_999),
        ],
    )
    def test_審査金利3_5percentで資料と一致する(self, income_man, expected_man):
        assert loan.max_borrowing_man(income_man) == expected_man

    def test_審査金利3_0percentなら上限が上がる(self):
        assert loan.max_borrowing_man(400, screening_rate=0.030) == 3_031

    def test_既存借入は返済可能額から差し引かれる(self):
        """ロープレ設定A。車ローン月2万で枠が484万消える。"""
        without = loan.max_borrowing_man(350)
        with_car = loan.max_borrowing_man(350, existing_debt_monthly_yen=20_000)
        assert with_car == 1_986
        assert without - with_car == 484

    def test_既存借入が返済可能額を超えたらゼロ(self):
        """マイナスの借入上限は存在しない."""
        assert loan.max_borrowing_man(300, existing_debt_monthly_yen=999_999) == 0

    def test_年収ゼロならゼロ(self):
        assert loan.max_borrowing_man(0) == 0


class TestScreeningRateTrap:
    """提案の金利と審査の金利を混ぜてはいけない、を実行可能な形で固定する."""

    def test_実行金利で上限を出すと年収の10倍が出てしまう(self):
        danger = loan.max_borrowing_man(400, screening_rate=loan.EXECUTION_RATE)
        assert danger == 4_133
        assert danger / 400 > 10  # 10.3倍。事前審査で落ちる水準

    def test_審査金利のデフォルトは3_5percent(self):
        assert loan.SCREENING_RATE == 0.035
        assert loan.EXECUTION_RATE == 0.01
        assert loan.SCREENING_RATE != loan.EXECUTION_RATE


class TestProposalCap:
    """提案上限＝審査上限の7割。持ち出しを維持できる線."""

    def test_上限の7割を返す(self):
        assert loan.PROPOSAL_FACTOR == 0.7
        assert loan.proposal_cap_man(400) == round(2_823 * 0.7)

    def test_年収350万なら1700万台に収まる(self):
        """資料の「年収350万なら約1,700〜1,800万」と整合すること."""
        cap = loan.proposal_cap_man(350)
        assert 1_700 <= cap <= 1_800

    def test_提案上限は必ず審査上限より小さい(self):
        for income in (350, 400, 500, 650, 850):
            assert loan.proposal_cap_man(income) < loan.max_borrowing_man(income)


class TestRemainingBalance:
    def test_10年後の残高は1498万(self):
        assert loan.remaining_balance_man(2_000, months_paid=120) == 1_498

    def test_返済前は元本のまま(self):
        assert loan.remaining_balance_man(2_000, months_paid=0) == 2_000

    def test_完済すればゼロ(self):
        assert loan.remaining_balance_man(2_000, months_paid=35 * 12) == 0

    def test_残高は単調減少する(self):
        prev = None
        for m in range(0, 421, 60):
            bal = loan.remaining_balance_man(2_000, months_paid=m)
            if prev is not None:
                assert bal < prev
            prev = bal
