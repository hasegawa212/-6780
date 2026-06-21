"""Slack 承認（✅/❌）の判定.

Slack のリアクション名/絵文字から「承認 / 却下 / 保留」を決める。
納品（Drive アップロード等）は ✅ のときだけ発火させる想定。
"""

from __future__ import annotations

# 承認とみなすリアクション（絵文字名・絵文字本体の両対応）
_APPROVE = {
    "white_check_mark", "heavy_check_mark", "ballot_box_with_check",
    "+1", "thumbsup", "o", "circle",
    "✅", "☑️", "✔️", "👍", "⭕",
}
# 却下とみなすリアクション
_REJECT = {
    "x", "negative_squared_cross_mark", "no_entry", "no_entry_sign",
    "ng", "-1", "thumbsdown",
    "❌", "✖️", "🚫", "⛔", "👎",
}


def decide(reaction: str | None) -> str:
    """リアクション → "approve" / "reject" / "pending"。"""
    if not reaction:
        return "pending"
    r = str(reaction).strip().strip(":")  # ":white_check_mark:" 形式も許容
    if r in _APPROVE:
        return "approve"
    if r in _REJECT:
        return "reject"
    return "pending"


def reaction_from_payload(payload: dict) -> str | None:
    """Slack の各種ペイロードからリアクション名を取り出す。

    対応: Events API の reaction_added（payload["event"]["reaction"]）、
    簡易形式（payload["reaction"]）。
    """
    if not isinstance(payload, dict):
        return None
    event = payload.get("event")
    if isinstance(event, dict) and event.get("reaction"):
        return event.get("reaction")
    return payload.get("reaction")
