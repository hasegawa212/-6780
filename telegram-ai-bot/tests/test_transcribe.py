"""tac.transcribe のテスト。

外部API（OpenAI/Anthropic/Twilio）はモックで差し替え。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("TAC_DRY_RUN", "1")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("TAC_CALLLOG_FILE", str(tmp_path / "calls.jsonl"))
    from tac.config import CONFIG
    monkeypatch.setattr(CONFIG, "calllog_file", str(tmp_path / "calls.jsonl"))
    monkeypatch.setattr(CONFIG, "openai_key", "test-key")
    monkeypatch.setattr(CONFIG, "anthropic_key", "test-key")
    monkeypatch.setattr(CONFIG, "operator_model", "claude-test")
    monkeypatch.setattr(CONFIG, "transcribe_model", "whisper-1")
    monkeypatch.setattr(CONFIG, "twilio_account_sid", "ACtest")
    monkeypatch.setattr(CONFIG, "twilio_auth_token", "authtest")
    yield


def test_process_recording_no_url():
    from tac import transcribe
    result = transcribe.process_recording("", "CA123")
    assert not result["ok"]
    assert result["reason"] == "no_recording_url"


def test_process_recording_transcribe_failure(monkeypatch):
    from tac import transcribe
    monkeypatch.setattr(transcribe, "_transcribe", lambda url: "")
    result = transcribe.process_recording("https://example.com/rec", "CA123", "room1")
    assert not result["ok"]
    assert result["reason"] == "transcribe_failed"


def test_process_recording_success(monkeypatch, tmp_path):
    from tac import calllog, transcribe

    monkeypatch.setattr(transcribe, "_transcribe", lambda url: "こんにちは、物件について聞きたいです")
    monkeypatch.setattr(transcribe, "_summarize", lambda t: {
        "summary": "物件問い合わせ",
        "temperature": "高",
        "next_action": "資料送付",
    })
    result = transcribe.process_recording("https://example.com/rec", "CA456", "room2")
    assert result["ok"]
    assert result["summary"] == "物件問い合わせ"
    assert result["temperature"] == "高"

    records = calllog._all()
    insight = [r for r in records if r.get("status") == "insight"]
    assert len(insight) == 1
    assert insight[0]["summary"] == "物件問い合わせ"
    assert insight[0]["call_sid"] == "CA456"


def test_transcribe_no_api_key(monkeypatch):
    from tac import transcribe
    from tac.config import CONFIG
    monkeypatch.setattr(CONFIG, "openai_key", "")
    assert transcribe._transcribe("https://example.com/rec") == ""


def test_summarize_no_api_key(monkeypatch):
    from tac import transcribe
    from tac.config import CONFIG
    monkeypatch.setattr(CONFIG, "anthropic_key", "")
    assert transcribe._summarize("テスト") == {}


def test_summarize_no_transcript():
    from tac import transcribe
    assert transcribe._summarize("") == {}


def test_process_recording_async(monkeypatch):
    from tac import transcribe

    called = []
    monkeypatch.setattr(transcribe, "process_recording",
                        lambda url, sid, room="": called.append((url, sid, room)) or {"ok": True})
    transcribe.process_recording_async("https://rec", "CA789", "r")
    import time
    time.sleep(0.1)
    assert len(called) == 1
    assert called[0] == ("https://rec", "CA789", "r")
