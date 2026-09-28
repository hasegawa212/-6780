"""発信時間帯ガード（Calling-Hours Guard）。

常識外の時間（夜間・早朝）の発信を仕組みで止める。特定商取引法（勧誘は社会通念
上相当な時間帯に）・迷惑防止への配慮であり、DNC と並ぶ「守り」のコンプライアンス
機能。既定 OFF（後方互換）で、ON のときだけ設定時間帯（既定 9〜21時 JST）外の
発信をブロックする。

日本は夏時間（DST）が無いため、tzdata に依存せず UTC オフセット（既定 +9=JST）で
ローカル時刻を計算する。判定は [start, end) の半開区間（start は含み end は含まない）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .config import CONFIG


def window_text() -> str:
    """設定中の許可時間帯を人が読める文字列で返す（メッセージ用）。"""
    off = CONFIG.call_hours_utc_offset
    sign = "+" if off >= 0 else "-"
    return (
        f"{CONFIG.call_hours_start:02d}:00〜{CONFIG.call_hours_end:02d}:00 "
        f"(UTC{sign}{abs(off)})"
    )


def allowed(now: datetime | None = None) -> bool:
    """現在（既定 now=UTC 現在時刻）が発信可能な時間帯かを返す。

    CONFIG.enforce_call_hours が False なら常に True（後方互換）。ON のとき、
    ローカル時（UTC オフセット適用）の「時」が [start, end) にあれば True。
    """
    if not CONFIG.enforce_call_hours:
        return True
    if now is None:
        now = datetime.now(UTC)
    local = now + timedelta(hours=CONFIG.call_hours_utc_offset)
    hour = local.hour
    return CONFIG.call_hours_start <= hour < CONFIG.call_hours_end
