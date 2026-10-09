"""生活意識調査モード「ライフパートナー」（金融リテラシー・保険の見直しの意識調査）のテスト（TDD）。

実施事業者は株式会社ジャパンマネジメント。保険の案内は登録済みの保険代理店の担当者が行い、
AI は調査と「案内してよいか」の確認だけをする（商品の推奨・契約の勧誘はしない）。

決定的に守ること（LLM に任せない）:
- 冒頭で、事業者名・AI であること・調査の目的・保険の案内の目的・回答は任意であることを告げる
- 調査への同意と、保険の案内への同意を別々に取る。調査の回答だけで案内の許可とみなさない
- 断り・途中終了・同意の撤回・再連絡の拒否を確実に記録する（再連絡の拒否は DNC）
- 保存するのは選択肢（はい・いいえ・不明）だけ。発話の原文（健康・収入などが入りうる）は保存しない
- 不動産の話題を出さない。FP の紹介・予約はしない
- 架電の前に、連絡許可の証跡・DNC・時間帯・頻度を確かめる
番号はすべて架空。実発信はしない。
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import survey, survey_store  # noqa: E402
from tac.config import CONFIG  # noqa: E402

NUM = "+819011112222"
NOON_JST = datetime(2026, 10, 9, 3, 0, tzinfo=UTC)  # 12:00 JST
NIGHT_JST = datetime(2026, 10, 9, 13, 0, tzinfo=UTC)  # 22:00 JST


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    monkeypatch.setattr(CONFIG, "survey_enabled", True)
    monkeypatch.setattr(CONFIG, "survey_company", "株式会社ジャパンマネジメント")
    monkeypatch.setattr(CONFIG, "survey_caller_id", "+81300000777")
    monkeypatch.setattr(CONFIG, "survey_insurance_agency", "架空保険代理店株式会社")
    monkeypatch.setattr(CONFIG, "survey_purpose", "金融教育の資料づくりと、ご希望の方への情報提供")
    monkeypatch.setattr(CONFIG, "survey_file", str(tmp_path / "survey.json"))
    monkeypatch.setattr(CONFIG, "dnc_file", str(tmp_path / "dnc.txt"))
    (tmp_path / "dnc.txt").write_text("", encoding="utf-8")
    yield


def _entry(**over):
    e = {
        "number": NUM,
        "lead_source": "資料請求フォーム（2026-09）",
        "permission_scope": ["survey"],
        "permission_evidence": "form-2026-09-0001",
    }
    e.update(over)
    return e


def _run(*answers):
    s = survey.new_session(NUM, "CA1")
    replies = [survey.opening(s)]
    for a in answers:
        replies.append(survey.advance(s, a))
    return s, replies


# ---- 冒頭の説明 -----------------------------------------------------------------------
def test_opening_discloses_company_ai_purpose_insurance_and_voluntary():
    s = survey.new_session(NUM, "CA1")
    say = survey.opening(s).say
    assert "株式会社ジャパンマネジメントのAI音声案内担当、ライフパートナーです" in say
    assert "金融" in say and "保険" in say and "調査" in say
    assert "金融教育の資料づくり" in say  # 調査結果の利用目的
    assert "架空保険代理店株式会社" in say and "保険の見直しのご案内" in say  # 営業利用の目的
    assert "任意" in say


def test_survey_never_mentions_real_estate_or_fp():
    texts = [survey.opening(survey.new_session(NUM, "CA1")).say]
    texts += [q["say"] for q in survey.questions()]
    texts += [survey.closing_text(material=True, insurance=True), survey.closing_text(False, False)]
    blob = "".join(texts)
    for word in ("不動産", "物件", "売却", "査定", "住宅", "ファイナンシャル", "FP"):
        assert word not in blob


def test_survey_does_not_ask_for_health_or_income_details():
    blob = "".join(q["say"] for q in survey.questions())
    for word in ("病気", "病歴", "健康状態", "持病", "年収", "収入", "月収", "貯金額", "資産額"):
        assert word not in blob


# ---- 同意を別々に取る ---------------------------------------------------------------------
def test_declining_the_survey_ends_immediately_and_records_it():
    s, r = _run("いいえ、結構です")
    assert r[-1].end is True
    assert s["survey_consent"] == "DECLINED"
    assert s["answers"] == {}
    assert ("decline",) in r[-1].actions


def test_completing_the_survey_without_insurance_interest_gives_no_insurance_consent():
    s, r = _run("はい", "あまりないです", "特にしていません", "あります", "ないです", "あまり分かっていません", "ないです")
    assert r[-1].end is True
    assert s["survey_consent"] == "GRANTED"
    assert s["insurance_contact_consent"] == "NOT_ASKED"
    assert s["material_contact_consent"] == "NOT_ASKED"
    assert not any(a[0] == "handoff_insurance" for a in r[-1].actions)


def test_insurance_guidance_needs_its_own_explicit_yes():
    # 保険の見直しに関心あり → 別の質問で案内の可否を確かめる
    s, r = _run("はい", "あります", "しています", "あります", "ありません", "わかっています", "あります")
    ask = r[-1].say
    assert "架空保険代理店株式会社" in ask and "お電話" in ask and "よろしいでしょうか" in ask
    assert s["insurance_contact_consent"] == "PENDING"
    r2 = survey.advance(s, "はい、お願いします")
    assert s["insurance_contact_consent"] == "GRANTED"
    assert ("handoff_insurance",) in r2.actions


def test_interest_alone_is_not_consent_to_be_contacted():
    s, r = _run("はい", "あります", "しています", "あります", "ありません", "わかっています", "あります", "いいえ")
    assert s["answers"]["insurance_review_interest"] == "YES"
    assert s["insurance_contact_consent"] == "DECLINED"
    assert not any(a[0] == "handoff_insurance" for a in r[-1].actions)


def test_ambiguous_consent_is_not_consent():
    # 「大丈夫です」は断りの意味もある。2 回はっきりしなければ同意なし
    s, _ = _run("はい", "あります", "しています", "あります", "ありません", "わかっています", "あります")
    survey.advance(s, "大丈夫です")
    r = survey.advance(s, "うーん")
    assert s["insurance_contact_consent"] == "DECLINED"
    assert not any(a[0] == "handoff_insurance" for a in r.actions)


def test_material_guidance_is_asked_only_to_those_interested_in_education():
    s, r = _run("はい", "あります", "しています", "あります", "あります")  # 金融教育に関心あり
    # 保険の 2 問の後に資料の案内の可否を聞く
    survey.advance(s, "わかっています")
    r = survey.advance(s, "ないです")
    assert "資料" in r.say and "よろしいでしょうか" in r.say
    r = survey.advance(s, "はい")
    assert s["material_contact_consent"] == "GRANTED"
    assert ("material_contact",) in r.actions


# ---- 途中終了・撤回・再連絡の拒否 -------------------------------------------------------------
def test_stopping_midway_ends_the_survey():
    s, r = _run("はい", "あります", "もうやめてください")
    assert r[-1].end is True
    assert s["outcome"] == "STOPPED"


def test_do_not_call_request_registers_dnc_at_any_point():
    s, r = _run("はい", "もう二度と電話しないでください")
    assert r[-1].end is True
    assert ("dnc",) in r[-1].actions
    assert s["outcome"] == "DNC"


def test_withdrawing_consent_discards_the_answers():
    s, r = _run("はい", "あります", "しています", "やっぱり回答を取り消してください")
    assert r[-1].end is True
    assert ("withdraw",) in r[-1].actions
    assert s["answers"] == {}
    assert s["survey_consent"] == "WITHDRAWN"


def test_silence_is_asked_again_once_then_ends_without_consent():
    s = survey.new_session(NUM, "CA1")
    survey.opening(s)
    r1 = survey.advance(s, "")
    assert r1.end is False and "もう一度" in r1.say
    r2 = survey.advance(s, "")
    assert r2.end is True
    assert s["survey_consent"] == "NO_ANSWER"


# ---- 保存（原文を残さない・同意の証跡） ---------------------------------------------------------
def test_store_keeps_choices_and_consent_evidence_but_not_what_was_said(tmp_path):
    s, _ = _run("はい", "あります", "しています", "持病があって不安です", "ありません", "わかっています", "あります")
    survey.advance(s, "はい")
    survey_store.save_session(s, now=NOON_JST)
    raw = Path(CONFIG.survey_file).read_text(encoding="utf-8")
    assert "持病" not in raw
    rec = json.loads(raw)["records"][NUM]
    assert rec["survey_consent"]["status"] == "GRANTED"
    assert rec["survey_consent"]["evidence"] == "call:CA1"
    assert rec["insurance_contact_consent"]["status"] == "GRANTED"
    assert rec["insurance_contact_consent"]["agency"] == "架空保険代理店株式会社"
    assert rec["insurance_contact_consent"]["method"] == "phone"
    assert survey_store.handoffs()[0]["number"] == NUM


def test_withdraw_by_operator_clears_answers_and_consents():
    s, _ = _run("はい", "あります", "しています", "あります", "ありません", "わかっています", "あります", "はい")
    survey_store.save_session(s, now=NOON_JST)
    assert survey_store.withdraw(NUM, now=NOON_JST) is True
    rec = survey_store.get(NUM)
    assert rec["answers"] == {}
    assert rec["insurance_contact_consent"]["status"] == "WITHDRAWN"
    assert survey_store.handoffs() == []


# ---- 架電の可否（決定的な判定） ------------------------------------------------------------
def test_eligible_entry_can_be_called_at_noon():
    assert survey.can_call(_entry(), now=NOON_JST) == (True, "OK")


@pytest.mark.parametrize("over,reason", [
    ({"permission_scope": ["real_estate"]}, "NO_SURVEY_PERMISSION"),
    ({"permission_evidence": ""}, "NO_PERMISSION_EVIDENCE"),
    ({"lead_source": ""}, "NO_LEAD_SOURCE"),
])
def test_no_call_without_permission_evidence(over, reason):
    assert survey.can_call(_entry(**over), now=NOON_JST) == (False, reason)


def test_no_call_when_disabled_or_settings_missing(monkeypatch):
    monkeypatch.setattr(CONFIG, "survey_enabled", False)
    assert survey.can_call(_entry(), now=NOON_JST)[0] is False
    monkeypatch.setattr(CONFIG, "survey_enabled", True)
    monkeypatch.setattr(CONFIG, "survey_insurance_agency", "")
    assert survey.can_call(_entry(), now=NOON_JST) == (False, "SETTINGS_MISSING")


def test_no_call_to_dnc_or_when_dnc_cannot_be_read(monkeypatch):
    Path(CONFIG.dnc_file).write_text(NUM + "\n", encoding="utf-8")
    assert survey.can_call(_entry(), now=NOON_JST) == (False, "DNC")
    monkeypatch.setattr(CONFIG, "dnc_file", "/nonexistent-dir/dnc.txt")
    assert survey.can_call(_entry(), now=NOON_JST) == (False, "DNC")


def test_no_call_outside_hours_even_if_the_global_guard_is_off(monkeypatch):
    monkeypatch.setattr(CONFIG, "enforce_call_hours", False)
    assert survey.can_call(_entry(), now=NIGHT_JST) == (False, "OUTSIDE_HOURS")


def test_no_second_call_after_any_finished_survey():
    s, _ = _run("いいえ")
    survey_store.save_session(s, now=NOON_JST)
    assert survey.can_call(_entry(), now=NOON_JST) == (False, "ALREADY_SURVEYED")


def test_unanswered_calls_are_retried_at_most_twice():
    survey_store.record_attempt(NUM, now=NOON_JST)
    survey_store.record_attempt(NUM, now=NOON_JST)
    assert survey.can_call(_entry(), now=NOON_JST) == (False, "MAX_ATTEMPTS")


# ---- TwiML ----------------------------------------------------------------------------
def test_twiml_gathers_japanese_speech_and_hangs_up_at_the_end():
    xml = survey.twiml(survey.Reply(say="質問です", end=False), "https://x.test/tac/survey/step?num=%2B81")
    assert '<Gather input="speech" language="ja-JP"' in xml and "質問です" in xml
    end = survey.twiml(survey.Reply(say="失礼いたします", end=True), "")
    assert end.rstrip().endswith("<Hangup/></Response>") and "<Gather" not in end
