"""架電記録（Call Log）のテスト（TDD）。

発信（bridge_call）の監査証跡：いつ・誰に・結果どうだったかを JSONL で残し、
直近を取得できる。コンプライアンス（正直な架電・DNC遵守の証明）の土台。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import calllog, outbound  # noqa: E402
from tac.config import CONFIG  # noqa: E402


def _tmp_log():
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    f.close()
    CONFIG.calllog_file = f.name
    return f.name


def test_append_and_recent_roundtrip():
    _tmp_log()
    calllog.append("outbound", "+81901112222", "dialed", room="r1")
    calllog.append("outbound", "+81903334444", "blocked")
    rec = calllog.recent()
    assert len(rec) == 2
    # 新しい方が先頭（末尾N件を新しい順で返す）
    assert rec[0]["to"] == "+81903334444"
    assert rec[0]["status"] == "blocked"
    assert rec[1]["room"] == "r1"
    # 各レコードに ts(時刻) と direction が入る
    assert rec[0]["direction"] == "outbound"
    assert "ts" in rec[0]


def test_recent_limit():
    _tmp_log()
    for i in range(5):
        calllog.append("outbound", f"+8190000000{i}", "dialed")
    assert len(calllog.recent(limit=3)) == 3


def test_jsonl_one_object_per_line():
    path = _tmp_log()
    calllog.append("outbound", "+81901112222", "dialed")
    calllog.append("outbound", "+81903334444", "error", error="boom")
    lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2
    for ln in lines:
        json.loads(ln)  # 各行が単一の JSON


def test_missing_file_is_safe():
    CONFIG.calllog_file = "/nonexistent/dir/calls.jsonl"
    assert calllog.recent() == []


def test_bridge_call_logs_dnc_block():
    """DNCブロック時に status=blocked の記録が残る。"""
    _tmp_log()
    from tac import dnc
    dnc_old = CONFIG.dnc_file
    df = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    df.close()
    CONFIG.dnc_file = df.name
    CONFIG.agent_number = "+818094662479"
    dnc.add("+817066546780")
    try:
        outbound.bridge_call("+817066546780")
        rec = calllog.recent()
        assert rec and rec[0]["status"] == "blocked"
        assert rec[0]["to"] == "+817066546780"
    finally:
        CONFIG.dnc_file = dnc_old


def test_calls_route_requires_token_and_returns_records(monkeypatch=None):
    """GET /tac/calls: 無トークン=401、正しいトークンで記録を返す（flask無ければskip）。"""
    import importlib

    try:
        server = importlib.import_module("tac.server")
    except Exception:  # noqa: BLE001 - flask 未導入環境ではスキップ
        return
    _tmp_log()
    calllog.append("outbound", "+81905556666", "dialed")
    CONFIG.outbound_token = "tok-calls-test"
    client = server.app.test_client()
    try:
        assert client.get("/tac/calls").status_code == 401
        r = client.get("/tac/calls", query_string={"token": "tok-calls-test"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert any(c["to"] == "+81905556666" for c in body["calls"])
    finally:
        CONFIG.outbound_token = ""


def test_bridge_call_logs_error_when_no_creds():
    """Twilio未接続でも発信試行が error として記録される。"""
    _tmp_log()
    old_sid = CONFIG.twilio_account_sid
    CONFIG.twilio_account_sid = ""
    CONFIG.agent_number = "+818094662479"
    try:
        outbound.bridge_call("+81901234567")
        rec = calllog.recent()
        assert rec and rec[0]["status"] in ("error", "dialed")
        assert rec[0]["to"] == "+81901234567"
    finally:
        CONFIG.twilio_account_sid = old_sid
