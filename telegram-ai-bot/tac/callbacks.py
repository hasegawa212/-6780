"""折り返しスケジュール（Callback Schedule）。

calllog の disposition レコードから callback_at 付きのものを抽出し、
当日分や全件の一覧を返す。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .config import CONFIG


def list_callbacks(records: list[dict] | None = None) -> list[dict]:
    """callback_at 付きの折り返し予定を全件返す（時刻順）。"""
    if records is None:
        from . import calllog
        records = calllog._all()
    out = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        cb = rec.get("callback_at")
        if cb:
            out.append(rec)
    out.sort(key=lambda r: r.get("callback_at", ""))
    return out


def list_today(records: list[dict] | None = None) -> list[dict]:
    """当日分の折り返し予定だけ返す（時刻順）。"""
    off = CONFIG.call_hours_utc_offset
    now = datetime.now(UTC)
    today = (now + timedelta(hours=off)).date()

    all_cb = list_callbacks(records)
    out = []
    for rec in all_cb:
        cb = rec.get("callback_at", "")
        try:
            cb_date = datetime.fromisoformat(cb)
            # callback_at に TZ が無い場合はローカル日付としてそのまま比較
            if cb_date.date() == today:
                out.append(rec)
        except (TypeError, ValueError):
            continue
    return out
