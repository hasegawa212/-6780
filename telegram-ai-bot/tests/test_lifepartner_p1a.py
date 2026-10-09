"""ライフパートナー P1-a（TDD）：SQLite の CRM・監査ログ・通話の状態の永続化・Webhook の重複防止・同時架電数の上限。

番号はすべて架空。実際の電話は掛けない（発信は差し替える）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TAC_OUTBOUND_TOKEN", "test-token-42")

from tac import dnc, lp_db, survey, survey_questions, survey_store  # noqa: E402
from tac.config import CONFIG  # noqa: E402

NUM = "+819011115555"
AUTH = {"X-TAC-Token": "test-token-42"}
NOON_JST = datetime(2026, 10, 9, 3, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
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
    monkeypatch.setattr(CONFIG, "lp_db_file", str(tmp_path / "lifepartner.db"))
    monkeypatch.setattr(CONFIG, "survey_max_concurrent", 1)
    (tmp_path / "dnc.txt").write_text("", encoding="utf-8")
    (tmp_path / "survey_list.json").write_text(json.dumps([{
        "number": NUM, "name": "架空 花子", "lead_source": "資料請求フォーム",
        "permission_scope": ["survey"], "permission_evidence": "form-0001",
    }]), encoding="utf-8")
    monkeypatch.setattr(survey, "_now", lambda: NOON_JST)
    yield


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(CONFIG.lp_db_file)
    c.row_factory = sqlite3.Row
    return c


def _run(*said, sid="CA1"):
    s = survey.new_session(NUM, sid)
    survey.opening(s)
    for t in said:
        survey.advance(s, t)
    return s


# ---- 1. スキーマ ------------------------------------------------------------------------------
def test_schema_has_every_crm_table_and_uses_wal():
    lp_db.init()
    with _db() as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    for t in ("customers", "contact_permissions", "surveys", "survey_responses", "interest_profiles",
              "call_attempts", "appointments", "dnc_entries", "audit_logs", "call_sessions"):
        assert t in names, t
    assert mode == "wal"


def test_init_is_idempotent():
    lp_db.init()
    lp_db.init()
    with _db() as c:
        assert c.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] >= 1


# ---- 2. 結果の保存（顧客・同意・回答・関心） -------------------------------------------------------
def test_finished_survey_is_saved_to_the_crm_with_separate_consents():
    s = _run("はい", "あります", "しています", "持病があって不安です", "あります", "わかっています", "あります",
             "はい", "はい")
    survey_store.save_session(s, now=NOON_JST)
    with _db() as c:
        cust = c.execute("SELECT * FROM customers WHERE phone_e164=?", (NUM,)).fetchone()
        assert cust is not None
        perms = {r["purpose"]: r for r in c.execute(
            "SELECT * FROM contact_permissions WHERE customer_id=?", (cust["customer_id"],))}
        assert perms["survey"]["status"] == "GRANTED"
        assert perms["survey"]["evidence_reference"] == "call:CA1"
        assert perms["survey"]["disclosure_version"] == survey_questions.V1["disclosure_version"]
        assert perms["insurance_info"]["status"] == "GRANTED"
        assert perms["material_info"]["status"] == "GRANTED"
        rows = c.execute("SELECT * FROM survey_responses WHERE customer_id=?", (cust["customer_id"],)).fetchall()
        assert {r["question_id"] for r in rows} == {q for q, _ in survey._QUESTIONS}
        prof = c.execute("SELECT * FROM interest_profiles WHERE customer_id=?", (cust["customer_id"],)).fetchone()
        assert prof["insurance_review_interest"] == "YES"
        assert prof["financial_education_interest"] == "YES"
    # 発話の原文はどこにも残らない
    assert "持病".encode() not in Path(CONFIG.lp_db_file).read_bytes()


def test_skipped_answers_are_kept_as_skipped_not_dropped():
    s = _run("はい", "答えたくないです", "しています")
    s["outcome"] = "STOPPED"
    survey_store.save_session(s, now=NOON_JST)
    assert survey_store.get(NUM)["answers"]["info_access"] == "SKIPPED"
    with _db() as c:
        r = c.execute("SELECT * FROM survey_responses WHERE question_id='info_access'").fetchone()
        assert r["skipped"] == 1 and r["answer_value"] is None


def test_survey_answers_do_not_become_a_sales_consent():
    # 保険の見直しに「関心がある」と答えても、案内の同意を断ったら案内の許可は無い
    s = _run("はい", "あります", "しています", "あります", "あります", "わかっています", "あります", "いいえ")
    survey_store.save_session(s, now=NOON_JST)
    with _db() as c:
        st = c.execute("SELECT status FROM contact_permissions WHERE purpose='insurance_info'").fetchone()[0]
    assert st == "DECLINED"
    assert survey_store.handoffs() == []


# ---- 3. 撤回・DNC・監査ログ ----------------------------------------------------------------------
def test_withdraw_deletes_answers_revokes_consents_and_is_audited():
    s = _run("はい", "あります", "しています", "あります", "あります", "わかっています", "あります", "はい")
    survey_store.save_session(s, now=NOON_JST)
    assert survey_store.withdraw(NUM, now=NOON_JST) is True
    with _db() as c:
        assert c.execute("SELECT COUNT(*) FROM survey_responses").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM interest_profiles").fetchone()[0] == 0
        for r in c.execute("SELECT * FROM contact_permissions"):
            assert r["status"] == "WITHDRAWN" and r["revoked_at"]
        actions = [r["action"] for r in c.execute("SELECT action FROM audit_logs")]
    assert "withdraw" in actions


def test_audit_log_never_holds_the_phone_number():
    s = _run("はい", "もう電話しないでください")
    survey_store.save_session(s, now=NOON_JST)
    survey_store.withdraw(NUM, now=NOON_JST)
    with _db() as c:
        for r in c.execute("SELECT * FROM audit_logs"):
            text = " ".join(str(v) for v in tuple(r))
            assert "9011115555" not in text


def test_dnc_from_a_survey_call_is_mirrored_in_the_crm_and_dnc_txt_stays_the_source(client):
    _talk(client, "はい", "もう二度と電話しないでください")
    assert dnc.is_blocked(NUM) is True
    with _db() as c:
        row = c.execute("SELECT * FROM dnc_entries WHERE phone_e164=?", (NUM,)).fetchone()
    assert row is not None and row["source"] == "survey"


# ---- 4. 通話の状態の永続化と Webhook の重複防止 ---------------------------------------------------
@pytest.fixture
def client():
    from tac.server import app

    with app.test_client() as c:
        yield c


@pytest.fixture
def dialer(monkeypatch):
    import tac.outbound as outbound

    placed = []

    def fake(**kw):
        placed.append(kw)
        return {"ok": True, "sid": f"CA-dial-{len(placed)}"}

    monkeypatch.setattr(outbound, "_create_call", fake)
    return placed


def _step(client, text, *, sid="CA1", turn=None):
    q = "num=%2B819011115555" + (f"&turn={turn}" if turn is not None else "")
    return client.post(f"/tac/survey/step?{q}", data={"CallSid": sid, "SpeechResult": text}).get_data(as_text=True)


def _talk(client, *said, sid="CA1"):
    client.post("/tac/survey/voice?num=%2B819011115555", data={"CallSid": sid})
    out = []
    for i, t in enumerate(said, start=1):
        out.append(_step(client, t, sid=sid, turn=i))
    return out


def test_call_state_lives_in_the_database_not_in_memory(client):
    client.post("/tac/survey/voice?num=%2B819011115555", data={"CallSid": "CA1"})
    _step(client, "はい", turn=1)
    st = lp_db.load_session("CA1")
    assert st is not None and st["state"]["step"] == "info_access" and st["turn"] == 2
    import tac.server as server

    assert not hasattr(server, "_SURVEY_SESSIONS")


def test_action_url_carries_the_turn_number(client):
    xml = client.post("/tac/survey/voice?num=%2B819011115555", data={"CallSid": "CA1"}).get_data(as_text=True)
    assert "turn=1" in xml
    assert "turn=2" in _step(client, "はい", turn=1)


def test_a_duplicate_webhook_does_not_advance_the_survey_twice(client):
    client.post("/tac/survey/voice?num=%2B819011115555", data={"CallSid": "CA1"})
    first = _step(client, "はい", turn=1)
    again = _step(client, "はい", turn=1)  # Twilio の再送
    assert again == first
    assert lp_db.load_session("CA1")["state"]["step"] == "info_access"


def test_session_is_removed_when_the_call_ends(client):
    _talk(client, "いいえ")
    assert lp_db.load_session("CA1") is None


# ---- 5. 同時架電数の上限・発信 API の冪等性・状態コールバック -------------------------------------
def test_concurrency_limit_blocks_a_second_call_while_one_is_live(client, dialer, monkeypatch, tmp_path):
    (tmp_path / "survey_list.json").write_text(json.dumps([
        {"number": NUM, "lead_source": "x", "permission_scope": ["survey"], "permission_evidence": "y"},
        {"number": "+819011116666", "lead_source": "x", "permission_scope": ["survey"], "permission_evidence": "y"},
    ]), encoding="utf-8")
    assert client.post("/tac/survey/call", data={"number": NUM}, headers=AUTH).status_code == 200
    r = client.post("/tac/survey/call", data={"number": "+819011116666"}, headers=AUTH)
    assert r.status_code == 422 and r.get_json()["reason"] == "CONCURRENCY_LIMIT"
    assert len(dialer) == 1
    # 1 本目が終われば次を掛けられる
    client.post("/tac/survey/status", data={"CallSid": "CA-dial-1", "CallStatus": "completed"})
    assert client.post("/tac/survey/call", data={"number": "+819011116666"}, headers=AUTH).status_code == 200


def test_a_stuck_call_does_not_block_forever():
    lp_db.init()
    lp_db.start_attempt(NUM, idempotency_key="k-old", now=NOON_JST - timedelta(hours=2))
    assert lp_db.active_calls(now=NOON_JST) == 0


def test_same_idempotency_key_dials_only_once(client, dialer):
    h = {**AUTH, "Idempotency-Key": "req-1"}
    a = client.post("/tac/survey/call", data={"number": NUM}, headers=h)
    b = client.post("/tac/survey/call", data={"number": NUM}, headers=h)
    assert a.status_code == 200 and b.get_json().get("duplicate") is True
    assert len(dialer) == 1


def test_call_attempt_records_the_outcome_from_the_status_callback(client, dialer):
    client.post("/tac/survey/call", data={"number": NUM}, headers=AUTH)
    client.post("/tac/survey/status", data={"CallSid": "CA-dial-1", "CallStatus": "no-answer"})
    with _db() as c:
        r = c.execute("SELECT * FROM call_attempts WHERE call_id='CA-dial-1'").fetchone()
    assert r["call_status"] == "no-answer" and r["ended_at"]
    assert dialer[0]["call_status_callback"] == "https://tac.example.test/tac/survey/status"


def test_status_webhook_is_signature_checked():
    from tac import server

    assert "/tac/survey/status" in server._TWILIO_WEBHOOK_PATHS


def test_unreadable_crm_blocks_the_call(monkeypatch, tmp_path):
    bad = tmp_path / "dir-not-db"
    bad.mkdir()
    monkeypatch.setattr(CONFIG, "lp_db_file", str(bad))
    entry = {"number": NUM, "lead_source": "x", "permission_scope": ["survey"], "permission_evidence": "y"}
    assert survey.can_call(entry, now=NOON_JST) == (False, "STORE_UNAVAILABLE")


# ---- 6. 保存期間・バックアップ ---------------------------------------------------------------------
def test_old_responses_are_purged_after_the_retention_period():
    s = _run("はい", "あります")
    s["outcome"] = "STOPPED"
    survey_store.save_session(s, now=NOON_JST - timedelta(days=400))
    removed = lp_db.purge_expired(days=365, now=NOON_JST)
    assert removed >= 1
    with _db() as c:
        assert c.execute("SELECT COUNT(*) FROM survey_responses").fetchone()[0] == 0


def test_backup_produces_a_consistent_copy(tmp_path):
    s = _run("はい", "あります")
    s["outcome"] = "STOPPED"
    survey_store.save_session(s, now=NOON_JST)
    dest = tmp_path / "backup.db"
    info = lp_db.backup(str(dest))
    with sqlite3.connect(dest) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 1
    assert info["ok"] is True and len(info["sha256"]) == 64


# ---- 7. 回答の途中で電話を切られた ----------------------------------------------------------------
def test_hang_up_midway_keeps_the_answers_given_so_far_and_closes_the_session(client, dialer):
    client.post("/tac/survey/call", data={"number": NUM}, headers=AUTH)
    sid = "CA-dial-1"
    client.post("/tac/survey/voice?num=%2B819011115555", data={"CallSid": sid})
    _step(client, "はい", sid=sid, turn=1)
    _step(client, "あります", sid=sid, turn=2)
    client.post("/tac/survey/status", data={"CallSid": sid, "CallStatus": "completed"})
    assert lp_db.load_session(sid) is None
    rec = survey_store.get(NUM)
    assert rec["outcome"] == "HUNG_UP" and rec["answers"] == {"info_access": "YES"}
    with _db() as c:
        assert c.execute("SELECT outcome FROM call_attempts WHERE call_id=?", (sid,)).fetchone()[0] == "HUNG_UP"
    # 切られた相手には掛け直さない
    entry = {"number": NUM, "lead_source": "x", "permission_scope": ["survey"], "permission_evidence": "y"}
    assert survey.can_call(entry, now=NOON_JST) == (False, "ALREADY_SURVEYED")


def test_initiated_status_still_counts_as_a_live_call():
    lp_db.init()
    att = lp_db.start_attempt(NUM, idempotency_key="k1", now=NOON_JST)
    lp_db.finish_dial(att["attempt_id"], ok=True, call_id="CA-x")
    lp_db.update_call_status("CA-x", "initiated", now=NOON_JST)
    assert lp_db.active_calls(now=NOON_JST) == 1
