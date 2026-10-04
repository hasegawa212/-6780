"""server.py の新エンドポイント（recording-status, amd-status, insight, ranked）のテスト。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("TAC_DRY_RUN", "1")
os.environ.setdefault("TAC_OUTBOUND_TOKEN", "test-token-42")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from tac.config import CONFIG
    monkeypatch.setattr(CONFIG, "calllog_file", str(tmp_path / "calls.jsonl"))
    monkeypatch.setattr(CONFIG, "follow_file", str(tmp_path / "follow.json"))
    monkeypatch.setattr(CONFIG, "queue_file", str(tmp_path / "queue.json"))
    monkeypatch.setattr(CONFIG, "dnc_file", str(tmp_path / "dnc.txt"))
    monkeypatch.setattr(CONFIG, "outbound_token", "test-token-42")
    yield


@pytest.fixture
def client():
    from tac.server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _auth():
    return {"X-TAC-Token": "test-token-42"}


class TestRecordingStatus:
    def test_completed_triggers_async(self, client, monkeypatch):
        from tac import transcribe
        called = []
        monkeypatch.setattr(transcribe, "process_recording_async",
                            lambda url, sid, room="": called.append((url, sid, room)))
        r = client.post("/tac/recording-status", data={
            "RecordingUrl": "https://api.twilio.com/rec/RE123",
            "CallSid": "CA999",
            "RecordingStatus": "completed",
            "ConferenceSid": "CF111",
        })
        assert r.status_code == 204
        assert len(called) == 1
        assert called[0][0] == "https://api.twilio.com/rec/RE123"

    def test_non_completed_ignored(self, client, monkeypatch):
        from tac import transcribe
        called = []
        monkeypatch.setattr(transcribe, "process_recording_async",
                            lambda url, sid, room="": called.append(1))
        r = client.post("/tac/recording-status", data={
            "RecordingUrl": "https://api.twilio.com/rec/RE123",
            "CallSid": "CA999",
            "RecordingStatus": "in-progress",
        })
        assert r.status_code == 204
        assert len(called) == 0


class TestAmdStatus:
    def test_machine_detected_logs(self, client, tmp_path):
        from tac.config import CONFIG
        r = client.post("/tac/amd-status", data={
            "CallSid": "CA111",
            "AnsweredBy": "machine_start",
            "To": "+81901234567",
        })
        assert r.status_code == 204
        from tac import calllog
        recs = calllog._all()
        amd = [x for x in recs if x.get("status") == "amd_machine"]
        assert len(amd) == 1
        assert amd[0]["answered_by"] == "machine_start"

    def test_human_answer_no_log(self, client):
        r = client.post("/tac/amd-status", data={
            "CallSid": "CA222",
            "AnsweredBy": "human",
            "To": "+81901234567",
        })
        assert r.status_code == 204
        from tac import calllog
        recs = calllog._all()
        assert not [x for x in recs if x.get("status") == "amd_machine"]


class TestCallsInsight:
    def test_returns_calls_with_insight(self, client, tmp_path):
        from tac import calllog
        calllog.append("outbound", "+81901111111", "dialed", room="r1", call_sid="CA1")
        calllog.append("outbound", "", "insight", call_sid="CA1", room="r1",
                        summary="物件問合せ", temperature="高", next_action="資料送付")
        r = client.get("/tac/calls/insight", headers=_auth())
        data = r.get_json()
        assert data["ok"]
        assert len(data["calls"]) >= 1
        c = data["calls"][0]
        assert c["ai_summary"] == "物件問合せ"
        assert c["ai_temperature"] == "高"

    def test_filter_by_to(self, client, tmp_path):
        from tac import calllog
        calllog.append("outbound", "+81901111111", "dialed", room="r1")
        calllog.append("outbound", "+81902222222", "dialed", room="r2")
        r = client.get("/tac/calls/insight?to=%2B81902222222", headers=_auth())
        data = r.get_json()
        assert data["ok"]
        assert all(c["to"] == "+81902222222" for c in data["calls"])


class TestCallsRanked:
    def test_ranked_returns_sorted(self, client, tmp_path):
        from tac import followup
        from tac.config import CONFIG
        followup.ingest([
            {"number": "+81901111111", "name": "テスト太郎", "record": "リスケしたい", "status": ""},
            {"number": "+81902222222", "name": "テスト花子", "record": "不在だった", "status": ""},
        ])
        r = client.get("/tac/calls/ranked", headers=_auth())
        data = r.get_json()
        assert data["ok"]
        assert data["count"] >= 1
        assert data["ranked"][0]["score"] >= data["ranked"][-1]["score"]

    def test_ranked_excludes_non_callable(self, client, tmp_path):
        from tac import followup
        followup.ingest([
            {"number": "+81909999999", "name": "停止者", "record": "拒否します", "status": ""},
        ])
        r = client.get("/tac/calls/ranked", headers=_auth())
        data = r.get_json()
        assert data["count"] == 0


class TestContinuousMode:
    """連続モードはクライアントサイドJS。ここではHTMLにトグルがあることを検証。"""
    def test_html_has_continuous_toggle(self, client):
        r = client.get("/tac/app")
        assert r.status_code == 200
        html = r.data.decode()
        assert "continuous-toggle" in html
        assert "連続モード" in html

    def test_html_has_ranked_button(self, client):
        r = client.get("/tac/app")
        html = r.data.decode()
        assert "queue-sort-ranked" in html
        assert "おすすめ順" in html

    def test_html_has_ai_insight_styles(self, client):
        r = client.get("/tac/app")
        html = r.data.decode()
        assert "ai-insight" in html
        assert "ai-temp" in html
