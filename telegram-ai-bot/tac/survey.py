"""生活意識調査モード「ライフパートナー」（金融リテラシー・保険の見直しの意識調査）。

実施事業者（例：株式会社ジャパンマネジメント）が、連絡許可の証跡がある相手にだけ電話し、
決まった質問を自然な日本語で読み上げて、選択肢（はい・いいえ・不明）で回答を記録する。

AI（LLM）に任せず、サーバーが決定的に行うこと:
- 冒頭の説明：事業者名・AI であること・調査であること・調査結果の利用目的・
  保険の見直しの案内をすることがある旨（案内する保険代理店の名称）・回答は任意であること
- 調査への同意と、保険の案内への同意・資料の案内への同意を、それぞれ別に取る
  （調査に答えたことや「関心がある」と答えたことを、案内の許可とみなさない）
- 断り・途中終了・同意の撤回・再連絡の拒否（DNC）を、その場で判定して終える
- 発話の原文は保存しない（健康状態・収入などが入りうるため）。選択肢だけを残す

しないこと：保険商品の推奨・契約の勧誘（登録済みの保険代理店の担当者が、同意した人にだけ後日案内する）、
不動産の話題、FP の紹介・予約。保険業法・監督指針・個人情報保護法の当てはめは要専門家確認。
flask 非依存の純関数とデータだけを置く（Twilio の Webhook は server.py）。
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .config import CONFIG

DISPLAY_NAME = "ライフパートナー"
SURVEY_ID = "lifepartner"
SURVEY_VERSION = "2026-10-09.1"
SURVEY_TITLE = "生活意識調査（ライフパートナー）"
# 冒頭の説明・同意の質問の文面の版。文面を変えたら上げる（同意の記録に残し、どの説明で同意したかを示す）
DISCLOSURE_VERSION = "lp-2026-10-09.2"
# 調査の電話は、全体の時間帯ガードの設定（TAC_ENFORCE_CALL_HOURS）に関係なく常に 9〜20 時（JST）だけ
_HOURS_JST = (9, 20)
_JST = timedelta(hours=9)
MAX_ATTEMPTS = 2

# 回答の質問（選択肢で記録する）。健康状態・病歴・収入・資産額は聞かない
_QUESTIONS = (
    ("info_access", "普段、お金の管理や将来の備えについて、情報を得る機会はありますか？"),
    ("household_saving", "家計の管理や貯蓄について、意識して取り組まれていることはありますか？"),
    ("future_worry", "将来のお金について、不安に感じていることはありますか？"),
    ("education_interest", "お金の基本を学べる金融教育に、ご関心はありますか？"),
    ("insurance_understanding", "現在ご加入の保険について、保障内容を把握されていますか？"),
    ("insurance_review_interest", "保険の保障内容の確認や見直しに、ご関心はありますか？"),
)

# ---- 発話の判定（決定的なルール） ----
_WITHDRAW = ("取り消", "撤回", "なかったことに", "回答を消", "やっぱり回答")
_DNC = ("電話しないで", "電話をしないで", "かけないで", "かけてこないで", "掛けないで", "二度と",
        "迷惑", "連絡しないで", "連絡不要", "営業電話", "営業の電話")
_STOP = ("やめて", "もういい", "切ります", "終わりにして", "協力できません", "お断り")
_NO = ("いいえ", "いえ", "ない", "ありません", "していません", "してません", "分からない", "わからない",
       "分かっていません", "わかっていません", "あまり", "特に", "不要", "結構", "いらない", "いりません")
_YES = ("はい", "ええ", "うん", "ある", "あります", "しています", "してます", "把握", "分かって",
        "わかって", "お願い", "いいですよ", "かまいません", "構いません", "不安")
# その質問だけ答えたくない（調査は続ける）。冒頭（調査への同意）で言われたら断りとして扱う
_SKIP = ("答えたくない", "言いたくない", "パス", "飛ばして", "とばして", "次の質問", "ノーコメント", "控えます")
# 同意の質問は、はっきりした「はい」だけを同意にする（「大丈夫です」は断りの意味もあるので同意にしない）
_CONSENT_YES = ("はい", "お願い", "いいですよ", "かまいません", "構いません", "ぜひ")


def _norm(text: str) -> str:
    return (text or "").replace(" ", "").replace("　", "")


def classify(text: str, *, consent: bool = False) -> str | None:
    """発話を "WITHDRAW" / "DNC" / "STOP" / "SKIP" / "NO" / "YES" / None（不明）に分ける。"""
    t = _norm(text)
    if not t:
        return None
    if any(p in t for p in _WITHDRAW):
        return "WITHDRAW"
    if any(p in t for p in _DNC):
        return "DNC"
    if any(p in t for p in _STOP):
        return "STOP"
    if any(p in t for p in _SKIP):
        return "SKIP"
    if any(p in t for p in _NO):
        return "NO"
    if any(p in t for p in (_CONSENT_YES if consent else _YES)):
        return "YES"
    return None


@dataclass
class Reply:
    say: str
    end: bool
    actions: list[tuple] = field(default_factory=list)


def missing_settings() -> list[str]:
    items = (
        ("実施事業者", CONFIG.survey_company),
        ("発信元番号", CONFIG.survey_caller_id),
        ("保険代理店", CONFIG.survey_insurance_agency),
        ("調査結果の利用目的", CONFIG.survey_purpose),
    )
    return [label for label, value in items if not (value or "").strip()]


def questions() -> list[dict]:
    """回答の質問と、同意の質問（文面の確認・テスト用）。"""
    return [{"id": qid, "say": say} for qid, say in _QUESTIONS] + [
        {"id": "insurance_contact", "say": _insurance_consent_text()},
        {"id": "material_contact", "say": _material_consent_text()},
    ]


def _insurance_consent_text() -> str:
    return (
        f"保険の見直しのご案内のため、お名前とお電話番号を、保険代理店の{CONFIG.survey_insurance_agency}にお伝えし、"
        "後日、同社の担当者からお電話でご案内してもよろしいでしょうか？"
        "ご希望されない場合は、お伝えも、ご案内もいたしません。"
    )


def _material_consent_text() -> str:
    return (
        f"金融教育の資料について、後日{CONFIG.survey_company}からお電話でご案内してもよろしいでしょうか？"
        "ご希望されない場合は、ご連絡いたしません。"
    )


def closing_text(material: bool, insurance: bool) -> str:
    follow = "ご希望いただいたご案内は、後日あらためてお電話いたします。" if (material or insurance) else ""
    return (
        f"ご協力ありがとうございました。{follow}"
        f"ご回答の取り消しをご希望の場合は、{CONFIG.survey_company}までお申し付けください。失礼いたします。"
    )


_BYE_DECLINE = "承知いたしました。お時間をいただき、ありがとうございました。失礼いたします。"
_BYE_DNC = "承知いたしました。今後、お電話をしないよう登録いたします。ご迷惑をおかけしました。失礼いたします。"
_BYE_WITHDRAW = "承知いたしました。いただいたご回答は取り消しました。お時間をいただき、ありがとうございました。失礼いたします。"
_BYE_SILENT = "お電話が遠いようですので、これで失礼いたします。お時間をいただき、ありがとうございました。"
_RETRY = "お電話が少し遠いようです。恐れ入りますが、もう一度お願いできますか？"


# ---- 会話の状態機械 ----
def new_session(number: str, call_sid: str) -> dict:
    return {
        "number": number,
        "call_sid": call_sid,
        "step": "OPENING",
        "retries": 0,
        "answers": {},
        "survey_consent": "PENDING",
        "insurance_contact_consent": "NOT_ASKED",
        "material_contact_consent": "NOT_ASKED",
        "outcome": "",
        "actions": [],
    }


def opening(s: dict) -> Reply:
    say = (
        f"お世話になっております。{CONFIG.survey_company}のAI音声案内担当、{DISPLAY_NAME}です。"
        "本日は金融知識や保険に関する意識調査のご案内でお電話しました。"
        f"調査の結果は、{CONFIG.survey_purpose}に利用します。"
        f"また、ご希望の方にだけ、保険代理店の{CONFIG.survey_insurance_agency}から、保険の見直しのご案内をすることがあります。"
        "回答は任意で、3分ほどの内容です。ご協力いただけますでしょうか？"
    )
    s["step"] = "OPENING"
    return Reply(say=say, end=False)


def _end(s: dict, outcome: str, say: str, action: tuple | None = None) -> Reply:
    s["step"] = "CLOSED"
    s["outcome"] = outcome
    if action:
        s["actions"].append(action)
    return Reply(say=say, end=True, actions=list(s["actions"]))


def _next_after_questions(s: dict) -> Reply:
    a = s["answers"]
    if a.get("insurance_review_interest") == "YES" and s["insurance_contact_consent"] == "NOT_ASKED":
        s["step"] = "INSURANCE_CONSENT"
        s["insurance_contact_consent"] = "PENDING"
        return Reply(say=_insurance_consent_text(), end=False)
    if a.get("education_interest") == "YES" and s["material_contact_consent"] == "NOT_ASKED":
        s["step"] = "MATERIAL_CONSENT"
        s["material_contact_consent"] = "PENDING"
        return Reply(say=_material_consent_text(), end=False)
    return _end(
        s, "COMPLETED",
        closing_text(s["material_contact_consent"] == "GRANTED", s["insurance_contact_consent"] == "GRANTED"),
    )


def _ask(s: dict, index: int) -> Reply:
    if index >= len(_QUESTIONS):
        return _next_after_questions(s)
    s["step"] = _QUESTIONS[index][0]
    s["retries"] = 0
    return Reply(say=_QUESTIONS[index][1], end=False)


def advance(s: dict, text: str) -> Reply:
    """お客様の発話（音声認識の結果）を受けて、次に話すことを返す。発話の原文は保存しない。"""
    step = s["step"]
    if step == "CLOSED":
        return Reply(say="", end=True, actions=list(s["actions"]))
    consent_step = step in ("OPENING", "INSURANCE_CONSENT", "MATERIAL_CONSENT")
    kind = classify(text, consent=consent_step)

    # どの時点でも：撤回・再連絡の拒否・途中終了
    if kind == "WITHDRAW":
        s["answers"] = {}
        s["survey_consent"] = "WITHDRAWN"
        for k in ("insurance_contact_consent", "material_contact_consent"):
            if s[k] in ("GRANTED", "PENDING"):
                s[k] = "WITHDRAWN"
        s["actions"] = [a for a in s["actions"] if a[0] not in ("handoff_insurance", "material_contact")]
        return _end(s, "WITHDRAWN", _BYE_WITHDRAW, ("withdraw",))
    if kind == "DNC":
        if s["survey_consent"] == "PENDING":
            s["survey_consent"] = "DECLINED"
        return _end(s, "DNC", _BYE_DNC, ("dnc",))
    if kind == "STOP":
        if s["survey_consent"] == "PENDING":
            s["survey_consent"] = "DECLINED"
            return _end(s, "DECLINED", _BYE_DECLINE, ("decline",))
        return _end(s, "STOPPED", _BYE_DECLINE)

    # 聞き取れない・どちらとも取れない：1 回だけ聞き直す
    if kind is None:
        if s["retries"] < 1:
            s["retries"] += 1
            return Reply(say=_RETRY if not _norm(text) else "恐れ入ります。はい、か、いいえ、でお答えいただけますか？", end=False)
        kind = "UNCLEAR"

    if step == "OPENING":
        if kind == "YES":
            s["survey_consent"] = "GRANTED"
            return _ask(s, 0)
        if kind == "UNCLEAR" and not _norm(text):
            s["survey_consent"] = "NO_ANSWER"
            return _end(s, "NO_ANSWER", _BYE_SILENT)
        s["survey_consent"] = "DECLINED"
        return _end(s, "DECLINED", _BYE_DECLINE, ("decline",))

    if step == "INSURANCE_CONSENT":
        if kind == "YES":
            s["insurance_contact_consent"] = "GRANTED"
            s["actions"].append(("handoff_insurance",))
        else:
            s["insurance_contact_consent"] = "DECLINED"
        s["retries"] = 0
        return _next_after_questions(s)

    if step == "MATERIAL_CONSENT":
        if kind == "YES":
            s["material_contact_consent"] = "GRANTED"
            s["actions"].append(("material_contact",))
        else:
            s["material_contact_consent"] = "DECLINED"
        s["retries"] = 0
        return _next_after_questions(s)

    # 回答の質問
    ids = [q for q, _ in _QUESTIONS]
    if step in ids:
        if kind == "UNCLEAR" and not _norm(text):
            return _end(s, "NO_RESPONSE", _BYE_SILENT)
        s["answers"][step] = {"YES": "YES", "NO": "NO", "SKIP": "SKIPPED"}.get(kind, "UNKNOWN")
        return _ask(s, ids.index(step) + 1)
    return _end(s, "ERROR", _BYE_DECLINE)


# ---- 架電の可否（決定的な判定） ----
def _now() -> datetime:
    return datetime.now(UTC)


def load_list() -> list[dict]:
    """調査の対象者（連絡許可の証跡つき）。読めなければ空（＝誰にも掛けない）。"""
    import json

    try:
        with open(CONFIG.survey_list_file, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def find_entry(number: str) -> dict | None:
    from . import phone

    key = phone.to_e164(number) or number
    for e in load_list():
        if (phone.to_e164(str(e.get("number") or "")) or e.get("number")) == key:
            return {**e, "number": key}
    return None

def can_call(entry: dict, *, now: datetime | None = None) -> tuple[bool, str]:
    """この相手に調査の電話を掛けてよいか。判定できないときは掛けない（fail closed）。"""
    from . import dnc, kill_switch, survey_store

    now = now or _now()
    if kill_switch.engaged():
        return False, "KILL_SWITCH"
    if not CONFIG.survey_enabled:
        return False, "DISABLED"
    if missing_settings():
        return False, "SETTINGS_MISSING"
    if not str(entry.get("lead_source") or "").strip():
        return False, "NO_LEAD_SOURCE"
    if "survey" not in (entry.get("permission_scope") or []):
        return False, "NO_SURVEY_PERMISSION"
    if not str(entry.get("permission_evidence") or "").strip():
        return False, "NO_PERMISSION_EVIDENCE"
    number = str(entry.get("number") or "")
    if not number or dnc.is_blocked(number):
        return False, "DNC"
    hour = (now.astimezone(UTC) + _JST).hour
    if not (_HOURS_JST[0] <= hour < _HOURS_JST[1]):
        return False, "OUTSIDE_HOURS"
    from . import lp_db

    try:
        if survey_store.get(number) is not None:
            return False, "ALREADY_SURVEYED"
        if len(survey_store.attempts(number)) >= MAX_ATTEMPTS:
            return False, "MAX_ATTEMPTS"
        active = lp_db.active_calls(now=now)
    except Exception:  # noqa: BLE001  記録を読めないなら、掛けてよいか判断できないので掛けない
        return False, "STORE_UNAVAILABLE"
    if active >= max(CONFIG.survey_max_concurrent, 0):
        return False, "CONCURRENCY_LIMIT"
    return True, "OK"


# ---- TwiML ----
def _say(text: str) -> str:
    return f'<Say language="ja-JP">{html.escape(text)}</Say>'


def twiml(reply: Reply, action_url: str) -> str:
    """話して終える、または話してから音声の回答を待つ TwiML。"""
    head = '<?xml version="1.0" encoding="UTF-8"?><Response>'
    if reply.end:
        return f"{head}{_say(reply.say)}<Hangup/></Response>"
    act = html.escape(action_url, quote=True)
    silence = html.escape(action_url + ("&" if "?" in action_url else "?") + "silence=1", quote=True)
    return (
        f'{head}<Gather input="speech" language="ja-JP" speechTimeout="auto" timeout="6" '
        f'action="{act}" method="POST">{_say(reply.say)}</Gather>'
        f'<Redirect method="POST">{silence}</Redirect></Response>'
    )
