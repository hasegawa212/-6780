"""quadrant.py のテスト — 09「誰に誰を当てるか」の年収×貯蓄4象限."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quadrant import CustomerType, classify  # noqa: E402


class TestQuadrant:
    @pytest.mark.parametrize(
        ("income", "savings", "expected"),
        [
            (650, 200, CustomerType.A),
            (500, 150, CustomerType.A),
            (650, 100, CustomerType.B),
            (500, 0, CustomerType.B),
            (400, 200, CustomerType.C),
            (350, 150, CustomerType.C),
            (400, 100, CustomerType.D),
            (350, 0, CustomerType.D),
        ],
    )
    def test_4象限に振り分ける(self, income, savings, expected):
        assert classify(income, savings).type is expected


class TestBoundaries:
    def test_年収の境界は500万(self):
        """資料は「年収350-450万」と「500万↑」で割れており450〜500に隙間がある。
        実装では500万を境界として隙間を埋める."""
        assert classify(499, 200).type is CustomerType.C
        assert classify(500, 200).type is CustomerType.A

    def test_貯蓄の境界は150万(self):
        """諸費用（物件価格の7〜10%）を自己資金で出せるかの線."""
        assert classify(600, 149).type is CustomerType.B
        assert classify(600, 150).type is CustomerType.A


class TestUnknown:
    def test_年収未確認なら分類しない(self):
        assert classify(None, 200).type is None

    def test_貯蓄未確認なら分類しない(self):
        assert classify(600, None).type is None

    def test_未確認でも理由は返す(self):
        assert classify(None, None).reasons


class TestGuidance:
    """象限ごとに、次に何をするかが返ること."""

    def test_全象限に打ち手がある(self):
        for income, savings in [(650, 200), (650, 0), (400, 200), (400, 0)]:
            got = classify(income, savings)
            assert got.focus
            assert got.reasons

    def test_TypeBは金融機関の選定が論点(self):
        assert "金融機関" in classify(650, 0).focus

    def test_TypeCは頭金で月々を下げるのが主武器(self):
        assert "頭金" in classify(400, 200).focus


class TestClosingCosts:
    """諸費用は物件価格の7〜10%。貯蓄150万の根拠."""

    def test_諸費用レンジを返す(self):
        from quadrant import closing_costs_man

        low, high = closing_costs_man(2_000)
        assert (low, high) == (140, 200)

    def test_物件価格ゼロならゼロ(self):
        from quadrant import closing_costs_man

        assert closing_costs_man(0) == (0, 0)

    def test_頭金に回せる額は貯蓄から諸費用を引いた残り(self):
        """貯蓄200万で頭金200万を入れると諸費用が払えなくなる."""
        from quadrant import affordable_down_payment_man

        assert affordable_down_payment_man(savings_man=200, property_price_man=2_000) == 0
        assert affordable_down_payment_man(savings_man=400, property_price_man=2_000) == 200

    def test_足りなければマイナスではなくゼロ(self):
        from quadrant import affordable_down_payment_man

        assert affordable_down_payment_man(savings_man=50, property_price_man=2_000) == 0
