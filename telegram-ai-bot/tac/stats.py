"""ダッシュボード集計（Stats）。

calllog の全レコードから担当別・日別の発信数/応答率/成約率を集計する。
"""

from __future__ import annotations

from datetime import timedelta

from .config import CONFIG


def aggregate(records: list[dict] | None = None) -> dict:
    """担当別・日別の集計を返す。"""
    if records is None:
        from . import calllog
        records = calllog._all()

    off = CONFIG.call_hours_utc_offset
    by_agent: dict[str, dict] = {}
    by_date: dict[str, dict] = {}
    total_dialed = 0
    total_dispositions = 0
    total_seiyaku = 0

    for rec in records:
        if not isinstance(rec, dict):
            continue
        status = rec.get("status", "")
        agent_name = rec.get("agent_name", "")

        # 日付キー
        ts = rec.get("ts", "")
        date_key = _date_key(ts, off)

        if status == "dialed":
            total_dialed += 1
            if agent_name:
                ag = by_agent.setdefault(agent_name, _empty_agent())
                ag["dialed"] += 1
            if date_key:
                dt = by_date.setdefault(date_key, _empty_date())
                dt["dialed"] += 1

        elif status == "disposition":
            total_dispositions += 1
            disp = rec.get("disposition", "")
            is_seiyaku = disp == "成約"
            if is_seiyaku:
                total_seiyaku += 1

            if agent_name:
                ag = by_agent.setdefault(agent_name, _empty_agent())
                ag["dispositions"] += 1
                if is_seiyaku:
                    ag["seiyaku"] += 1
            if date_key:
                dt = by_date.setdefault(date_key, _empty_date())
                dt["dispositions"] += 1
                if is_seiyaku:
                    dt["seiyaku"] += 1

    return {
        "total_dialed": total_dialed,
        "total_dispositions": total_dispositions,
        "total_seiyaku": total_seiyaku,
        "by_agent": by_agent,
        "by_date": by_date,
    }


def daily_summary(day: str | None = None, records: list[dict] | None = None) -> dict:
    """指定日(JST境界)の発信サマリを返す。

    day 省略時は today（call_hours_utc_offset=既定JST の当日）。運用で「今日ちゃんと
    回ってるか・何件ブロックしたか・成約は何件か」を一目で見るための集計。
    重複Webキャンセル数（二重送信はじき）も併記する。
    """
    from datetime import UTC, datetime

    off = CONFIG.call_hours_utc_offset
    if records is None:
        from . import calllog
        records = calllog._all()
    if day is None:
        day = (datetime.now(UTC) + timedelta(hours=off)).strftime("%Y-%m-%d")

    from . import dnc

    by_status: dict[str, int] = {}
    dispositions: dict[str, int] = {}
    numbers: set[str] = set()
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if _date_key(rec.get("ts", ""), off) != day:
            continue
        status = rec.get("status", "")
        if isinstance(status, str) and status:
            by_status[status] = by_status.get(status, 0) + 1
        if status == "disposition":
            disp = rec.get("disposition", "") or "(未記入)"
            dispositions[disp] = dispositions.get(disp, 0) + 1
        if status == "dialed":
            to = rec.get("to")
            num = dnc.normalize(to) if isinstance(to, str) else ""
            if num:
                numbers.add(num)

    from . import idempotency
    return {
        "date": day,
        "dialed": by_status.get("dialed", 0),
        "blocked": by_status.get("blocked", 0),
        "error": by_status.get("error", 0),
        "by_status": by_status,
        "dispositions": dispositions,
        "seiyaku": dispositions.get("成約", 0),
        "unique_numbers": len(numbers),
        "duplicate_webhooks_blocked": idempotency.duplicates_blocked(),
    }


def _empty_agent() -> dict:
    return {"dialed": 0, "dispositions": 0, "seiyaku": 0}


def _empty_date() -> dict:
    return {"dialed": 0, "dispositions": 0, "seiyaku": 0}


def _date_key(ts: str, utc_offset: int) -> str:
    """ISO 8601 タイムスタンプからローカル日付キー（YYYY-MM-DD）を返す。"""
    if not ts:
        return ""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(ts)
        local = dt + timedelta(hours=utc_offset)
        return local.strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""
