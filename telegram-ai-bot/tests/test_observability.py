"""観測性（Observability）の TDD。

1) stats.daily_summary … 指定日(JST)の発信サマリ（架電/ブロック/結果/ユニーク番号）。
2) idempotency の重複Webhookカウンタ … 二重送信を何件はじいたかを数える。

運用で「今日ちゃんと回ってるか／何件ブロックしたか」を一目で見るための土台。
実行: cd telegram-ai-bot && python3 -m pytest tests/test_observability.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import idempotency, stats  # noqa: E402


# ============================================================
# 1) 日次発信サマリ
# ============================================================
# JST(UTC+9)。2026-10-09 09:00 JST = 2026-10-09 00:00 UTC。
_RECORDS = [
    {"ts": "2026-10-09T00:30:00+00:00", "direction": "outbound", "to": "090-1111-1111", "status": "dialed"},
    {"ts": "2026-10-09T01:00:00+00:00", "direction": "outbound", "to": "090-1111-1111", "status": "dialed"},
    {"ts": "2026-10-09T02:00:00+00:00", "direction": "outbound", "to": "090-2222-2222", "status": "dialed"},
    {"ts": "2026-10-09T02:05:00+00:00", "direction": "outbound", "to": "090-3333-3333", "status": "blocked"},
    {"ts": "2026-10-09T03:00:00+00:00", "direction": "outbound", "to": "090-2222-2222", "status": "disposition", "disposition": "成約"},
    # 前日(JST)のレコード。2026-10-08T14:00Z = 2026-10-08 23:00 JST。集計に混ぜない。
    {"ts": "2026-10-08T14:00:00+00:00", "direction": "outbound", "to": "090-9999-9999", "status": "dialed"},
]


def test_daily_summary_counts_today_only():
    s = stats.daily_summary(day="2026-10-09", records=_RECORDS)
    assert s["date"] == "2026-10-09"
    assert s["dialed"] == 3          # 前日分は除外
    assert s["blocked"] == 1
    assert s["seiyaku"] == 1


def test_daily_summary_unique_numbers():
    s = stats.daily_summary(day="2026-10-09", records=_RECORDS)
    # 当日 dialed の宛先は 1111(x2)/2222 の 2 ユニーク
    assert s["unique_numbers"] == 2


def test_daily_summary_dispositions_breakdown():
    s = stats.daily_summary(day="2026-10-09", records=_RECORDS)
    assert s["dispositions"].get("成約") == 1


def test_daily_summary_prev_day_isolated():
    s = stats.daily_summary(day="2026-10-08", records=_RECORDS)
    assert s["dialed"] == 1
    assert s["seiyaku"] == 0


def test_daily_summary_defaults_to_today():
    # day 省略 → 例外なく today(JST) の構造を返す
    s = stats.daily_summary(records=[])
    assert "date" in s and s["dialed"] == 0


def test_daily_summary_has_duplicate_counter_field():
    s = stats.daily_summary(day="2026-10-09", records=_RECORDS)
    assert "duplicate_webhooks_blocked" in s


# ============================================================
# 2) 重複Webhookカウンタ
# ============================================================
def test_duplicate_counter_starts_zero():
    assert idempotency.duplicates_blocked() == 0


def test_duplicate_counter_increments_on_repeat():
    assert idempotency.seen("CAdup1") is False   # 初回
    assert idempotency.duplicates_blocked() == 0
    assert idempotency.seen("CAdup1") is True     # 2回目=重複
    assert idempotency.duplicates_blocked() == 1
    assert idempotency.seen("CAdup1") is True     # 3回目
    assert idempotency.duplicates_blocked() == 2


def test_duplicate_counter_distinct_keys_not_counted():
    idempotency.seen("CAa")
    idempotency.seen("CAb")
    assert idempotency.duplicates_blocked() == 0   # 別キーは重複じゃない


def test_empty_key_never_counts():
    idempotency.seen("")
    idempotency.seen("")
    assert idempotency.duplicates_blocked() == 0


# ============================================================
# 3) /tac/calls/daily ルート
# ============================================================
def test_daily_route_requires_token():
    import importlib
    try:
        server = importlib.import_module("tac.server")
    except Exception:  # noqa: BLE001 - flask 未導入ならskip
        import pytest
        pytest.skip("flask 未導入")
    from tac.config import CONFIG
    prev = CONFIG.outbound_token
    CONFIG.outbound_token = "tok-daily-test"
    try:
        client = server.app.test_client()
        assert client.get("/tac/calls/daily").status_code == 401
        r = client.get("/tac/calls/daily", query_string={"token": "tok-daily-test"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert "date" in body["summary"]
    finally:
        CONFIG.outbound_token = prev
