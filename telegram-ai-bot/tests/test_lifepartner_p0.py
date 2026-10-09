"""ライフパートナー P0（TDD）：名称の統一・質問のスキップ・第三者提供の明示・緊急停止。番号は架空。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TAC_OUTBOUND_TOKEN", "test-token-42")

from tac import autofollow, disclosure, kill_switch, outbound, realtime, survey  # noqa: E402
from tac.config import CONFIG  # noqa: E402

NUM = "+819011114444"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(CONFIG, "kill_switch_file", str(tmp_path / "kill_switch.json"))
    monkeypatch.setattr(CONFIG, "survey_question_set", "v1")  # 6 問の版（v1）の流れを確かめるテスト
    monkeypatch.setattr(CONFIG, "survey_company", "株式会社ジャパンマネジメント")
    monkeypatch.setattr(CONFIG, "survey_insurance_agency", "架空保険代理店株式会社")
    monkeypatch.setattr(CONFIG, "outbound_token", "test-token-42")
    yield


# ---- 1. 名称の統一：お客様が聞く言葉に「さくら」を残さない ---------------------------------------
def _customer_facing_texts() -> list[str]:
    import tac.server as server

    return [
        realtime.build_instructions(),
        realtime.build_greeting_response("", "")["response"]["instructions"],
        realtime.build_greeting_response("followup", "架空")["response"]["instructions"],
        disclosure.ai_text("株式会社サンプル不動産", "不動産売却の査定"),
        CONFIG.relay_welcome,
        server.GREETING,
        survey.opening(survey.new_session(NUM, "CA1")).say,
        realtime.refusal_twiml(),
        realtime.busy_twiml(),
    ]


def test_customer_facing_speech_never_says_the_old_name():
    for text in _customer_facing_texts():
        assert "さくら" not in text, text[:60]


def test_ai_introduces_itself_as_lifepartner_and_as_an_ai():
    assert "AI音声案内担当、ライフパートナー" in disclosure.ai_text("株式会社サンプル不動産", "不動産売却の査定")
    assert "AI音声案内担当、ライフパートナー" in realtime.build_instructions()
    assert "AI音声案内担当、ライフパートナー" in realtime.build_greeting_response("", "")["response"]["instructions"]
    assert "ライフパートナー" in CONFIG.relay_welcome


# ---- 2. 質問のスキップ（答えたくない質問は飛ばして続ける） --------------------------------------
@pytest.mark.parametrize("said", ["答えたくないです", "それはパスで", "飛ばしてください", "言いたくないです"])
def test_a_question_can_be_skipped_without_ending_the_survey(said):
    s = survey.new_session(NUM, "CA1")
    survey.opening(s)
    survey.advance(s, "はい")
    r = survey.advance(s, said)
    assert r.end is False
    first_question = next(iter(s["answers"]))
    assert s["answers"][first_question] == "SKIPPED"


def test_declining_at_the_start_is_still_a_decline():
    s = survey.new_session(NUM, "CA1")
    survey.opening(s)
    r = survey.advance(s, "答えたくないです")
    assert r.end is True and s["survey_consent"] == "DECLINED"


# ---- 3. 保険代理店への個人情報の提供（第三者提供）を明示して同意を取る ------------------------------
def test_insurance_consent_says_name_and_number_are_passed_to_the_agency():
    q = next(x for x in survey.questions() if x["id"] == "insurance_contact")["say"]
    assert "架空保険代理店株式会社" in q
    assert "お名前とお電話番号" in q and "お伝え" in q
    assert "よろしいでしょうか" in q


# ---- 4. 緊急停止（再起動なしで即時。すべての発信を止める） ---------------------------------------
def test_switch_is_released_when_nothing_was_ever_set():
    assert kill_switch.engaged() is False


def test_engaged_switch_blocks_every_outbound_call_before_the_network(monkeypatch):
    import urllib.request

    def no_network(*a, **k):
        raise AssertionError("緊急停止中にネットワークへ出てはいけない")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    kill_switch.engage(actor="operator", reason="テスト")
    res = outbound._create_call(to=NUM, twiml="<Response/>")
    assert res["ok"] is False and res.get("blocked") is True


def test_unreadable_switch_state_counts_as_engaged(tmp_path):
    Path(CONFIG.kill_switch_file).write_text("{not json", encoding="utf-8")
    assert kill_switch.engaged() is True


def test_release_records_who_and_when():
    kill_switch.engage(actor="operator", reason="障害")
    kill_switch.release(actor="operator")
    state = json.loads(Path(CONFIG.kill_switch_file).read_text(encoding="utf-8"))
    assert state["engaged"] is False
    assert state["history"][-1]["action"] == "release" and state["history"][-1]["actor"] == "operator"
    assert kill_switch.engaged() is False


def test_survey_and_autofollow_refuse_while_engaged(monkeypatch):
    kill_switch.engage(actor="operator", reason="テスト")
    monkeypatch.setattr(CONFIG, "survey_enabled", True)
    monkeypatch.setattr(CONFIG, "survey_caller_id", "+81300000777")
    entry = {"number": NUM, "lead_source": "x", "permission_scope": ["survey"], "permission_evidence": "y"}
    assert survey.can_call(entry) == (False, "KILL_SWITCH")
    monkeypatch.setattr(CONFIG, "company_name", "株式会社サンプル不動産")
    monkeypatch.setattr(CONFIG, "solicitation_product", "不動産売却の査定")
    monkeypatch.setattr(autofollow, "VOICE_STREAM_URL", "")
    res = autofollow.ivr_placer({"number": NUM, "name": "架空"})
    assert res["ok"] is False


@pytest.fixture
def client():
    from tac.server import app

    with app.test_client() as c:
        yield c


def test_kill_switch_api_requires_the_token_and_takes_effect_immediately(client):
    assert client.post("/tac/kill-switch", data={"engaged": "true"}).status_code in (401, 403)
    auth = {"X-TAC-Token": "test-token-42"}
    r = client.post("/tac/kill-switch", data={"engaged": "true", "reason": "障害"}, headers=auth)
    assert r.get_json()["engaged"] is True
    assert client.get("/tac/kill-switch", headers=auth).get_json()["engaged"] is True
    assert kill_switch.engaged() is True
    client.post("/tac/kill-switch", data={"engaged": "false"}, headers=auth)
    assert kill_switch.engaged() is False


def test_preflight_fails_go_live_while_the_switch_is_engaged():
    from tac import check

    kill_switch.engage(actor="operator", reason="テスト")
    rep = check.preflight()
    ks = next(c for c in rep["checks"] if c["key"] == "kill_switch")
    assert ks["ok"] is False and ks["level"] == "critical"
    assert rep["go_live"] is False
