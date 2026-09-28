"""発信時間帯ガード（Calling-Hours Guard）のテスト（TDD）。

常識外の時間（夜間・早朝）の発信を仕組みで止める。特定商取引法・迷惑防止への
配慮であり、DNC と並ぶ「守り」のコンプライアンス機能。既定 OFF（後方互換）で、
ON のときだけ設定時間帯（既定 9〜21時 JST）外の発信をブロックする。
"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import calling_hours  # noqa: E402
from tac.config import CONFIG  # noqa: E402


def _at_utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


def test_disabled_allows_any_hour():
    CONFIG.enforce_call_hours = False
    # 真夜中 UTC（＝JST 昼）でも早朝でも、無効なら常に許可
    assert calling_hours.allowed(_at_utc(2026, 1, 1, 18)) is True  # JST 03:00
    assert calling_hours.allowed(_at_utc(2026, 1, 1, 3)) is True   # JST 12:00


def test_within_window_allowed():
    CONFIG.enforce_call_hours = True
    CONFIG.call_hours_start = 9
    CONFIG.call_hours_end = 21
    CONFIG.call_hours_utc_offset = 9
    # UTC 03:00 = JST 12:00 → 許可
    assert calling_hours.allowed(_at_utc(2026, 6, 1, 3)) is True


def test_before_start_blocked():
    CONFIG.enforce_call_hours = True
    CONFIG.call_hours_start = 9
    CONFIG.call_hours_end = 21
    CONFIG.call_hours_utc_offset = 9
    # UTC 23:00 = JST 翌08:00 → 開始(9)前でブロック
    assert calling_hours.allowed(_at_utc(2026, 6, 1, 23)) is False


def test_end_is_exclusive_and_after_end_blocked():
    CONFIG.enforce_call_hours = True
    CONFIG.call_hours_start = 9
    CONFIG.call_hours_end = 21
    CONFIG.call_hours_utc_offset = 9
    # UTC 12:00 = JST 21:00 → 終了(21)は含めない＝ブロック
    assert calling_hours.allowed(_at_utc(2026, 6, 1, 12)) is False
    # UTC 14:00 = JST 23:00 → ブロック
    assert calling_hours.allowed(_at_utc(2026, 6, 1, 14)) is False


def test_start_is_inclusive():
    CONFIG.enforce_call_hours = True
    CONFIG.call_hours_start = 9
    CONFIG.call_hours_end = 21
    CONFIG.call_hours_utc_offset = 9
    # UTC 00:00 = JST 09:00 → 開始ちょうどは許可
    assert calling_hours.allowed(_at_utc(2026, 6, 1, 0)) is True


def test_bridge_call_blocks_outside_hours_and_logs():
    """時間外は bridge_call が発信せず blocked(reason=outside_hours) を記録。"""
    from tac import calllog, outbound

    # 架電記録を一時ファイルに
    lf = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    lf.close()
    CONFIG.calllog_file = lf.name
    # DNC は空の一時ファイル
    df = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    df.close()
    dnc_old = CONFIG.dnc_file
    CONFIG.dnc_file = df.name
    CONFIG.agent_number = "+818094662479"
    # 時間帯ガードを有効化し、「常に時間外」になる窓にする（start==end で全時刻ブロック）
    CONFIG.enforce_call_hours = True
    CONFIG.call_hours_start = 9
    CONFIG.call_hours_end = 9
    try:
        result = outbound.bridge_call("+81901234567")
        assert result["ok"] is False
        assert result.get("blocked") is True
        rec = calllog.recent()
        assert rec and rec[0]["status"] == "blocked"
        assert rec[0].get("reason") == "outside_hours"
        assert rec[0]["to"] == "+81901234567"
    finally:
        CONFIG.enforce_call_hours = False
        CONFIG.dnc_file = dnc_old
