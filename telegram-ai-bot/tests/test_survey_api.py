"""生活意識調査モードの Webhook・操作 API（TDD）。実発信はしない（_create_call をスタブ）。番号は架空。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("TAC_OUTBOUND_TOKEN", "test-token-42")

NUM = "+819011113333"
AUTH = {"X-TAC-Token": "test-token-42"}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from tac import survey
    from tac.config import CONFIG

    monkeypatch.setattr(CONFIG, "outbound_token", "test-token-42")
    monkeypatch.setattr(CONFIG, "verify_twilio_signature", False)
    monkeypatch.setattr(CONFIG, "public_base_url", "https://tac.example.test")
    monkeypatch.setattr(CONFIG, "survey_enabled", True)
    monkeypatch.setattr(CONFIG, "survey_question_set", "v1")  # 6 問の版（v1）の流れを確かめるテスト
    monkeypatch.setattr(CONFIG, "survey_company", "株式会社ジャパンマネジメント")
    monkeypatch.setattr(CONFIG, "survey_caller_id", "+81300000777")
    monkeypatch.setattr(CONFIG, "survey_insurance_agency", "架空保険代理店株式会社")
    monkeypatch.setattr(CONFIG, "survey_file", str(tmp_path / "survey.json"))
    monkeypatch.setattr(CONFIG, "survey_list_file", str(tmp_path / "survey_list.json"))
    monkeypatch.setattr(CONFIG, "dnc_file", str(tmp_path / "dnc.txt"))
    monkeypatch.setattr(CONFIG, "calllog_file", str(tmp_path / "calls.jsonl"))
    (tmp_path / "dnc.txt").write_text("", encoding="utf-8")
    (tmp_path / "survey_list.json").write_text(json.dumps([{
        "number": NUM, "lead_source": "資料請求フォーム", "permission_scope": ["survey"],
        "permission_evidence": "form-0001",
    }]), encoding="utf-8")
    # 時刻は正午（JST）に固定
    from datetime import UTC, datetime
    monkeypatch.setattr(survey, "_now", lambda: datetime(2026, 10, 9, 3, 0, tzinfo=UTC))
    yield


@pytest.fixture
def client():
    from tac.server import app

    with app.test_client() as c:
        yield c


@pytest.fixture
def dialer(monkeypatch):
    import tac.outbound as outbound

    placed = []
    monkeypatch.setattr(outbound, "_create_call", lambda **kw: placed.append(kw) or {"ok": True, "sid": "CA9"})
    return placed


def _talk(client, *said, sid="CA1"):
    r = client.post("/tac/survey/voice?num=%2B819011113333", data={"CallSid": sid})
    replies = [r.get_data(as_text=True)]
    for text in said:
        r = client.post("/tac/survey/step?num=%2B819011113333", data={"CallSid": sid, "SpeechResult": text})
        replies.append(r.get_data(as_text=True))
    return replies


def test_call_api_requires_the_token_and_uses_the_survey_caller_id(client, dialer):
    assert client.post("/tac/survey/call", data={"number": NUM}).status_code in (401, 403)
    r = client.post("/tac/survey/call", data={"number": NUM}, headers=AUTH)
    assert r.get_json()["ok"] is True
    assert dialer[0]["to"] == NUM
    assert dialer[0]["from_"] == "+81300000777"
    assert "/tac/survey/voice" in dialer[0]["twiml"]


def test_call_api_refuses_numbers_not_on_the_permitted_survey_list(client, dialer):
    r = client.post("/tac/survey/call", data={"number": "+819099998888"}, headers=AUTH)
    assert r.status_code == 422 and r.get_json()["reason"] == "NOT_ON_SURVEY_LIST"
    assert dialer == []


def test_call_api_refuses_when_disabled(client, dialer, monkeypatch):
    from tac.config import CONFIG

    monkeypatch.setattr(CONFIG, "survey_enabled", False)
    r = client.post("/tac/survey/call", data={"number": NUM}, headers=AUTH)
    assert r.status_code == 422 and r.get_json()["reason"] == "DISABLED"
    assert dialer == []


def test_full_conversation_records_consents_and_hands_off_insurance(client):
    replies = _talk(client, "はい", "あります", "しています", "あります", "ありません", "わかっています",
                    "あります", "はい、お願いします")
    assert "ライフパートナー" in replies[0] and "<Gather" in replies[0]
    assert replies[-1].rstrip().endswith("<Hangup/></Response>")
    r = client.get("/tac/survey/handoffs", headers=AUTH).get_json()
    assert [h["number"] for h in r["handoffs"]] == [NUM]
    s = client.get("/tac/survey/summary", headers=AUTH).get_json()["summary"]
    assert s["total"] == 1 and s["insurance_contact_granted"] == 1


def test_do_not_call_request_registers_dnc(client):
    _talk(client, "はい", "もう電話しないでください")
    from tac import dnc

    assert dnc.is_blocked(NUM) is True


def test_unknown_call_session_ends_politely(client):
    r = client.post("/tac/survey/step?num=%2B819011113333", data={"CallSid": "CA-unknown", "SpeechResult": "はい"})
    xml = r.get_data(as_text=True)
    assert "<Hangup/>" in xml and "<Gather" not in xml


def test_withdraw_api_clears_the_answers(client):
    _talk(client, "はい", "あります", "しています", "あります", "ありません", "わかっています", "あります", "はい")
    r = client.post("/tac/survey/withdraw", data={"number": NUM}, headers=AUTH)
    assert r.get_json()["ok"] is True
    assert client.get("/tac/survey/handoffs", headers=AUTH).get_json()["handoffs"] == []


def test_survey_webhooks_are_signature_checked():
    from tac import server

    for path in ("/tac/survey/voice", "/tac/survey/step"):
        assert path in server._TWILIO_WEBHOOK_PATHS


def test_raw_speech_is_not_written_anywhere(client, tmp_path):
    _talk(client, "はい", "あります", "しています", "持病があって不安です", "ありません", "わかっています", "ないです")
    from tac.config import CONFIG

    for f in (CONFIG.survey_file, CONFIG.calllog_file):
        if Path(f).exists():
            assert "持病" not in Path(f).read_text(encoding="utf-8")
