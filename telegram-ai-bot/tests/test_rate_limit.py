"""発信の1日上限（Daily Call Cap / レート制限）のテスト（TDD）。

1日あたりの発信（dialed）件数に上限を設け、掛けすぎ（迷惑・コスト）を仕組みで
防ぐ。既定 OFF（後方互換）。当日の判定は発信時間帯ガードと同じ UTC オフセット
（既定 JST=UTC+9）を使う。DNC・時間帯ガードと並ぶ「守り」の機能。
"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import calllog, rate_limit  # noqa: E402
from tac.config import CONFIG  # noqa: E402


def _tmp_stores():
    lf = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    lf.close()
    CONFIG.calllog_file = lf.name
    df = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    df.close()
    CONFIG.dnc_file = df.name


def test_disabled_cap_always_allows():
    CONFIG.daily_call_cap = 0
    recs = [{"ts": "2026-09-28T00:00:00+00:00", "status": "dialed", "to": "+81900000000"}] * 100
    assert rate_limit.allowed(records=recs) is True


def test_dialed_today_counts_only_today_dialed():
    CONFIG.call_hours_utc_offset = 9
    now = datetime(2026, 9, 28, 3, 0, tzinfo=UTC)  # JST 12:00, 2026-09-28
    recs = [
        {"ts": "2026-09-28T02:00:00+00:00", "status": "dialed", "to": "+8190001"},  # JST今日
        {"ts": "2026-09-28T02:30:00+00:00", "status": "dialed", "to": "+8190002"},  # JST今日
        {"ts": "2026-09-28T02:40:00+00:00", "status": "blocked", "to": "+8190003"},  # dialedでない
        {"ts": "2026-09-26T02:00:00+00:00", "status": "dialed", "to": "+8190004"},  # 別の日
    ]
    assert rate_limit.dialed_today(now=now, records=recs) == 2


def test_date_boundary_uses_offset():
    """UTC 23:00 は JST では翌日。オフセットで日付が変わる。"""
    CONFIG.call_hours_utc_offset = 9
    now = datetime(2026, 9, 28, 23, 30, tzinfo=UTC)  # JST 2026-09-29 08:30
    recs = [
        {"ts": "2026-09-28T23:00:00+00:00", "status": "dialed", "to": "+8190001"},  # JST 9/29
        {"ts": "2026-09-28T10:00:00+00:00", "status": "dialed", "to": "+8190002"},  # JST 9/28（前日）
    ]
    assert rate_limit.dialed_today(now=now, records=recs) == 1


def test_allowed_under_and_over_cap():
    CONFIG.call_hours_utc_offset = 9
    CONFIG.daily_call_cap = 2
    now = datetime(2026, 9, 28, 3, 0, tzinfo=UTC)
    one = [{"ts": "2026-09-28T02:00:00+00:00", "status": "dialed", "to": "+8190001"}]
    two = one + [{"ts": "2026-09-28T02:10:00+00:00", "status": "dialed", "to": "+8190002"}]
    assert rate_limit.allowed(now=now, records=one) is True   # 1 < 2
    assert rate_limit.allowed(now=now, records=two) is False   # 2 >= 2


def test_bridge_call_blocks_when_cap_reached():
    """当日 dialed が上限に達していると、発信せず daily_cap で記録して拒否。"""
    from tac import outbound

    _tmp_stores()
    CONFIG.agent_number = "+818094662479"
    CONFIG.enforce_call_hours = False
    CONFIG.daily_call_cap = 1
    # 当日すでに 1 件 dialed（append は now=今日 で記録）
    calllog.append("outbound", "+81900001111", "dialed")
    try:
        res = outbound.bridge_call("+81901234567")
        assert res["ok"] is False
        assert res.get("blocked") is True
        rec = calllog.recent()
        assert rec[0]["status"] == "blocked"
        assert rec[0].get("reason") == "daily_cap"
        assert rec[0]["to"] == "+81901234567"
    finally:
        CONFIG.daily_call_cap = 0
