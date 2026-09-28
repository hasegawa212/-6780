"""screening.py のテスト — 06「電話で聞く5問」の仮ランク判定."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import screening  # noqa: E402
from screening import JobChange, PhoneAnswers, ProvisionalRank, provisional_rank  # noqa: E402


def answers(**overrides) -> PhoneAnswers:
    """5問すべて満たした状態を既定にし、崩したいところだけ上書きする."""
    base = dict(
        rent_yen=60_000,
        tenure_years=5.0,
        job_change=JobChange.NONE,
        decision_maker_present=True,
        has_motivation=True,
    )
    base.update(overrides)
    return PhoneAnswers(**base)


class TestRankByUnmetCount:
    def test_5問すべて満たせばA(self):
        assert provisional_rank(answers()).rank is ProvisionalRank.A

    def test_1つ欠ければB(self):
        assert provisional_rank(answers(decision_maker_present=False)).rank is ProvisionalRank.B

    def test_2つ欠ければC(self):
        got = provisional_rank(answers(decision_maker_present=False, tenure_years=2.0))
        assert got.rank is ProvisionalRank.C

    def test_3つ欠けてもC(self):
        """Cより下はない。保留・見送りは別の判定."""
        got = provisional_rank(
            answers(decision_maker_present=False, tenure_years=2.0, has_motivation=False)
        )
        assert got.rank is ProvisionalRank.C


class TestExclusionsTakePrecedence:
    """除外は加点より先。他が満点でも覆らない."""

    @pytest.mark.parametrize("tenure", [0.0, 0.5, 0.99])
    def test_勤続1年未満は他が満点でも保留(self, tenure):
        got = provisional_rank(answers(tenure_years=tenure))
        assert got.rank is ProvisionalRank.HOLD

    def test_転職検討中は他が満点でも保留(self):
        got = provisional_rank(answers(job_change=JobChange.CONSIDERING))
        assert got.rank is ProvisionalRank.HOLD

    def test_家賃ゼロかつ動機なしは見送り(self):
        got = provisional_rank(answers(rent_yen=0, has_motivation=False))
        assert got.rank is ProvisionalRank.DECLINE

    def test_見送りは保留より優先される(self):
        """両方に該当する場合、より強い除外を返す."""
        got = provisional_rank(answers(rent_yen=0, has_motivation=False, tenure_years=0.5))
        assert got.rank is ProvisionalRank.DECLINE


class TestExclusionBoundaries:
    """除外条件を広げすぎない。母数が落ちる原因になる."""

    def test_家賃ゼロだけでは見送りにしない(self):
        """社宅でも退去時期という動機になり得る."""
        got = provisional_rank(answers(rent_yen=0))
        assert got.rank is not ProvisionalRank.DECLINE

    def test_動機なしだけでは見送りにしない(self):
        got = provisional_rank(answers(has_motivation=False))
        assert got.rank is not ProvisionalRank.DECLINE

    def test_転勤の可能性は減点しない(self):
        """転勤は不安の問題でトークで解消できる。転職は制度の問題で通らない."""
        got = provisional_rank(answers(job_change=JobChange.TRANSFER_POSSIBLE))
        assert got.rank is ProvisionalRank.A

    def test_勤続ちょうど1年は保留にしない(self):
        assert provisional_rank(answers(tenure_years=1.0)).rank is not ProvisionalRank.HOLD

    def test_勤続ちょうど3年は満たしたとみなす(self):
        assert provisional_rank(answers(tenure_years=3.0)).rank is ProvisionalRank.A


class TestUnanswered:
    """未確認と不適合は別物。聞けていないことを握りつぶさない."""

    def test_未回答はmissingに出る(self):
        got = provisional_rank(answers(rent_yen=None, has_motivation=None))
        assert set(got.missing) == {"rent_yen", "has_motivation"}

    def test_未回答があるとAにはならない(self):
        got = provisional_rank(answers(decision_maker_present=None))
        assert got.rank is not ProvisionalRank.A

    def test_未回答は見送り判定を発動させない(self):
        """家賃を聞き忘れただけで見送りにしてはいけない."""
        got = provisional_rank(answers(rent_yen=None, has_motivation=None))
        assert got.rank is not ProvisionalRank.DECLINE

    def test_未回答は保留判定を発動させない(self):
        got = provisional_rank(answers(tenure_years=None, job_change=None))
        assert got.rank is not ProvisionalRank.HOLD

    def test_全問満たしていればmissingは空(self):
        assert provisional_rank(answers()).missing == []


class TestReasons:
    """ランクだけ返しても現場は納得しない。理由を必ず返す."""

    def test_落ちた条件が理由に出る(self):
        got = provisional_rank(answers(decision_maker_present=False))
        assert any("同席" in r for r in got.reasons)

    def test_保留の理由が出る(self):
        got = provisional_rank(answers(job_change=JobChange.CONSIDERING))
        assert any("転職" in r for r in got.reasons)

    def test_見送りの理由が出る(self):
        got = provisional_rank(answers(rent_yen=0, has_motivation=False))
        assert got.reasons
        assert any("家賃" in r for r in got.reasons)

    def test_Aでも理由は空にしない(self):
        assert provisional_rank(answers()).reasons


class TestProvisionalIsNotFinal:
    def test_仮ランクであることが型で分かる(self):
        """確定ランク（rank.Rank）と混ざらないよう別の型にする."""
        got = provisional_rank(answers())
        assert isinstance(got.rank, ProvisionalRank)
        assert got.is_provisional is True

    def test_Sランクは仮では出さない(self):
        """電話では年収を聞かないので、Sを名乗れる情報がない."""
        assert not hasattr(ProvisionalRank, "S")
        assert screening.__doc__ is not None
