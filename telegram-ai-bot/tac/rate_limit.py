"""発信の1日上限（Daily Call Cap / レート制限）。

1日あたりの発信（架電記録で status="dialed"）件数に上限を設け、掛けすぎ
（迷惑・コスト）を仕組みで防ぐ。既定 OFF（TAC_DAILY_CALL_CAP<=0＝無制限）。
当日の境界は発信時間帯ガードと同じ UTC オフセット（既定 JST=UTC+9）を使う。

DNC・発信時間帯ガードと並ぶ「守り」の機能。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .config import CONFIG


def dialed_today(now: datetime | None = None, records: list[dict] | None = None) -> int:
    """当日（ローカル日付）に発信済み（dialed）の件数を数える。

    records 省略時は架電記録の全件から数える。ts が壊れている行は無視。
    """
    if now is None:
        now = datetime.now(UTC)
    if records is None:
        from . import calllog

        records = calllog._all()
    off = CONFIG.call_hours_utc_offset
    today = (now + timedelta(hours=off)).date()
    n = 0
    for r in records:
        if not isinstance(r, dict) or r.get("status") != "dialed":
            continue
        ts = r.get("ts")
        try:
            dt = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            continue
        if (dt + timedelta(hours=off)).date() == today:
            n += 1
    return n


def allowed(now: datetime | None = None, records: list[dict] | None = None) -> bool:
    """発信可能か（当日の上限に達していないか）を返す。

    CONFIG.daily_call_cap <= 0 なら常に True（無効）。1 以上なら
    「当日 dialed 件数 < 上限」のときだけ True。
    """
    cap = CONFIG.daily_call_cap
    if cap <= 0:
        return True
    return dialed_today(now=now, records=records) < cap
