"""Webhook 冪等性（idempotency）のテスト（TDD）。

Twilio は同じ StatusCallback / AMD を重複・遅延・順序逆転で送ることがある。
処理済みの (CallSid+イベント) を短期記憶し、二重計上・二重 redirect を防ぐ。
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("TAC_DRY_RUN", "1")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from tac.config import CONFIG
    monkeypatch.setattr(CONFIG, "calllog_file", str(tmp_path / "calls.jsonl"))
    monkeypatch.setattr(CONFIG, "dnc_file", str(tmp_path / "dnc.txt"))
    monkeypatch.setattr(CONFIG, "idempotency_file", str(tmp_path / "idem.json"))
    yield


@pytest.fixture
def client():
    from tac.server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _read_log(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []


# ---- idempotency モジュール単体 ----

def test_seen_first_false_then_true():
    """初回は False（未処理）、2回目以降は True（処理済み）。"""
    from tac import idempotency
    assert idempotency.seen("CA1:status:completed") is False
    assert idempotency.seen("CA1:status:completed") is True


def test_empty_key_never_dedups():
    """キーが空なら常に False（誤って全部を抑止しない）。"""
    from tac import idempotency
    assert idempotency.seen("") is False
    assert idempotency.seen("") is False


def test_distinct_keys_independent():
    """別キーは互いに干渉しない。"""
    from tac import idempotency
    assert idempotency.seen("CAx:status:ringing") is False
    assert idempotency.seen("CAx:status:completed") is False  # 別イベントは別物


# ---- Webhook ハンドラの二重計上防止 ----

def test_call_status_dedup(client):
    """同一 CallSid+CallStatus の重複コールバックで outcome を二重計上しない。"""
    from tac.config import CONFIG
    data = {"CallSid": "CAdup1", "CallStatus": "completed", "To": "+819012345678"}
    client.post("/tac/autofollow/call-status", data=data)
    client.post("/tac/autofollow/call-status", data=data)  # 重複送信
    logs = _read_log(CONFIG.calllog_file)
    outcomes = [x for x in logs if str(x.get("status", "")).startswith("autofollow_")]
    assert len(outcomes) == 1


def test_amd_status_dedup(client):
    """同一 CallSid の AMD 重複で amd_machine を二重記録しない。"""
    from tac.config import CONFIG
    data = {"CallSid": "CAdup2", "AnsweredBy": "machine_start", "To": "+819012345678"}
    client.post("/tac/amd-status", data=data)
    client.post("/tac/amd-status", data=data)  # 重複送信
    logs = _read_log(CONFIG.calllog_file)
    amd = [x for x in logs if x.get("status") == "amd_machine"]
    assert len(amd) == 1


def test_call_status_distinct_statuses_both_recorded(client):
    """異なる CallStatus（ringing→completed）はそれぞれ1回ずつ記録する。"""
    from tac.config import CONFIG
    base = {"CallSid": "CAseq", "To": "+819012345678"}
    client.post("/tac/autofollow/call-status", data={**base, "CallStatus": "ringing"})
    client.post("/tac/autofollow/call-status", data={**base, "CallStatus": "completed"})
    logs = _read_log(CONFIG.calllog_file)
    outcomes = [x for x in logs if str(x.get("status", "")).startswith("autofollow_")]
    assert len(outcomes) == 2
