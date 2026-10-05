"""自動フォロー 常駐オート運転（無人スケジューラ）のテスト（TDD）。

tick() の多重ロック（自動運転/エンジン/一時停止/時間帯）を固定する。
実発信は run を注入してモックし、本物の発信は呼ばない。
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import autofollow_scheduler as sched  # noqa: E402
from tac.config import CONFIG  # noqa: E402

NOON_JST = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)   # JST 13:00（営業時間内）
NIGHT_JST = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)  # JST 翌5:00（時間外）

ALL_ON = {"auto": True, "enabled": True, "paused": False}


@pytest.fixture(autouse=True)
def _hours(monkeypatch):
    monkeypatch.setattr(CONFIG, "enforce_call_hours", True)
    yield


def test_tick_runs_when_all_on_and_in_hours():
    calls = []
    res = sched.tick(now=NOON_JST, state=ALL_ON,
                     run=lambda: calls.append(1) or {"placed": 2})
    assert res["ran"] is True
    assert res["placed"] == 2
    assert calls == [1]


def test_tick_skips_when_auto_off():
    calls = []
    res = sched.tick(now=NOON_JST, state={"auto": False, "enabled": True, "paused": False},
                     run=lambda: calls.append(1))
    assert res["ran"] is False
    assert "自動運転OFF" in res["reason"]
    assert calls == []


def test_tick_skips_when_engine_off():
    res = sched.tick(now=NOON_JST, state={"auto": True, "enabled": False, "paused": False},
                     run=lambda: 1 / 0)
    assert res["ran"] is False
    assert "エンジンOFF" in res["reason"]


def test_tick_skips_when_paused():
    res = sched.tick(now=NOON_JST, state={"auto": True, "enabled": True, "paused": True},
                     run=lambda: 1 / 0)
    assert res["ran"] is False
    assert "停止" in res["reason"]


def test_tick_skips_outside_calling_hours():
    res = sched.tick(now=NIGHT_JST, state=ALL_ON, run=lambda: 1 / 0)
    assert res["ran"] is False
    assert "時間帯" in res["reason"]


def test_start_is_idempotent():
    try:
        first = sched.start(interval=3600)
        second = sched.start(interval=3600)
        assert first is True
        assert second is False  # 多重起動しない
    finally:
        sched.stop()
