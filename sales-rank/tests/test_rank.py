"""rank.py のテスト — 07「アポが取れる相手」の3軸による確定ランク."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rank import CustomerProfile, Rank, final_rank  # noqa: E402
from screening import JobChange, ProvisionalRank  # noqa: E402


def profile(**overrides) -> CustomerProfile:
    """Sランク相当を既定にし、崩したいところだけ上書きする."""
    base = dict(
        annual_income_man=650.0,
        savings_man=200.0,
        tenure_years=9.0,
        existing_debt_monthly_yen=0,
        rent_yen=70_000,
        job_change=JobChange.NONE,
        decision_maker_present=True,
        has_motivation=True,
    )
    base.update(overrides)
    return CustomerProfile(**base)


class TestRankTable:
    def test_S_年収650万_借入なし_勤続3年以上_同席可(self):
        assert final_rank(profile()).rank is Rank.S

    def test_A_同席が取れなければSにはならない(self):
        assert final_rank(profile(decision_maker_present=False)).rank is Rank.A

    def test_A_年収500万_借入なし_勤続3年以上(self):
        assert final_rank(profile(annual_income_man=500.0)).rank is Rank.A

    def test_B_勤続1年から3年(self):
        assert final_rank(profile(tenure_years=2.0)).rank is Rank.B

    def test_B_軽微な借入あり(self):
        assert final_rank(profile(existing_debt_monthly_yen=10_000)).rank is Rank.B

    def test_C_年収350万未満(self):
        assert final_rank(profile(annual_income_man=300.0)).rank is Rank.C

    def test_C_重い借入(self):
        """返済可能額の2割を超えて食う借入は軽微ではない."""
        assert final_rank(profile(annual_income_man=350.0,
                                  existing_debt_monthly_yen=30_000)).rank is Rank.C


class TestKurabayashi:
    """倉林様 — 旧基準でCに埋もれていた案件が浮かび上がることの回帰テスト.

    「年収650万・勤続9年の倉林様もCランク」が07の症例。
    測り方が壊れていただけで、案件が悪かったわけではない。
    """

    def test_旧基準でCだった案件がSになる(self):
        got = final_rank(profile(annual_income_man=650.0, tenure_years=9.0))
        assert got.rank is Rank.S

    def test_同席が未確定ならAどまり(self):
        got = final_rank(profile(annual_income_man=650.0, tenure_years=9.0,
                                 decision_maker_present=None))
        assert got.rank is Rank.A


class TestExclusions:
    @pytest.mark.parametrize("tenure", [0.0, 0.5, 0.99])
    def test_勤続1年未満は年収が高くても保留(self, tenure):
        assert final_rank(profile(tenure_years=tenure)).rank is Rank.HOLD

    def test_転職検討中は年収が高くても保留(self):
        assert final_rank(profile(job_change=JobChange.CONSIDERING)).rank is Rank.HOLD

    def test_家賃ゼロかつ動機なしは見送り(self):
        assert final_rank(profile(rent_yen=0, has_motivation=False)).rank is Rank.DECLINE

    def test_転勤の可能性は減点しない(self):
        assert final_rank(profile(job_change=JobChange.TRANSFER_POSSIBLE)).rank is Rank.S


class TestBoundaries:
    @pytest.mark.parametrize(
        ("income", "expected"),
        [(649.0, Rank.A), (650.0, Rank.S), (499.0, Rank.B), (500.0, Rank.A),
         (349.0, Rank.C), (350.0, Rank.B)],
    )
    def test_年収の境界(self, income, expected):
        assert final_rank(profile(annual_income_man=income)).rank is expected

    def test_勤続ちょうど3年はSの条件を満たす(self):
        assert final_rank(profile(tenure_years=3.0)).rank is Rank.S

    def test_軽微な借入の境界は返済可能額の2割(self):
        """年収350万の返済可能額は月102,083円。その2割＝20,416円が境目."""
        assert final_rank(profile(annual_income_man=350.0,
                                  existing_debt_monthly_yen=20_000)).rank is Rank.B
        assert final_rank(profile(annual_income_man=350.0,
                                  existing_debt_monthly_yen=21_000)).rank is Rank.C


class TestLoanFigures:
    """判定と同時に、提案に使う数字も返す."""

    def test_借入上限と提案上限を返す(self):
        got = final_rank(profile(annual_income_man=400.0))
        assert got.max_borrowing_man == 2_823
        assert got.proposal_cap_man == round(2_823 * 0.7)

    def test_既存借入が上限に反映される(self):
        got = final_rank(profile(annual_income_man=350.0,
                                 existing_debt_monthly_yen=20_000))
        assert got.max_borrowing_man == 1_986

    def test_年収未確認なら上限は出さない(self):
        got = final_rank(profile(annual_income_man=None))
        assert got.max_borrowing_man is None
        assert got.proposal_cap_man is None

    def test_提案上限は必ず借入上限より小さい(self):
        got = final_rank(profile(annual_income_man=650.0))
        assert got.proposal_cap_man < got.max_borrowing_man


class TestAxes:
    """3軸それぞれの評価を返す。ランクだけでは何を直せばよいか分からない."""

    def test_3軸すべてが返る(self):
        axes = final_rank(profile()).axes
        assert set(axes) == {"会える確度", "属性の質", "審査適性"}

    def test_勤続が短いと審査適性だけが下がる(self):
        got = final_rank(profile(tenure_years=2.0))
        assert "3年" in got.axes["審査適性"] or "勤続" in got.axes["審査適性"]

    def test_同席が取れないと会える確度に出る(self):
        got = final_rank(profile(decision_maker_present=False))
        assert "同席" in got.axes["会える確度"]


class TestUnknownVsUnmet:
    def test_未確認はmissingに出る(self):
        got = final_rank(profile(annual_income_man=None, savings_man=None))
        assert set(got.missing) == {"annual_income_man", "savings_man"}

    def test_年収未確認ではSにもAにもならない(self):
        assert final_rank(profile(annual_income_man=None)).rank not in (Rank.S, Rank.A)

    def test_未確認は見送りを発動させない(self):
        assert final_rank(profile(rent_yen=None, has_motivation=None)).rank is not Rank.DECLINE


class TestNotProvisional:
    def test_確定ランクは仮ランクと別の型(self):
        got = final_rank(profile())
        assert isinstance(got.rank, Rank)
        assert not isinstance(got.rank, ProvisionalRank)
        assert got.is_provisional is False

    def test_理由は必ず返る(self):
        assert final_rank(profile()).reasons


class TestCanMeet:
    def test_会えていなければCどまり(self):
        got = final_rank(profile(can_meet=False))
        assert got.rank is Rank.C
        assert any("アポ" in r for r in got.reasons)
