"""ライフパートナー P1-c（TDD）：管理画面・閲覧の権限・少人数の集計の秘匿・閲覧の監査ログ。

番号・人物はすべて架空。トークンは平文でなくハッシュで設定する。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import dnc, lp_admin, lp_db, survey, survey_questions, survey_store  # noqa: E402
from tac.config import CONFIG  # noqa: E402

NOON_JST = datetime(2026, 10, 9, 3, 0, tzinfo=UTC)
V2 = survey_questions.V2


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


ADMIN, VIEWER, ANALYST = "adm-token-111", "view-token-222", "ana-token-333"


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    monkeypatch.setattr(CONFIG, "outbound_token", "test-token-42")
    monkeypatch.setattr(CONFIG, "survey_question_set", "v2")
    monkeypatch.setattr(CONFIG, "survey_company", "株式会社ジャパンマネジメント")
    monkeypatch.setattr(CONFIG, "survey_insurance_agency", "架空保険代理店株式会社")
    monkeypatch.setattr(CONFIG, "survey_file", str(tmp_path / "survey.json"))
    monkeypatch.setattr(CONFIG, "survey_list_file", str(tmp_path / "survey_list.json"))
    monkeypatch.setattr(CONFIG, "dnc_file", str(tmp_path / "dnc.txt"))
    monkeypatch.setattr(CONFIG, "lp_min_cell", 5)
    monkeypatch.setattr(CONFIG, "lp_users", f"yamada:admin:{_h(ADMIN)},sato:viewer:{_h(VIEWER)},"
                                            f"suzuki:analyst:{_h(ANALYST)}")
    (tmp_path / "dnc.txt").write_text("", encoding="utf-8")
    yield


@pytest.fixture
def client():
    from tac.server import app

    with app.test_client() as c:
        yield c


def _num(i: int) -> str:
    return f"+8190222{i:05d}"


def _write_list(n: int, *, missing_evidence: int = 0):
    rows = []
    for i in range(n):
        rows.append({"number": _num(i), "lead_source": "資料請求フォーム", "permission_scope": ["survey"],
                     "permission_evidence": "" if i < missing_evidence else f"form-{i}"})
    Path(CONFIG.survey_list_file).write_text(json.dumps(rows), encoding="utf-8")


def _survey(i: int, *, insurance: str = "あります", education: str = "ないです", consent: str = "はい"):
    s = survey.new_session(_num(i), f"CA{i}")
    survey.opening(s)
    survey.advance(s, "はい")
    answers = {
        "life_satisfaction": "まあまあ", "daily_burden": "特にないです", "lifestyle_review_interest": "あります",
        "living_cost_burden": "ときどき", "spending_tracked": "はい", "fixed_cost_interest": "あります",
        "info_access": "あります", "fraud_awareness_interest": "ないです", "future_money_worry": "あります",
        "education_interest": education, "insurance_understanding": "はい", "premium_burden": "あまりない",
        "insurance_review_interest": insurance, "future_security": "不安", "retirement_interest": "あります",
        "life_priority": "家族",
    }
    while s["step"] in answers:
        survey.advance(s, answers[s["step"]])
    while s["step"] != "CLOSED":
        survey.advance(s, consent)
    survey_store.save_session(s, now=NOON_JST)
    return s


def _get(client, path, token):
    return client.get(path, headers={"X-LP-Token": token} if token else {})


# ---- 1. 認証と権限 ---------------------------------------------------------------------------
def test_summary_requires_a_known_token(client):
    assert _get(client, "/tac/lifepartner/api/summary", "").status_code == 401
    assert _get(client, "/tac/lifepartner/api/summary", "wrong").status_code == 401
    assert _get(client, "/tac/lifepartner/api/summary", ANALYST).status_code == 200


def test_analyst_sees_aggregates_but_not_individual_answers(client):
    assert _get(client, "/tac/lifepartner/api/responses", ANALYST).status_code == 403
    assert _get(client, "/tac/lifepartner/api/responses", VIEWER).status_code == 200
    assert _get(client, "/tac/lifepartner/api/responses", ADMIN).status_code == 200


def test_outbound_token_acts_as_admin(client):
    r = client.get("/tac/lifepartner/api/responses", headers={"X-TAC-Token": "test-token-42"})
    assert r.status_code == 200


def test_malformed_user_config_grants_nobody(monkeypatch, client):
    monkeypatch.setattr(CONFIG, "lp_users", f"broken-entry,yamada:superuser:{_h(ADMIN)},sato:viewer:nothex")
    assert _get(client, "/tac/lifepartner/api/summary", ADMIN).status_code == 401
    assert _get(client, "/tac/lifepartner/api/summary", VIEWER).status_code == 401


def test_who_am_i_returns_the_role_but_never_a_token(client):
    r = _get(client, "/tac/lifepartner/api/me", VIEWER).get_json()
    assert r == {"ok": True, "name": "sato", "role": "viewer"}


# ---- 2. 集計 -------------------------------------------------------------------------------
def test_summary_counts_the_funnel(client):
    _write_list(8, missing_evidence=1)
    dnc.add(_num(7))
    for i in range(6):
        att = lp_db.start_attempt(_num(i), idempotency_key=f"k{i}", now=NOON_JST)
        lp_db.finish_dial(att["attempt_id"], ok=True, call_id=f"CA{i}")
        lp_db.update_call_status(f"CA{i}", "completed", now=NOON_JST)
    for i in range(6):
        _survey(i)
        lp_db.set_outcome(f"CA{i}", "COMPLETED")
    att = lp_db.start_attempt(_num(6), idempotency_key="k6", now=NOON_JST)
    lp_db.finish_dial(att["attempt_id"], ok=False, result={"ok": False})
    lp_db.mirror_dnc(_num(7), source="survey")

    sm = _get(client, "/tac/lifepartner/api/summary", ANALYST).get_json()["summary"]
    assert sm["targets"] == 8
    assert sm["permitted"] == 6  # 証跡が無い 1 件と DNC の 1 件を除く
    assert sm["dialed"] == 6
    assert sm["answered"] == 6
    assert sm["started"]["value"] == 6
    assert sm["completed"]["value"] == 6
    assert sm["dnc"] == 1
    assert sm["errors"] == 1
    assert sm["appointments"] == 0
    assert sm["interests"]["insurance_review_interest"]["value"] == 6
    assert sm["information_requested"]["insurance_info"]["value"] == 6


def test_campaign_list_shows_the_survey_and_its_version(client):
    _survey(0)
    sm = _get(client, "/tac/lifepartner/api/summary", ANALYST).get_json()
    assert {"survey_id": "lifepartner", "survey_version": V2["version"]}.items() <= sm["campaigns"][0].items()


# ---- 3. 少人数の集計の秘匿 ---------------------------------------------------------------------
def test_small_counts_are_suppressed(client):
    for i in range(3):
        _survey(i)
    sm = _get(client, "/tac/lifepartner/api/summary", ADMIN).get_json()["summary"]
    cell = sm["interests"]["insurance_review_interest"]
    assert cell["value"] is None and cell["suppressed"] is True and cell["display"] == "5未満"


def test_a_count_that_would_reveal_the_rest_is_suppressed_too(client):
    # 6 人中 5 人が「関心あり」→ 残り 1 人が「関心なし」と分かってしまうので伏せる
    for i in range(5):
        _survey(i)
    _survey(5, insurance="ないです")
    sm = _get(client, "/tac/lifepartner/api/summary", ANALYST).get_json()["summary"]
    assert sm["interests"]["insurance_review_interest"]["suppressed"] is True
    assert sm["interests"]["lifestyle_interest"]["value"] == 6  # 全員：伏せる必要がない


def test_zero_is_shown_as_zero(client):
    for i in range(6):
        _survey(i)
    sm = _get(client, "/tac/lifepartner/api/summary", ANALYST).get_json()["summary"]
    assert sm["information_requested"]["material_info"] == {"value": 0, "suppressed": False, "display": "0"}


# ---- 4. 個別の回答の閲覧（権限・マスク・監査） ------------------------------------------------------
def test_individual_responses_mask_the_phone_number(client):
    _survey(0)
    body = _get(client, "/tac/lifepartner/api/responses", VIEWER).get_data(as_text=True)
    assert _num(0) not in body and _num(0)[1:] not in body
    rows = json.loads(body)["responses"]
    assert rows[0]["phone"] == "****" + _num(0)[-4:]
    assert rows[0]["answers"]["life_satisfaction"] == "NEUTRAL"


def test_every_view_is_audited_with_the_actor_and_without_the_number(client):
    _survey(0)
    _get(client, "/tac/lifepartner/api/summary", ANALYST)
    _get(client, "/tac/lifepartner/api/responses", VIEWER)
    _get(client, "/tac/lifepartner/api/responses", ANALYST)  # 拒否も記録する
    with sqlite3.connect(CONFIG.lp_db_file) as c:
        rows = c.execute("SELECT actor, action, metadata FROM audit_logs WHERE action LIKE 'admin_%'").fetchall()
    acts = {(a, b) for a, b, _ in rows}
    assert ("suzuki", "admin_view_summary") in acts
    assert ("sato", "admin_view_responses") in acts
    assert ("suzuki", "admin_denied") in acts
    assert all(_num(0)[4:] not in (m or "") for _, _, m in rows)


# ---- 5. 画面 ---------------------------------------------------------------------------------
def test_dashboard_page_is_branded_and_holds_no_data(client):
    _survey(0)
    r = client.get("/tac/lifepartner/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "ライフパートナー" in html
    assert "さくら" not in html and _num(0) not in html and "test-token-42" not in html
    assert "sessionStorage" in html and "localStorage" not in html
    for label in ("対象者数", "架電許可確認済み", "発信件数", "応答件数", "調査開始", "調査完了", "回答拒否",
                  "DNC", "生活改善", "家計見直し", "金融教育", "保険見直し", "案内を希望", "相談予約", "エラー"):
        assert label in html, label


def test_dashboard_page_is_not_cached(client):
    r = client.get("/tac/lifepartner/")
    assert "no-store" in r.headers.get("Cache-Control", "")


def test_api_responses_are_not_cached(client):
    r = _get(client, "/tac/lifepartner/api/summary", ANALYST)
    assert "no-store" in r.headers.get("Cache-Control", "")


def test_hash_helper_prints_a_sha256(capsys):
    lp_admin.main(["hash", "some-token"])
    assert capsys.readouterr().out.strip() == _h("some-token")
