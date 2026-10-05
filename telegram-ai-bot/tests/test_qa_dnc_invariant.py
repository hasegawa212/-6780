"""QA（2026-10-05）: 「抑止中の相手には新しい発信を絶対に生まない」を破る経路の回帰テスト。

番号はすべて架空。Twilio は呼ばない（_create_call を記録だけするスタブに差し替える）。
各テストは、修正前の実装で失敗することを確認してから修正している（docs は tac-next/docs/QA_REPORT.md）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import autofollow, dnc, followup, outbound, rate_limit  # noqa: E402
from tac.config import CONFIG  # noqa: E402

TARGET = "+819000000123"  # 架空


@pytest.fixture
def dialer(monkeypatch, tmp_path):
    """発信を記録するだけのスタブ＋一時ファイルの DNC／架電記録／台帳。"""
    placed: list[str] = []

    def fake_create_call(to, twiml, **_kw):
        placed.append(to)
        return {"ok": True, "sid": f"CA{len(placed)}"}

    monkeypatch.setattr(outbound, "_create_call", fake_create_call)
    monkeypatch.setattr(CONFIG, "dnc_file", str(tmp_path / "dnc.txt"))
    monkeypatch.setattr(CONFIG, "calllog_file", str(tmp_path / "calls.jsonl"))
    monkeypatch.setattr(CONFIG, "follow_file", str(tmp_path / "follow.json"))
    monkeypatch.setattr(CONFIG, "agent_number", "+819000000999")
    monkeypatch.setattr(CONFIG, "enforce_call_hours", False)
    monkeypatch.setattr(CONFIG, "disclosure_enabled", False)
    monkeypatch.setattr(CONFIG, "daily_call_cap", 0)
    return placed


# --- QA-TAC-01: DNC が読めないと「禁止なし」になる（fail open） -------------------------------
def test_unreadable_dnc_file_blocks_outbound(dialer, tmp_path):
    os.mkdir(tmp_path / "dir_as_file")
    CONFIG.dnc_file = str(tmp_path / "dir_as_file")  # 読めない（IsADirectoryError）
    r = outbound.bridge_call(TARGET)
    assert r["ok"] is False and r.get("blocked") is True
    assert dialer == []


def test_dnc_on_unmounted_volume_blocks_outbound(dialer):
    CONFIG.dnc_file = "/nonexistent-volume/data/dnc.txt"  # ボリューム未マウント相当
    r = outbound.bridge_call(TARGET)
    assert r.get("blocked") is True
    assert dialer == []


def test_first_boot_without_dnc_file_still_allows_calls(dialer):
    # ディレクトリはあるがファイルはまだ無い = 初回起動。これは正常（登録 0 件）。
    r = outbound.bridge_call(TARGET)
    assert r["ok"] is True
    assert dialer == [TARGET, CONFIG.agent_number]


# --- QA-TAC-02: 国内表記で登録した DNC が E.164 の発信に効かない ------------------------------
@pytest.mark.parametrize("registered", ["090-0000-0123", "09000000123", "０９０－００００－０１２３", "+81 90 0000 0123"])
def test_dnc_registered_in_any_notation_blocks_e164_call(dialer, registered):
    dnc.add(registered)
    r = outbound.bridge_call(TARGET)
    assert r.get("blocked") is True, registered
    assert dialer == []


def test_dnc_registered_in_e164_blocks_local_notation_call(dialer):
    dnc.add(TARGET)
    r = outbound.bridge_call("090-0000-0123")
    assert r.get("blocked") is True
    assert dialer == []


# --- QA-TAC-03: 「連絡停止」に分類しても DNC に入らない ---------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "今後の連絡は不要です",
        "連絡不要とのこと",
        "日程変更の件ですが、もう電話しないでください",  # 再調整語があっても拒否が勝つ
        "二度とかけてこないでと言われた",
        "迷惑なので営業電話はやめてほしい",
    ],
)
def test_refusal_records_are_classified_as_stop(text):
    assert followup.classify(text)[0] == "連絡停止", text


def test_follow_ledger_stop_category_blocks_every_dial_path(dialer):
    followup.ingest([{"number": "090-0000-0123", "record": "今後の連絡は不要です", "source": {"ref": "qa-1"}}])
    entry = followup.load()[0]
    assert entry["category"] == "連絡停止"
    r = outbound.bridge_call(TARGET)
    assert r.get("blocked") is True
    assert dialer == []


def test_manual_correction_to_stop_blocks_outbound(dialer):
    followup.ingest([{"number": "090-0000-0123", "record": "来週以降で日程を再調整したい", "source": {"ref": "qa-2"}}])
    entry = followup.load()[0]
    assert followup.correct(entry["id"], "連絡停止") is True
    assert outbound.bridge_call(TARGET).get("blocked") is True
    assert dialer == []


# --- QA-TAC-04: 自動フォローの発信が 1 日上限に数えられない ------------------------------------
def test_autofollow_calls_count_toward_daily_cap(dialer):
    result = autofollow.ivr_placer({"number": TARGET, "name": "架空 太郎", "category": "不在"})
    assert result.get("ok") is True
    assert rate_limit.dialed_today() == 1


# --- QA-TAC-05: 自動フォロー判定も DNC が読めないと素通り -----------------------------------
def test_autofollow_eligibility_fails_closed_when_dnc_unreadable(dialer, tmp_path):
    os.mkdir(tmp_path / "dnc_dir")
    CONFIG.dnc_file = str(tmp_path / "dnc_dir")
    entry = {"number": TARGET, "category": "不在", "consent": "", "follow_count": 0}
    assert followup.can_follow(entry, cap=2) is False


def _callable_entries(n: int) -> list[dict]:
    return [
        {"number": f"+8190000001{i:02d}", "name": f"架空{i}", "category": "不在",
         "consent": "", "follow_count": 0, "last_follow_at": ""}
        for i in range(n)
    ]


# --- QA-TAC-07: 自動フォロー本体の判定も DNC が読めないと素通り ---------------------------------
def test_autofollow_selection_fails_closed_when_dnc_unreadable(dialer, tmp_path):
    os.mkdir(tmp_path / "dnc_dir2")
    CONFIG.dnc_file = str(tmp_path / "dnc_dir2")
    entry, decision = autofollow.select_next(_callable_entries(1), enabled=True)
    assert entry is None, decision
    assert autofollow.run_once(entries=_callable_entries(1), enabled=True)["placed"] is False
    assert dialer == []


# --- QA-TAC-08: 連続オート発信が 1 日上限を超える ---------------------------------------------
def test_autofollow_batch_never_exceeds_daily_cap(dialer):
    CONFIG.daily_call_cap = 2
    out = autofollow.run_batch(entries=_callable_entries(5), enabled=True, max_calls=10)
    assert out["placed"] <= 2, out
    assert len(dialer) <= 2


# --- QA-TAC-09: API とスケジューラが同時に run_once すると同じ相手へ二重発信 -------------------
def test_concurrent_run_once_never_double_dials_same_person(dialer, monkeypatch):
    import threading
    import time

    entries = _callable_entries(1)  # 共有の台帳（同じ相手が1人だけ）
    real = outbound._create_call

    def slow_create_call(to, twiml, **kw):
        time.sleep(0.05)  # 発信 API の往復時間（この間に別スレッドが同じ相手を選ぶ）
        return real(to, twiml, **kw)

    monkeypatch.setattr(outbound, "_create_call", slow_create_call)
    monkeypatch.setattr(followup, "record_follow", lambda number: 1)  # 台帳ファイルは使わない

    def worker():
        autofollow.run_once(entries=entries, enabled=True)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert dialer.count("+819000000100") <= 1, dialer
