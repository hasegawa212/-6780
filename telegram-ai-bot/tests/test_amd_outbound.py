"""AMD（留守電判定）関連の outbound テスト。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("TAC_DRY_RUN", "1")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from tac.config import CONFIG
    monkeypatch.setattr(CONFIG, "calllog_file", str(tmp_path / "calls.jsonl"))
    monkeypatch.setattr(CONFIG, "dnc_file", str(tmp_path / "dnc.txt"))
    monkeypatch.setattr(CONFIG, "caller_id", "+16590000000")
    monkeypatch.setattr(CONFIG, "agent_number", "+818090000000")
    monkeypatch.setattr(CONFIG, "twilio_account_sid", "ACtest")
    monkeypatch.setattr(CONFIG, "twilio_auth_token", "authtest")
    monkeypatch.setattr(CONFIG, "outbound_token", "tok")
    monkeypatch.setattr(CONFIG, "disclosure_enabled", False)
    monkeypatch.setattr(CONFIG, "enforce_call_hours", False)
    monkeypatch.setattr(CONFIG, "daily_call_cap", 0)
    yield


def test_amd_off_no_machine_detection(monkeypatch):
    """AMD OFF のとき、_create_call に MachineDetection が付かない。"""
    from tac import outbound
    from tac.config import CONFIG
    monkeypatch.setattr(CONFIG, "amd_enabled", False)
    monkeypatch.setattr(CONFIG, "public_base_url", "")

    created = []

    def fake_create(*, to, twiml, amd=False, status_callback=""):
        created.append({"to": to, "amd": amd, "status_callback": status_callback})
        return {"ok": True, "sid": "CAfake", "to": to, "status": "queued"}

    monkeypatch.setattr(outbound, "_create_call", fake_create)
    outbound.bridge_call("+81901234567")
    target = created[0]
    assert target["amd"] is False


def test_amd_on_adds_machine_detection(monkeypatch):
    """AMD ON のとき、相手レッグに amd=True が渡る。"""
    from tac import outbound
    from tac.config import CONFIG
    monkeypatch.setattr(CONFIG, "amd_enabled", True)
    monkeypatch.setattr(CONFIG, "public_base_url", "https://test.fly.dev")

    created = []

    def fake_create(*, to, twiml, amd=False, status_callback=""):
        created.append({"to": to, "amd": amd, "status_callback": status_callback})
        return {"ok": True, "sid": "CAfake", "to": to, "status": "queued"}

    monkeypatch.setattr(outbound, "_create_call", fake_create)
    outbound.bridge_call("+81901234567")
    target = created[0]
    assert target["amd"] is True
    assert "amd-status" in target["status_callback"]
    agent = created[1]
    assert agent["amd"] is False


def test_recording_callback_in_conf_twiml(monkeypatch):
    """録音ON + public_base_url ありのとき、Conference TwiML に recordingStatusCallback が入る。"""
    from tac import outbound
    from tac.config import CONFIG
    monkeypatch.setattr(CONFIG, "record_calls", True)
    monkeypatch.setattr(CONFIG, "public_base_url", "https://test.fly.dev")
    twiml = outbound._conf_twiml("room123", starter=True)
    assert "recordingStatusCallback" in twiml
    assert "/tac/recording-status" in twiml


def test_no_recording_callback_without_public_url(monkeypatch):
    """public_base_url 未設定のときは recordingStatusCallback が付かない。"""
    from tac import outbound
    from tac.config import CONFIG
    monkeypatch.setattr(CONFIG, "record_calls", True)
    monkeypatch.setattr(CONFIG, "public_base_url", "")
    twiml = outbound._conf_twiml("room123", starter=True)
    assert "recordingStatusCallback" not in twiml
    assert "record-from-start" in twiml
