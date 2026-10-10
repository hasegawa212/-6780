"""生活意識調査「ライフパートナー」の質問（版つき）。

質問の文面・選択肢・回答の分け方はここだけで管理する。文面を変えたら版（version）を上げる
（回答と同意の記録に版が残り、どの文面で答えたかを後から確かめられる）。

決まりごと：
- 一度に聞くのは 1 つだけ。選択肢は偏りなく並べ、答えを誘導しない。
- 健康状態・病歴・収入・資産額は聞かない。発話の原文は保存しない（ここで選択肢に分けた値だけを残す）。
- 不動産・FP の話題を入れない。保険は一般的な情報への関心だけを聞き、商品を勧めない。

kind:
- "yesno"  : はい・いいえ（survey.classify の YES / NO）
- "scale"  : 段階（ordered の上から順に、最初に当てはまったもの）
- "choice" : ひとことの答えを分類（同上。どれにも当たらなければ OTHER）
"""

from __future__ import annotations

# 段階の分け方（否定を含む言い方を先に置く）
_UNSURE = ("UNKNOWN", ("わからない", "分からない", "わかりません", "分かりません"))
_SATISFACTION = (
    _UNSURE,
    ("UNSATISFIED", ("不満", "満足していない", "満足してない", "満足していません", "良くない", "よくない",
                     "いまいち", "イマイチ")),
    ("NEUTRAL", ("まあまあ", "まぁまぁ", "普通", "ふつう", "そこそこ", "どちらとも")),
    ("SATISFIED", ("満足", "とても良い", "良いです", "いいです", "十分")),
)
_FREQUENCY = (
    _UNSURE,
    ("RARELY", ("あまり", "ほとんど", "感じない", "感じません")),
    ("SOMETIMES", ("ときどき", "時々", "たまに", "少し")),
    ("OFTEN", ("よく", "いつも", "かなり", "とても", "すごく", "ある", "あります")),
    ("RARELY", ("ない", "ありません")),
)
_FUTURE = (
    ("SECURE", ("不安はない", "不安はありません", "心配ない", "心配はない", "心配していない", "安心")),
    ("WORRIED", ("不安", "心配")),
    ("NEUTRAL", ("どちらとも", "わからない", "分からない", "普通", "まあまあ")),
)
_BURDEN = (
    ("NONE", ("特にない", "特には", "特にありません", "とくにない")),
    ("MONEY", ("支払", "支出", "お金", "出費", "生活費", "家計", "物価", "固定費", "ローン")),
    ("TIME", ("時間", "忙し")),
    ("HOUSEWORK", ("家事", "育児", "子育て", "介護")),
    ("WORK", ("仕事", "職場", "通勤")),
    ("NONE", ("ないです", "ありません", "ない")),
)
_PRIORITY = (
    ("FAMILY", ("家族", "子ども", "子供", "孫", "夫婦")),
    ("HEALTH", ("健康", "元気")),
    ("HOBBY", ("趣味", "旅行", "楽しみ")),
    ("MONEY", ("お金", "ゆとり", "貯金", "貯蓄")),
    ("WORK", ("仕事",)),
    ("NONE", ("特にない", "特には", "とくにない", "ないです", "ありません")),
)
_NOT_INSURED = ("入っていない", "入ってない", "加入していない", "加入してない", "入っていません", "加入していません")

# 答えを受けたときの、ひとことの相づち（なければ「ありがとうございます。」）
_ACK = {
    ("daily_burden", "MONEY"): "毎月の支出が気になっていらっしゃるのですね。",
    ("daily_burden", "TIME"): "お時間のやりくりが大変なのですね。",
    ("daily_burden", "HOUSEWORK"): "ご家庭のことでご負担があるのですね。",
    ("daily_burden", "WORK"): "お仕事のご負担があるのですね。",
    ("insurance_understanding", "NOT_INSURED"): "承知いたしました。",
    ("insurance_understanding", "NO"): "承知いたしました。",
}

V1 = {
    "set": "v1",
    "version": "2026-10-09.1",
    "disclosure_version": "lp-2026-10-09.2",
    "questions": (
        {"id": "info_access", "area": "C", "kind": "yesno",
         "say": "普段、お金の管理や将来の備えについて、情報を得る機会はありますか？"},
        {"id": "household_saving", "area": "B", "kind": "yesno",
         "say": "家計の管理や貯蓄について、意識して取り組まれていることはありますか？"},
        {"id": "future_worry", "area": "E", "kind": "yesno",
         "say": "将来のお金について、不安に感じていることはありますか？"},
        {"id": "education_interest", "area": "C", "kind": "yesno", "trigger": "material",
         "interest": "financial_education_interest",
         "say": "お金の基本を学べる金融教育に、ご関心はありますか？"},
        {"id": "insurance_understanding", "area": "D", "kind": "yesno",
         "say": "現在ご加入の保険について、保障内容を把握されていますか？"},
        {"id": "insurance_review_interest", "area": "D", "kind": "yesno", "trigger": "insurance",
         "interest": "insurance_review_interest",
         "say": "保険の保障内容の確認や見直しに、ご関心はありますか？"},
    ),
}

V2 = {
    "set": "v2",
    "version": "2026-10-10.1",
    "disclosure_version": "lp-2026-10-10.1",
    "questions": (
        # A. 生活全般の見直し
        {"id": "life_satisfaction", "area": "A", "kind": "scale", "ordered": _SATISFACTION,
         "say": "まず、現在の暮らしについてお伺いします。全体として、満足、まあまあ、不満、のうち、どれに近いでしょうか？",
         "rephrase": "今の暮らしに、満足されているか、まあまあか、不満があるか、で結構です。"},
        {"id": "daily_burden", "area": "A", "kind": "choice", "ordered": _BURDEN,
         "say": "日々の暮らしの中で、負担に感じていることはありますか？お金、時間、家事、お仕事など、ひとことで結構です。",
         "rephrase": "負担に感じていることがあれば、ひとことで教えてください。特になければ、ない、で結構です。"},
        {"id": "lifestyle_review_interest", "area": "A", "kind": "yesno", "interest": "lifestyle_interest",
         "say": "暮らし全般を見直すことに、ご関心はありますか？",
         "rephrase": "暮らしを見直すことに関心があるかどうか、はい、か、いいえ、で結構です。"},
        # B. 家計・固定費の見直し
        {"id": "living_cost_burden", "area": "B", "kind": "scale", "ordered": _FREQUENCY,
         "say": "毎月の生活費について、負担に感じることは、よくある、ときどきある、あまりない、のうち、どれに近いでしょうか？",
         "rephrase": "生活費の負担を感じることが、よくあるか、ときどきか、あまりないか、で結構です。"},
        {"id": "spending_tracked", "area": "B", "kind": "yesno",
         "say": "毎月の支出の内訳を、おおよそ把握されていますか？",
         "rephrase": "毎月何にいくら使っているか、だいたい分かっているかどうか、で結構です。"},
        {"id": "fixed_cost_interest", "area": "B", "kind": "yesno", "interest": "household_budget_interest",
         "say": "通信費や光熱費などの、固定費を見直すことに、ご関心はありますか？",
         "rephrase": "毎月決まって出ていく費用を見直すことに、関心があるかどうか、で結構です。"},
        # C. 金融リテラシー
        {"id": "info_access", "area": "C", "kind": "yesno",
         "say": "お金の管理や将来の備えについて、情報を得る機会はありますか？",
         "rephrase": "お金のことを知る機会があるかどうか、はい、か、いいえ、で結構です。"},
        {"id": "fraud_awareness_interest", "area": "C", "kind": "yesno",
         "say": "金融トラブルや詐欺を避けるための知識について、ご関心はありますか？",
         "rephrase": "お金の詐欺やトラブルの防ぎ方に、関心があるかどうか、で結構です。"},
        {"id": "future_money_worry", "area": "C", "kind": "yesno",
         "say": "将来のお金について、不安に感じることはありますか？",
         "rephrase": "将来のお金に不安があるかどうか、はい、か、いいえ、で結構です。"},
        {"id": "education_interest", "area": "C", "kind": "yesno", "trigger": "material",
         "interest": "financial_education_interest",
         "say": "お金の基本を学べる、金融教育の資料に、ご関心はありますか？",
         "rephrase": "お金の基本が分かる資料に、関心があるかどうか、で結構です。"},
        # D. 保険の見直し
        {"id": "insurance_understanding", "area": "D", "kind": "yesno", "extra": (("NOT_INSURED", _NOT_INSURED),),
         "say": "続いて、保険についてお伺いします。現在ご加入の保険の保障内容を、把握されていますか？",
         "rephrase": "入っている保険の内容が、だいたい分かっているかどうか、で結構です。"},
        {"id": "premium_burden", "area": "D", "kind": "scale", "ordered": _FREQUENCY,
         "skip_if": {"insurance_understanding": "NOT_INSURED"},
         "say": "保険料について、負担に感じることは、よくある、ときどきある、あまりない、のうち、どれに近いでしょうか？",
         "rephrase": "保険料の負担を感じることが、よくあるか、ときどきか、あまりないか、で結構です。"},
        {"id": "insurance_review_interest", "area": "D", "kind": "yesno", "trigger": "insurance",
         "interest": "insurance_review_interest",
         "say": "保険の仕組みや、契約内容を確認するときの一般的なポイントについて、情報を得ることにご関心はありますか？",
         "rephrase": "保険の内容を確認するときのポイントを知ることに、関心があるかどうか、で結構です。"},
        # E. 将来の暮らし
        {"id": "future_security", "area": "E", "kind": "scale", "ordered": _FUTURE,
         "say": "将来の暮らしについて、安心、どちらともいえない、不安、のうち、どれに近いでしょうか？",
         "rephrase": "これからの暮らしに、安心か、不安か、どちらともいえないか、で結構です。"},
        {"id": "retirement_interest", "area": "E", "kind": "yesno", "interest": "future_life_interest",
         "say": "老後の暮らしの準備について、ご関心はありますか？",
         "rephrase": "老後の準備に関心があるかどうか、はい、か、いいえ、で結構です。"},
        {"id": "life_priority", "area": "E", "kind": "choice", "ordered": _PRIORITY,
         "say": "最後に、これからの暮らしで大切にしたいことを、ひとことで教えていただけますか？たとえば、家族、趣味、お金のゆとり、などです。",
         "rephrase": "これから大切にしたいことを、ひとことで結構です。"},
    ),
}

SETS = {"v1": V1, "v2": V2}
DEFAULT_SET = "v2"


def get_set(name: str | None) -> dict:
    """質問の版。知らない名前なら既定の版（設定の誤りで質問が無くならないように）。"""
    return SETS.get((name or "").strip(), SETS[DEFAULT_SET])


def answer_values() -> set[str]:
    """保存してよい回答の値（選択肢）。これ以外は保存しない。"""
    vals = {"YES", "NO", "UNKNOWN", "SKIPPED", "OTHER"}
    for qs in SETS.values():
        for q in qs["questions"]:
            for value, _ in q.get("ordered", ()) + q.get("extra", ()):
                vals.add(value)
    return vals


def classify_answer(q: dict, text: str) -> str | None:
    """scale / choice の答えを選択肢に分ける。分けられなければ None（choice は OTHER）。"""
    t = (text or "").replace(" ", "").replace("　", "")
    if not t:
        return None
    for value, patterns in q.get("ordered", ()):
        if any(p in t for p in patterns):
            return value
    return "OTHER" if q.get("kind") == "choice" else None


def extra_answer(q: dict, text: str) -> str | None:
    """yesno の質問で、はい・いいえ以外の決まった答え（例：保険に入っていない）。"""
    t = (text or "").replace(" ", "").replace("　", "")
    for value, patterns in q.get("extra", ()):
        if any(p in t for p in patterns):
            return value
    return None


def ack(qid: str, value: str) -> str:
    return _ACK.get((qid, value), "ありがとうございます。")
