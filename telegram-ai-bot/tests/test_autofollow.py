"""自動フォロー架電エンジンのテスト（TDD）。

「同意済みのお客様に、順番に自動でフォロー架電する」を安全に行うための中核ロジック。
設計図（訪問営業｜自動フォロー設計図）の TDD チェックを機械で固定する:

  - 同意なし・拒否済み(DNC)は発信しない
  - 1日1回・合計2回を超えない
  - 希望時間帯（発信時間帯）外には発信しない
  - 応答・拒否で再発信を止める
  - 二重処理しない（本日発信済みは対象外）
  - 停止/OFF のときは1件も発信しない（既定OFF＝事故ゼロ）

実架電はテストではモック（placer 注入）。本物の発信は呼ばない。
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import autofollow as af  # noqa: E402
from tac.config import CONFIG  # noqa: E402


# 発信可能な時間帯（JST 13:00 相当 = UTC 04:00）の固定時刻
NOON_JST = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)
# 深夜（JST 05:00 相当 = 前日 UTC 20:00）
NIGHT_JST = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)


def _entry(**over):
    e = {
        "id": "e1",
        "number": "+819011110000",
        "name": "山田",
        "category": "再調整希望",
        "consent": "同意",
        "follow_count": 0,
        "last_follow_at": "",
    }
    e.update(over)
    return e


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    # 状態ファイルを隔離し、ガードは既定（OFF）に戻す
    monkeypatch.setattr(CONFIG, "autofollow_file", str(tmp_path / "af.json"))
    monkeypatch.setattr(CONFIG, "follow_cap", 2)
    monkeypatch.setattr(CONFIG, "enforce_call_hours", True)
    monkeypatch.setattr(CONFIG, "daily_call_cap", 0)
    # DNC を汚染しないようメモリ上で判定を差し替え
    monkeypatch.setattr(af.dnc, "contains", lambda n: n in _DNC)
    yield


_DNC: set[str] = set()


# --- 状態（ON/OFF・一時停止） -----------------------------------------
def test_engine_is_off_by_default():
    assert af.is_enabled() is False
    st = af.status()
    assert st["enabled"] is False
    assert st["paused"] is False


def test_can_toggle_enabled_and_paused():
    af.set_enabled(True)
    assert af.is_enabled() is True
    af.set_paused(True)
    assert af.status()["paused"] is True
    af.set_paused(False)
    af.set_enabled(False)
    assert af.is_enabled() is False


# --- decide（1件の発信可否と理由） ------------------------------------
def test_off_blocks_everything():
    d = af.decide(_entry(), now=NOON_JST, enabled=False)
    assert d.place is False
    assert "OFF" in d.reason


def test_paused_blocks():
    d = af.decide(_entry(), now=NOON_JST, enabled=True, paused=True)
    assert d.place is False
    assert "停止" in d.reason


def test_eligible_contact_is_placeable():
    d = af.decide(_entry(), now=NOON_JST, enabled=True)
    assert d.place is True


def test_outside_calling_hours_blocks():
    d = af.decide(_entry(), now=NIGHT_JST, enabled=True)
    assert d.place is False
    assert "時間帯" in d.reason


def test_declined_consent_blocks():
    d = af.decide(_entry(consent="拒否"), now=NOON_JST, enabled=True)
    assert d.place is False
    assert "拒否" in d.reason


def test_dnc_blocks():
    _DNC.add("+819011110000")
    try:
        d = af.decide(_entry(), now=NOON_JST, enabled=True)
        assert d.place is False
        assert "DNC" in d.reason
    finally:
        _DNC.discard("+819011110000")


def test_non_callable_category_blocks():
    d = af.decide(_entry(category="連絡停止"), now=NOON_JST, enabled=True)
    assert d.place is False
    assert "対象外" in d.reason


def test_total_cap_blocks():
    d = af.decide(_entry(follow_count=2), now=NOON_JST, enabled=True)
    assert d.place is False
    assert "合計" in d.reason


def test_already_followed_today_blocks():
    today = NOON_JST.isoformat()
    d = af.decide(_entry(follow_count=1, last_follow_at=today), now=NOON_JST, enabled=True)
    assert d.place is False
    assert "本日" in d.reason


def test_followed_yesterday_is_ok():
    yday = (NOON_JST - timedelta(days=1)).isoformat()
    d = af.decide(_entry(follow_count=1, last_follow_at=yday), now=NOON_JST, enabled=True)
    assert d.place is True


def test_global_daily_cap_blocks(monkeypatch):
    monkeypatch.setattr(af.rate_limit, "allowed", lambda now=None, records=None: False)
    d = af.decide(_entry(), now=NOON_JST, enabled=True)
    assert d.place is False
    assert "全体" in d.reason


# --- select_next（次に掛ける1件を選ぶ） -------------------------------
def test_select_next_prefers_higher_priority_category():
    low = _entry(id="low", number="+819000000001", category="不在")          # score 70
    high = _entry(id="high", number="+819000000002", category="再調整希望")  # score 90
    chosen, dec = af.select_next([low, high], now=NOON_JST, enabled=True)
    assert chosen["id"] == "high"
    assert dec.place is True


def test_select_next_skips_blocked_and_returns_eligible():
    blocked = _entry(id="b", number="+819000000001", consent="拒否")
    ok = _entry(id="ok", number="+819000000002")
    chosen, _ = af.select_next([blocked, ok], now=NOON_JST, enabled=True)
    assert chosen["id"] == "ok"


def test_select_next_returns_none_when_all_blocked():
    chosen, dec = af.select_next(
        [_entry(consent="拒否")], now=NOON_JST, enabled=True
    )
    assert chosen is None
    assert dec.place is False


def test_select_next_off_returns_reason():
    chosen, dec = af.select_next([_entry()], now=NOON_JST, enabled=False)
    assert chosen is None
    assert "OFF" in dec.reason


# --- run_once（1件発信：実架電はモック） -----------------------------
def test_run_once_places_and_records(monkeypatch):
    placed = {}
    recorded = []
    monkeypatch.setattr(af.followup, "record_follow", lambda num: recorded.append(num) or 1)
    res = af.run_once(
        entries=[_entry()], now=NOON_JST, enabled=True,
        placer=lambda e: placed.setdefault("num", e["number"]) or {"sid": "CA1"},
    )
    assert res["placed"] is True
    assert placed["num"] == "+819011110000"
    assert recorded == ["+819011110000"]  # フォロー回数を記録した


def test_run_once_off_does_not_place(monkeypatch):
    called = []
    res = af.run_once(
        entries=[_entry()], now=NOON_JST, enabled=False,
        placer=lambda e: called.append(1),
    )
    assert res["placed"] is False
    assert "OFF" in res["reason"]
    assert called == []  # 実発信は一切呼ばれない


# --- run_batch（連続オート発信） --------------------------------------
def _entries3():
    return [
        _entry(id="a", number="+819000000001", name="A", category="再調整希望"),
        _entry(id="b", number="+819000000002", name="B", category="日程返答待ち"),
        _entry(id="c", number="+819000000003", name="C", category="不在"),
    ]


def test_run_batch_calls_all_eligible_once_each(monkeypatch):
    monkeypatch.setattr(af.followup, "record_follow", lambda num: 1)
    dialed = []
    res = af.run_batch(
        entries=_entries3(), now=NOON_JST, enabled=True, max_calls=10,
        placer=lambda e: dialed.append(e["number"]) or {"ok": True},
    )
    assert res["placed"] == 3
    # 3人それぞれに1回ずつ・重複なし（同じ相手を連打しない）
    assert sorted(dialed) == ["+819000000001", "+819000000002", "+819000000003"]
    assert len(set(dialed)) == 3


def test_run_batch_respects_max_calls(monkeypatch):
    monkeypatch.setattr(af.followup, "record_follow", lambda num: 1)
    dialed = []
    res = af.run_batch(
        entries=_entries3(), now=NOON_JST, enabled=True, max_calls=2,
        placer=lambda e: dialed.append(e["number"]) or {"ok": True},
    )
    assert res["placed"] == 2
    assert len(dialed) == 2


def test_run_batch_prioritizes_high_score_first(monkeypatch):
    monkeypatch.setattr(af.followup, "record_follow", lambda num: 1)
    dialed = []
    af.run_batch(
        entries=_entries3(), now=NOON_JST, enabled=True, max_calls=1,
        placer=lambda e: dialed.append(e["number"]) or {"ok": True},
    )
    # 再調整希望(score90)のAが最優先
    assert dialed == ["+819000000001"]


def test_run_batch_off_places_nothing(monkeypatch):
    called = []
    res = af.run_batch(
        entries=_entries3(), now=NOON_JST, enabled=False,
        placer=lambda e: called.append(1),
    )
    assert res["placed"] == 0
    assert "OFF" in res["reason"]
    assert called == []


def test_run_batch_skips_blocked_contacts(monkeypatch):
    monkeypatch.setattr(af.followup, "record_follow", lambda num: 1)
    dialed = []
    entries = [
        _entry(id="x", number="+819000000001", consent="拒否"),
        _entry(id="y", number="+819000000002"),
    ]
    res = af.run_batch(
        entries=entries, now=NOON_JST, enabled=True, max_calls=10,
        placer=lambda e: dialed.append(e["number"]) or {"ok": True},
    )
    assert res["placed"] == 1
    assert dialed == ["+819000000002"]


# --- IVR（DTMF）処理 --------------------------------------------------
def test_dtmf_mapping_has_required_options():
    assert af.DTMF["1"] == "日程変更"
    assert af.DTMF["2"] == "担当者と話す"
    assert af.DTMF["9"] == "連絡不要"


def test_dtmf_9_stops_and_adds_dnc(monkeypatch):
    rec = {}
    monkeypatch.setattr(
        af.disposition, "record",
        lambda to, result, add_dnc=None, **kw: rec.update(to=to, result=result, dnc=add_dnc),
    )
    out = af.on_dtmf(_entry(), "9")
    assert out["stop"] is True
    assert rec["dnc"] is True
    assert rec["result"] == af.disposition.DECLINE


def test_dtmf_2_requests_handoff():
    out = af.on_dtmf(_entry(), "2")
    assert out.get("handoff") is True


def test_register_result_answered_stops_followups():
    out = af.register_result(_entry(), answered=True, digit="9")
    assert out["stop"] is True


# --- IVR（音声 TwiML） ------------------------------------------------
def test_followup_message_includes_customer_name():
    msg = af.followup_message(_entry(name="山田"))
    assert "山田さま" in msg
    assert "MartialArts" in msg


def test_intro_twiml_gathers_single_dtmf_with_menu():
    xml = af.twiml_followup_intro(_entry(name="山田"), action_url="/tac/autofollow/dtmf?num=%2B81")
    assert xml.startswith("<?xml")
    assert '<Gather input="dtmf" numDigits="1"' in xml
    assert 'action="/tac/autofollow/dtmf?num=%2B81"' in xml
    # メニュー（1/2/9）を読み上げる
    assert "1" in xml and "2" in xml and "9" in xml
    # 不在時は留守対応して切る
    assert "<Hangup/>" in xml


def test_after_dtmf_1_reschedule_and_hangup():
    xml = af.twiml_after_dtmf("1")
    assert "日程調整" in xml
    assert "<Hangup/>" in xml
    assert "<Dial>" not in xml


def test_after_dtmf_2_dials_handoff_number():
    xml = af.twiml_after_dtmf("2", handoff_number="+819099998888")
    assert "<Dial>+819099998888</Dial>" in xml


def test_after_dtmf_2_without_handoff_falls_back():
    xml = af.twiml_after_dtmf("2", handoff_number="")
    assert "<Dial>" not in xml
    assert "折り返し" in xml


def test_after_dtmf_9_stops_politely():
    xml = af.twiml_after_dtmf("9")
    assert "停止" in xml
    assert "<Hangup/>" in xml


def test_ivr_placer_creates_call_with_intro_twiml(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        af.CONFIG, "public_base_url", "https://tac-martial-arts.fly.dev"
    )
    import tac.outbound as outbound
    monkeypatch.setattr(
        outbound, "_create_call",
        lambda *, to, twiml, **kw: captured.update(to=to, twiml=twiml) or {"ok": True, "sid": "CA1"},
    )
    res = af.ivr_placer(_entry(name="山田"))
    assert res["ok"] is True
    assert captured["to"] == "+819011110000"
    assert "<Gather" in captured["twiml"]
    # 絶対URLでDTMFコールバックが返るようにする
    assert "https://tac-martial-arts.fly.dev/tac/autofollow/dtmf" in captured["twiml"]
