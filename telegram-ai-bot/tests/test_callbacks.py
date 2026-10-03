"""折り返しスケジュール（Callback Schedule）のテスト（TDD）。

disposition「折り返し」に予定日時を付けて永続保存し、当日分を一覧・
キュー先頭に出す。
"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac.config import CONFIG  # noqa: E402


def _tmp_stores():
    lf = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    lf.close()
    CONFIG.calllog_file = lf.name
    df = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    df.close()
    CONFIG.dnc_file = df.name


def test_disposition_with_callback_at():
    """折り返し disposition に callback_at を付けて記録できる。"""
    from tac import calllog, disposition
    _tmp_stores()
    cb_time = "2026-10-04T14:00"
    res = disposition.record("+819012345678", "折り返し", callback_at=cb_time)
    assert res["ok"] is True
    assert res["callback_at"] == cb_time
    rec = calllog.recent()[0]
    assert rec["callback_at"] == cb_time
    assert rec["disposition"] == "折り返し"


def test_disposition_without_callback_at():
    """callback_at が無い場合は従来通り動く。"""
    from tac import disposition
    _tmp_stores()
    res = disposition.record("+819012345678", "検討")
    assert res["ok"] is True
    assert "callback_at" not in res or res.get("callback_at") is None


def test_callbacks_list():
    """折り返し予定の一覧が取得できる。"""
    from tac import callbacks, calllog, disposition
    _tmp_stores()
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT10:00")
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT10:00")
    disposition.record("+819011111111", "折り返し", callback_at=tomorrow)
    disposition.record("+819022222222", "折り返し", callback_at=yesterday)
    disposition.record("+819033333333", "検討")
    result = callbacks.list_callbacks()
    # 両方の折り返しが含まれる
    assert len(result) == 2
    numbers = [c["to"] for c in result]
    assert "+819011111111" in numbers
    assert "+819022222222" in numbers


def test_callbacks_today():
    """当日分の折り返しだけ抽出できる。"""
    from tac import callbacks, disposition
    _tmp_stores()
    today = datetime.now(UTC) + timedelta(hours=CONFIG.call_hours_utc_offset)
    today_str = today.strftime("%Y-%m-%dT14:00")
    tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%dT10:00")
    disposition.record("+819011111111", "折り返し", callback_at=today_str)
    disposition.record("+819022222222", "折り返し", callback_at=tomorrow)
    result = callbacks.list_today()
    assert len(result) == 1
    assert result[0]["to"] == "+819011111111"


def test_callbacks_sorted_by_time():
    """折り返し予定は時刻順にソートされる。"""
    from tac import callbacks, disposition
    _tmp_stores()
    today = datetime.now(UTC) + timedelta(hours=CONFIG.call_hours_utc_offset)
    later = today.strftime("%Y-%m-%dT18:00")
    earlier = today.strftime("%Y-%m-%dT09:00")
    disposition.record("+819011111111", "折り返し", callback_at=later)
    disposition.record("+819022222222", "折り返し", callback_at=earlier)
    result = callbacks.list_today()
    assert result[0]["to"] == "+819022222222"
    assert result[1]["to"] == "+819011111111"


def test_callbacks_route():
    """GET /tac/calls/callbacks ルートが動く。"""
    import importlib
    try:
        server = importlib.import_module("tac.server")
    except Exception:
        return
    from tac import disposition
    _tmp_stores()
    today = datetime.now(UTC) + timedelta(hours=CONFIG.call_hours_utc_offset)
    today_str = today.strftime("%Y-%m-%dT14:00")
    disposition.record("+819012345678", "折り返し", callback_at=today_str)
    CONFIG.outbound_token = "tok-cb-test"
    client = server.app.test_client()
    try:
        assert client.get("/tac/calls/callbacks").status_code == 401
        r = client.get("/tac/calls/callbacks", query_string={"token": "tok-cb-test"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert len(body["callbacks"]) >= 1
    finally:
        CONFIG.outbound_token = ""
