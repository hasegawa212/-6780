"""ライフパートナー P1-b（TDD）：A〜E の 5 分野の質問（版つき）・段階の回答・言い換え・忙しいの尊重・関心の分類。

番号はすべて架空。発話の原文は保存しない。
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import survey, survey_questions, survey_store  # noqa: E402
from tac.config import CONFIG  # noqa: E402

NUM = "+819011117777"
NOON_JST = datetime(2026, 10, 9, 3, 0, tzinfo=UTC)
V2 = survey_questions.V2


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    monkeypatch.setattr(CONFIG, "survey_question_set", "v2")
    monkeypatch.setattr(CONFIG, "survey_company", "株式会社ジャパンマネジメント")
    monkeypatch.setattr(CONFIG, "survey_insurance_agency", "架空保険代理店株式会社")
    monkeypatch.setattr(CONFIG, "survey_purpose", "金融教育の資料づくりと、ご希望の方への情報提供")
    monkeypatch.setattr(CONFIG, "survey_file", str(tmp_path / "survey.json"))
    yield


def _start():
    s = survey.new_session(NUM, "CA1")
    survey.opening(s)
    return s


def _ids():
    return [q["id"] for q in V2["questions"]]


# 全問に「はい」寄りで答える（段階・分類の質問にはそれぞれ当てはまる言い方で）
_ANSWER_ALL = {
    "life_satisfaction": "まあまあですね", "daily_burden": "毎月の支払いが多いことですね",
    "lifestyle_review_interest": "あります", "living_cost_burden": "ときどきあります",
    "spending_tracked": "はい", "fixed_cost_interest": "あります", "info_access": "あります",
    "fraud_awareness_interest": "あります", "future_money_worry": "あります", "education_interest": "あります",
    "insurance_understanding": "あまりないですね", "premium_burden": "あまりないです",
    "insurance_review_interest": "あります", "future_security": "どちらともいえないです",
    "retirement_interest": "あります", "life_priority": "家族ですね",
}


def _answer_through(s, overrides=None):
    answers = {**_ANSWER_ALL, **(overrides or {})}
    r = None
    while s["step"] in answers:
        r = survey.advance(s, answers[s["step"]])
    return r


# ---- 1. 冒頭と会話の流れ（仕様の会話例） --------------------------------------------------------
def test_opening_names_the_company_and_ai_and_says_it_is_optional():
    say = survey.opening(survey.new_session(NUM, "CA1")).say
    assert "株式会社ジャパンマネジメントのAI音声案内担当、ライフパートナーです" in say
    assert "生活意識調査" in say and "任意" in say and "3分" in say
    # 案内（保険）を予定しているので、冒頭で実施主体と目的を伝える
    assert "架空保険代理店株式会社" in say and "保険の見直しのご案内" in say
    assert "金融教育の資料づくり" in say


def test_the_example_conversation_from_the_spec():
    s = _start()
    r = survey.advance(s, "はい")
    assert s["step"] == "life_satisfaction" and "満足" in r.say
    r = survey.advance(s, "まあまあですね")
    assert s["answers"]["life_satisfaction"] == "NEUTRAL"
    assert r.say.startswith("ありがとうございます。") and s["step"] == "daily_burden"
    r = survey.advance(s, "毎月の支払いが多いことですね")
    assert s["answers"]["daily_burden"] == "MONEY"
    assert r.say.startswith("毎月の支出が気になっていらっしゃるのですね。")


def test_every_question_asks_one_thing_at_a_time():
    for q in V2["questions"]:
        assert q["say"].count("？") == 1, q["id"]


def test_all_five_areas_are_covered():
    assert {q["area"] for q in V2["questions"]} == {"A", "B", "C", "D", "E"}


def _all_texts() -> str:
    s = survey.new_session(NUM, "CA1")
    texts = [survey.opening(s).say]
    for q in V2["questions"]:
        texts += [q["say"], q.get("rephrase", "")]
    texts += [q["say"] for q in survey.questions()]
    texts += [survey.closing_text(True, True), survey.closing_text(False, False)]
    return "".join(texts)


def test_no_real_estate_or_fp_and_no_health_or_income_questions():
    blob = _all_texts()
    for word in ("不動産", "物件", "売却", "査定", "住宅", "ファイナンシャル", "FP",
                 "病気", "病歴", "健康状態", "持病", "年収", "収入", "月収", "貯金額", "資産額", "さくら"):
        assert word not in blob, word


def test_no_fp_booking_route_exists():
    from tac.server import app

    for rule in app.url_map.iter_rules():
        assert "fp" not in rule.rule.lower().split("/"), rule.rule


# ---- 2. 段階・分類の回答 -----------------------------------------------------------------------
@pytest.mark.parametrize("qid,said,value", [
    ("life_satisfaction", "満足していません", "UNSATISFIED"),
    ("life_satisfaction", "まあまあです", "NEUTRAL"),
    ("life_satisfaction", "満足です", "SATISFIED"),
    ("life_satisfaction", "よくわからないです", "UNKNOWN"),
    ("living_cost_burden", "あまりないです", "RARELY"),
    ("living_cost_burden", "ときどきあります", "SOMETIMES"),
    ("living_cost_burden", "よくあります", "OFTEN"),
    ("living_cost_burden", "よくわからないです", "UNKNOWN"),
    ("daily_burden", "お金がないです", "MONEY"),
    ("daily_burden", "特にないです", "NONE"),
    ("daily_burden", "健康のことです", "OTHER"),
    ("future_security", "不安はないです", "SECURE"),
    ("future_security", "不安ですね", "WORRIED"),
    ("life_priority", "家族です", "FAMILY"),
])
def test_scale_and_choice_answers_are_classified(qid, said, value):
    q = next(x for x in V2["questions"] if x["id"] == qid)
    assert survey_questions.classify_answer(q, said) == value


def test_unclear_answer_is_rephrased_once_then_recorded_as_unknown():
    s = _start()
    survey.advance(s, "はい")
    q = V2["questions"][0]
    r = survey.advance(s, "うーん")
    assert r.say == q["rephrase"] and s["step"] == "life_satisfaction"
    r = survey.advance(s, "うーん")
    assert s["answers"]["life_satisfaction"] == "UNKNOWN" and s["step"] == "daily_burden"


def test_not_insured_skips_the_premium_question():
    s = _start()
    survey.advance(s, "はい")
    while s["step"] != "insurance_understanding":
        survey.advance(s, _ANSWER_ALL[s["step"]])
    survey.advance(s, "保険には入っていないです")
    assert s["answers"]["insurance_understanding"] == "NOT_INSURED"
    assert s["step"] == "insurance_review_interest"
    assert "premium_burden" not in s["answers"]


def test_a_question_can_be_skipped():
    s = _start()
    survey.advance(s, "はい")
    r = survey.advance(s, "それは答えたくないです")
    assert s["answers"]["life_satisfaction"] == "SKIPPED" and r.end is False


# ---- 3. 忙しい・途中終了・営業への切り替えの防止 ------------------------------------------------------
def test_busy_at_the_start_ends_politely_without_asking_anything():
    s = _start()
    r = survey.advance(s, "今ちょっと忙しいので")
    assert r.end is True and s["outcome"] == "BUSY" and s["answers"] == {}
    assert "お忙しいところ" in r.say


def test_busy_midway_keeps_the_answers_so_far():
    s = _start()
    survey.advance(s, "はい")
    survey.advance(s, "まあまあです")
    r = survey.advance(s, "ごめんなさい、今は無理です")
    assert r.end is True and s["outcome"] == "BUSY"
    assert s["answers"] == {"life_satisfaction": "NEUTRAL"}


def test_a_real_estate_remark_does_not_switch_the_call_to_sales():
    s = _start()
    survey.advance(s, "はい")
    survey.advance(s, "まあまあです")
    r = survey.advance(s, "家を売りたいと思ってるんだけど")
    assert s["answers"]["daily_burden"] == "OTHER"
    for word in ("不動産", "売却", "査定", "物件"):
        assert word not in r.say


# ---- 4. 調査の完了と、案内の同意の分離 ------------------------------------------------------------
def test_survey_complete_is_announced_before_any_information_permission():
    s = _start()
    survey.advance(s, "はい")
    r = _answer_through(s)
    assert s["step"] == "INSURANCE_CONSENT"
    assert r.say.startswith("アンケートは以上です。")
    assert "お名前とお電話番号" in r.say


def test_no_interest_means_no_information_question_at_all():
    s = _start()
    survey.advance(s, "はい")
    r = _answer_through(s, {"insurance_review_interest": "ないです", "education_interest": "ないです"})
    assert r.end is True and s["outcome"] == "COMPLETED"
    assert "アンケートは以上です" in r.say
    assert s["insurance_contact_consent"] == "NOT_ASKED" and s["material_contact_consent"] == "NOT_ASKED"


def test_spec_state_names_are_exposed():
    s = _start()
    assert survey.state_name(s) == "SURVEY_PERMISSION"
    survey.advance(s, "はい")
    assert survey.state_name(s) == "QUESTION"
    _answer_through(s)
    assert survey.state_name(s) == "INFORMATION_PERMISSION"
    s2 = _start()
    survey.advance(s2, "電話しないでください")
    assert survey.state_name(s2) == "DNC"
    s3 = _start()
    survey.advance(s3, "結構です")
    assert survey.state_name(s3) == "DECLINED"


# ---- 5. 版・保存・関心の分類 -------------------------------------------------------------------
def test_a_call_keeps_the_question_set_it_started_with(monkeypatch):
    s = _start()
    monkeypatch.setattr(CONFIG, "survey_question_set", "v1")
    survey.advance(s, "はい")
    assert s["step"] == "life_satisfaction"


def test_unknown_set_name_falls_back_to_the_default():
    assert survey_questions.get_set("nope")["set"] == survey_questions.DEFAULT_SET


def test_answers_versions_and_interests_are_saved_to_the_crm():
    s = _start()
    survey.advance(s, "はい")
    _answer_through(s)
    survey.advance(s, "はい")  # 保険の案内
    survey.advance(s, "はい")  # 資料の案内
    assert s["outcome"] == "COMPLETED"
    survey_store.save_session(s, now=NOON_JST)
    assert survey_store.get(NUM)["answers"]["life_satisfaction"] == "NEUTRAL"
    with sqlite3.connect(CONFIG.lp_db_file) as c:
        c.row_factory = sqlite3.Row
        versions = {r[0] for r in c.execute("SELECT survey_version FROM survey_responses")}
        assert versions == {V2["version"]}
        dv = {r[0] for r in c.execute("SELECT disclosure_version FROM contact_permissions")}
        assert dv == {V2["disclosure_version"]}
        p = c.execute("SELECT * FROM interest_profiles").fetchone()
        assert p["lifestyle_interest"] == "YES"
        assert p["household_budget_interest"] == "YES"
        assert p["financial_education_interest"] == "YES"
        assert p["insurance_review_interest"] == "YES"
        assert p["future_life_interest"] == "YES"
        n = c.execute("SELECT COUNT(*) FROM survey_responses").fetchone()[0]
    assert n == len(V2["questions"])


# ---- 6. 割り込み（話している途中で答えられる） --------------------------------------------------------
def test_gather_allows_barge_in():
    xml = survey.twiml(survey.Reply(say="質問です", end=False), "https://x.test/tac/survey/step?num=%2B81")
    assert 'bargeIn="true"' in xml


# ---- 7. 電話の Webhook を通した v2 の調査 -----------------------------------------------------------
def test_v2_survey_runs_end_to_end_over_the_webhooks(monkeypatch, tmp_path):
    from tac.server import app

    monkeypatch.setattr(CONFIG, "verify_twilio_signature", False)
    monkeypatch.setattr(CONFIG, "public_base_url", "https://tac.example.test")
    monkeypatch.setattr(CONFIG, "dnc_file", str(tmp_path / "dnc.txt"))
    (tmp_path / "dnc.txt").write_text("", encoding="utf-8")
    q = "num=%2B819011117777"
    with app.test_client() as c:
        xml = c.post(f"/tac/survey/voice?{q}", data={"CallSid": "CA7"}).get_data(as_text=True)
        assert "生活意識調査" in xml
        said = ["はい"] + [_ANSWER_ALL[x["id"]] for x in V2["questions"]] + ["いいえ", "いいえ"]
        for turn, text in enumerate(said, start=1):
            xml = c.post(f"/tac/survey/step?{q}&turn={turn}", data={"CallSid": "CA7", "SpeechResult": text}
                         ).get_data(as_text=True)
            if "<Hangup/>" in xml:
                break
    assert "<Hangup/>" in xml
    rec = survey_store.get(NUM)
    assert rec["outcome"] == "COMPLETED"
    assert rec["insurance_contact_consent"]["status"] == "DECLINED"
    assert survey_store.handoffs() == []
