"""生活意識調査の記録（同意・回答の選択肢・撤回・保険の案内への引き継ぎ・架電の試行）。

JSON ファイル（CONFIG.survey_file、本番は /data）と、ライフパートナーの CRM（tac/lp_db.py、SQLite）の
両方に書く。JSON は架電の可否の判定（調査済み・試行回数）に、CRM は顧客・同意・回答・関心・監査に使う。
発話の原文は保存しない。
同意は種類ごとに、状態・時刻・証跡（通話の CallSid）を残す。撤回されたら回答と同意を消し、撤回の事実だけを残す。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime

from .config import CONFIG

_lock = threading.Lock()
_CHOICES = ("YES", "NO", "UNKNOWN", "SKIPPED")
# 会話の状態の同意の名前 → CRM の連絡の許可の目的
_PURPOSES = (("survey_consent", "survey"), ("insurance_contact_consent", "insurance_info"),
             ("material_contact_consent", "material_info"))
# 回答の質問 → 関心の項目（P1-b で生活全般・家計・将来の質問を足したら、ここに対応を足す）
_INTERESTS = {"education_interest": "financial_education_interest",
              "insurance_review_interest": "insurance_review_interest"}


def _load() -> dict:
    try:
        with open(CONFIG.survey_file, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"records": {}, "attempts": {}}
    # 読めない・壊れているときは例外にする（空として扱って同じ相手に掛け直さない）
    if not isinstance(data, dict):
        raise ValueError("survey file is not an object")
    data.setdefault("records", {})
    data.setdefault("attempts", {})
    return data


def _save(data: dict) -> None:
    path = CONFIG.survey_file
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _iso(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat()


def save_session(s: dict, *, now: datetime | None = None) -> dict:
    """終わった通話の結果を保存する。回答は選択肢だけ。"""
    at = _iso(now)
    evidence = f"call:{s.get('call_sid', '')}"
    rec = {
        "number": s["number"],
        "call_sid": s.get("call_sid", ""),
        "at": at,
        "outcome": s.get("outcome", ""),
        "survey_consent": {"status": s["survey_consent"], "at": at, "evidence": evidence},
        "answers": {k: v for k, v in s.get("answers", {}).items() if v in _CHOICES},
        "insurance_contact_consent": {
            "status": s["insurance_contact_consent"], "at": at, "evidence": evidence,
            "agency": CONFIG.survey_insurance_agency, "method": "phone",
        },
        "material_contact_consent": {
            "status": s["material_contact_consent"], "at": at, "evidence": evidence,
            "company": CONFIG.survey_company, "method": "phone",
        },
    }
    with _lock:
        data = _load()
        data["records"][s["number"]] = rec
        _save(data)
    _save_to_crm(s, now=now)
    return rec


def _save_to_crm(s: dict, *, now: datetime | None = None) -> None:
    from . import lp_db, survey

    evidence = f"call:{s.get('call_sid', '')}"
    providers = {"insurance_info": CONFIG.survey_insurance_agency, "material_info": CONFIG.survey_company,
                 "survey": CONFIG.survey_company}
    answers = {k: v for k, v in s.get("answers", {}).items() if v in _CHOICES}
    with lp_db.tx() as c:
        cid = lp_db.upsert_customer(c, s["number"], now=now)
        lp_db.ensure_survey(c, survey.SURVEY_ID, survey.SURVEY_VERSION, survey.SURVEY_TITLE)
        for key, purpose in _PURPOSES:
            status = s.get(key, "NOT_ASKED")
            if status == "NOT_ASKED":
                continue
            lp_db.set_permission(c, cid, purpose, status, disclosure_version=survey.DISCLOSURE_VERSION,
                                 evidence=evidence, provider=providers[purpose], now=now)
        c.execute("DELETE FROM survey_responses WHERE customer_id=? AND call_id=?", (cid, s.get("call_sid", "")))
        at = lp_db._iso(now)
        for qid, value in answers.items():
            skipped = value == "SKIPPED"
            c.execute(
                """INSERT INTO survey_responses(survey_id, survey_version, customer_id, call_id, question_id,
                                                answer_value, skipped, answered_at) VALUES (?,?,?,?,?,?,?,?)""",
                (survey.SURVEY_ID, survey.SURVEY_VERSION, cid, s.get("call_sid", ""), qid,
                 None if skipped else value, int(skipped), at))
        interests = {col: answers.get(qid) for qid, col in _INTERESTS.items()
                     if answers.get(qid) in ("YES", "NO", "UNKNOWN")}
        if interests:
            cols = ", ".join(interests)
            marks = ", ".join("?" * len(interests))
            updates = ", ".join(f"{k}=excluded.{k}" for k in interests)
            c.execute(f"""INSERT INTO interest_profiles(customer_id, {cols}, updated_at) VALUES (?, {marks}, ?)
                          ON CONFLICT(customer_id) DO UPDATE SET {updates}, updated_at=excluded.updated_at""",
                      (cid, *interests.values(), at))
        lp_db.audit(c, "survey", "survey_saved", cid,
                    {"outcome": s.get("outcome", ""), "answered": len(answers),
                     "skipped": sum(1 for v in answers.values() if v == "SKIPPED")}, now=now)


def get(number: str) -> dict | None:
    with _lock:
        return _load()["records"].get(number)


def record_attempt(number: str, *, now: datetime | None = None) -> None:
    with _lock:
        data = _load()
        data["attempts"].setdefault(number, []).append(_iso(now))
        _save(data)


def attempts(number: str) -> list[str]:
    with _lock:
        return list(_load()["attempts"].get(number, []))


def withdraw(number: str, *, now: datetime | None = None) -> bool:
    """同意の撤回（電話・窓口で申し出があったとき）。回答と同意を消し、撤回の事実だけを残す。"""
    with _lock:
        data = _load()
        rec = data["records"].get(number)
        if rec is None:
            return False
        at = _iso(now)
        rec["answers"] = {}
        rec["outcome"] = "WITHDRAWN"
        for k in ("survey_consent", "insurance_contact_consent", "material_contact_consent"):
            rec[k] = {**rec.get(k, {}), "status": "WITHDRAWN", "at": at}
        _save(data)
    _withdraw_in_crm(number, now=now)
    return True


def _withdraw_in_crm(number: str, *, now: datetime | None = None) -> None:
    """撤回をすぐ反映する：回答と関心を物理的に消し、連絡の許可をすべて撤回にする。"""
    from . import lp_db

    at = lp_db._iso(now)
    with lp_db.tx() as c:
        cid = lp_db.customer_id(c, number)
        if cid is None:
            return
        n = c.execute("DELETE FROM survey_responses WHERE customer_id=?", (cid,)).rowcount
        c.execute("DELETE FROM interest_profiles WHERE customer_id=?", (cid,))
        c.execute("UPDATE contact_permissions SET status='WITHDRAWN', revoked_at=? WHERE customer_id=?", (at, cid))
        lp_db.audit(c, "operator", "withdraw", cid, {"responses_deleted": n}, now=now)


def handoffs() -> list[dict]:
    """保険の案内に同意した人（登録済みの保険代理店の担当者が後日電話する）。撤回された人は含めない。"""
    with _lock:
        records = _load()["records"].values()
    return [
        {"number": r["number"], "at": r["insurance_contact_consent"]["at"],
         "agency": r["insurance_contact_consent"].get("agency", ""), "evidence": r["insurance_contact_consent"]["evidence"]}
        for r in records
        if r.get("insurance_contact_consent", {}).get("status") == "GRANTED"
    ]


def summary() -> dict:
    """集計（件数だけ。電話番号は出さない）。"""
    with _lock:
        records = list(_load()["records"].values())
    out: dict[str, int] = {}
    for r in records:
        key = r.get("outcome") or "UNKNOWN"
        out[key] = out.get(key, 0) + 1
    return {
        "total": len(records),
        "by_outcome": out,
        "insurance_contact_granted": sum(
            1 for r in records if r.get("insurance_contact_consent", {}).get("status") == "GRANTED"),
        "material_contact_granted": sum(
            1 for r in records if r.get("material_contact_consent", {}).get("status") == "GRANTED"),
    }
