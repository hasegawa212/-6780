"""ダッシュボード集計（Stats）のテスト（TDD）。

担当別・日別の発信数/応答率/成約率を集計する。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac.config import CONFIG  # noqa: E402


def _tmp_log():
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    f.close()
    CONFIG.calllog_file = f.name
    return f.name


def _seed_records():
    """テスト用レコードを投入する。"""
    from tac import calllog
    _tmp_log()
    calllog.append("outbound", "+81901111111", "dialed", agent_name="田中")
    calllog.append("outbound", "+81902222222", "dialed", agent_name="田中")
    calllog.append("outbound", "+81902222222", "disposition", disposition="成約", agent_name="田中")
    calllog.append("outbound", "+81903333333", "dialed", agent_name="鈴木")
    calllog.append("outbound", "+81903333333", "disposition", disposition="検討", agent_name="鈴木")
    calllog.append("outbound", "+81904444444", "dialed", agent_name="鈴木")
    calllog.append("outbound", "+81904444444", "disposition", disposition="成約", agent_name="鈴木")
    calllog.append("outbound", "+81905555555", "blocked", reason="dnc")


def test_aggregate_by_agent():
    from tac import stats
    _seed_records()
    result = stats.aggregate()
    by_agent = result["by_agent"]
    assert "田中" in by_agent
    assert "鈴木" in by_agent
    assert by_agent["田中"]["dialed"] == 2
    assert by_agent["鈴木"]["dialed"] == 2


def test_aggregate_seiyaku_rate():
    from tac import stats
    _seed_records()
    result = stats.aggregate()
    by_agent = result["by_agent"]
    # 田中: 1成約 / 1 disposition = 100%
    assert by_agent["田中"]["seiyaku"] == 1
    assert by_agent["田中"]["dispositions"] == 1
    # 鈴木: 1成約 / 2 dispositions = 50%
    assert by_agent["鈴木"]["seiyaku"] == 1
    assert by_agent["鈴木"]["dispositions"] == 2


def test_aggregate_by_date():
    from tac import stats
    _seed_records()
    result = stats.aggregate()
    by_date = result["by_date"]
    assert len(by_date) >= 1
    today_key = list(by_date.keys())[0]
    assert by_date[today_key]["dialed"] >= 4


def test_aggregate_totals():
    from tac import stats
    _seed_records()
    result = stats.aggregate()
    assert result["total_dialed"] == 4
    assert result["total_dispositions"] == 3
    assert result["total_seiyaku"] == 2


def test_aggregate_empty():
    from tac import stats
    _tmp_log()
    result = stats.aggregate()
    assert result["total_dialed"] == 0
    assert result["by_agent"] == {}
    assert result["by_date"] == {}


def test_stats_route_requires_token():
    import importlib
    try:
        server = importlib.import_module("tac.server")
    except Exception:
        return
    _seed_records()
    CONFIG.outbound_token = "tok-stats-test"
    client = server.app.test_client()
    try:
        assert client.get("/tac/calls/stats").status_code == 401
        r = client.get("/tac/calls/stats", query_string={"token": "tok-stats-test"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert "by_agent" in body["stats"]
        assert "by_date" in body["stats"]
    finally:
        CONFIG.outbound_token = ""
