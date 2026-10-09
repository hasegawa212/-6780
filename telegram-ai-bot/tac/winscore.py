"""成約確度エンジン（Win-Probability / 当社の勝ち筋ベース）。

さくら発信リストを「当社が実際に決済した勝ちパターン」で採点し、成約確度の
高い順に並べ替える。これは汎用スコアではなく、社内の実決済データに根ざした
“当社にしかない”優先度付け＝限られた架電時間を勝てる相手に集中させるための土台。

採点は全て説明可能（reasons）で、根拠は：
  1) 自動フォロー分類の温度（followup のカテゴリ。再調整希望>日程返答待ち>不在…）
  2) 当社の実決済エリア適合（closed-deals-winning-points の実データ：北関東）
  3) 記録本文の前向きシグナル
  4) 鮮度/しつこさ（follow_count）と過去到達性（calllog 履歴）
拒否（連絡停止）は常に除外。グレード帯は社内スコアリング(S/A/B/C/D)に揃える。

架空の根拠は作らない。信号が無い項目は 0 点＋「情報なし」と明記する。
重みは会社の勝ち筋の仮説であり、運用で検証・調整する（断定しない）。
"""

from __future__ import annotations

# 当社の実決済エリア（closed-deals-winning-points.md の実データ由来：北関東が軸）。
WIN_AREAS: tuple[str, ...] = (
    "宇都宮", "西川田", "兵庫塚", "雀宮", "鹿沼", "ひたちなか", "水戸",
    "真岡", "笠間", "鹿嶋", "常陸太田", "結城", "栃木", "茨城", "群馬",
)

# カテゴリ温度（自動フォロー分類。followup._SCORE と整合する序列）。
CATEGORY_POINTS: dict[str, int] = {
    "再調整希望": 45,
    "日程返答待ち": 40,
    "不在": 25,
    "要確認": 10,
    "連絡停止": -1000,  # 実際は除外（下の EXCLUDE で弾く）
}

# 記録本文の前向きシグナル（1語 +5、上限 +15）。
POSITIVE_WORDS: tuple[str, ...] = (
    "前向き", "興味", "検討", "予算", "買いたい", "見たい", "内見", "ローン", "審査",
)

AREA_POINTS = 25
POSITIVE_EACH = 5
POSITIVE_CAP = 15
FRESH_BONUS = 10        # まだ一度も掛けていない（鮮度）
STALE_PENALTY = -10     # 何度も追っているのに進まない
STALE_THRESHOLD = 3
REACH_BONUS = 5         # 過去に会話が成立している（到達性）

EXCLUDED_CATEGORIES = ("連絡停止",)


def grade_of(score: int) -> str:
    """社内スコアリング帯でグレードを返す（S:80+ / A:65-79 / B:50-64 / C:35-49 / D:<35）。"""
    if score >= 80:
        return "S"
    if score >= 65:
        return "A"
    if score >= 50:
        return "B"
    if score >= 35:
        return "C"
    return "D"


def _clamp(n: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, n))


def score_lead(lead: dict, *, history: list[dict] | None = None,
               now=None) -> dict:
    """1 件を採点する。{score, grade, reasons:[{factor,points,detail}]} を返す。

    history は calllog の当該番号レコード（任意）。過去に会話成立(disposition)が
    あれば到達性を加点する。拒否カテゴリは score=0 / grade=除外。
    """
    reasons: list[dict] = []

    def add(factor: str, points: int, detail: str) -> None:
        reasons.append({"factor": factor, "points": points, "detail": detail})

    category = str(lead.get("category") or "")

    # 拒否は常に除外（エリア一致でも発信対象にしない）
    if category in EXCLUDED_CATEGORIES:
        add("除外(拒否)", 0, "連絡停止＝発信対象外")
        return {"score": 0, "grade": "除外", "reasons": reasons}

    pts = 0

    # 1) カテゴリ温度
    cp = CATEGORY_POINTS.get(category, 0)
    pts += cp
    add("カテゴリ温度", cp, category or "未分類")

    # 2) 当社実決済エリア適合（area が空でも本文から拾う）
    haystack = f"{lead.get('area') or ''} {lead.get('record') or ''}"
    matched = [a for a in WIN_AREAS if a in haystack]
    if matched:
        pts += AREA_POINTS
        add("実決済エリア一致", AREA_POINTS, f"当社実績エリア: {matched[0]}")
    else:
        add("実決済エリア一致", 0, "実績エリアの地名なし/不明")

    # 3) 前向きシグナル（本文＋根拠語）
    text = f"{lead.get('record') or ''} {lead.get('basis') or ''}"
    hits = [w for w in POSITIVE_WORDS if w in text]
    if hits:
        p = min(len(hits) * POSITIVE_EACH, POSITIVE_CAP)
        pts += p
        add("前向きシグナル", p, "/".join(hits))
    else:
        add("前向きシグナル", 0, "前向き語なし")

    # 4) 鮮度 / しつこさ
    fc = int(lead.get("follow_count") or 0)
    if fc == 0:
        pts += FRESH_BONUS
        add("鮮度", FRESH_BONUS, "未架電（まだ一度も追っていない）")
    elif fc >= STALE_THRESHOLD:
        pts += STALE_PENALTY
        add("追いすぎ", STALE_PENALTY, f"フォロー{fc}回で未進展")
    else:
        add("鮮度", 0, f"フォロー{fc}回")

    # 5) 過去到達性（calllog 履歴に会話成立があるか）
    reached = any(
        isinstance(h, dict) and h.get("status") == "disposition"
        for h in (history or [])
    )
    if reached:
        pts += REACH_BONUS
        add("到達性", REACH_BONUS, "過去に会話成立あり")
    else:
        add("到達性", 0, "会話成立の履歴なし")

    score = _clamp(pts)
    return {"score": score, "grade": grade_of(score), "reasons": reasons}


def prioritize(leads: list[dict], *,
               history_by_number: dict[str, list[dict]] | None = None,
               include_excluded: bool = True) -> list[dict]:
    """発信リストを成約確度の高い順に並べ替える。

    各要素に `_win`（{score,grade,reasons}）を付与したコピーを返す。元リストは
    壊さない。拒否(除外)は include_excluded=False で落とせる（既定は末尾に残す）。
    """
    hb = history_by_number or {}
    out: list[dict] = []
    for lead in leads:
        enriched = dict(lead)
        hist = hb.get(str(lead.get("number") or ""), [])
        enriched["_win"] = score_lead(lead, history=hist)
        out.append(enriched)

    if not include_excluded:
        out = [x for x in out if x["_win"]["grade"] != "除外"]

    # 除外は末尾、それ以外は score 降順（同点は安定ソートで元順維持）
    out.sort(key=lambda x: (x["_win"]["grade"] == "除外", -x["_win"]["score"]))
    return out
