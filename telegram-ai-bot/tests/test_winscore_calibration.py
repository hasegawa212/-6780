"""成約確度エンジンのキャリブレーション tac/winscore.calibrate の TDD。

「S/A… と採点した客が、実際どれくらい成約したか」を実データ(架電記録の
disposition)で突き合わせ、グレード別の的中率を出す。重みが本当に効いているかを
運用で検証する土台（AGENTS.md: 重みは仮説、運用で検証）。架空の数字は作らない。

- 結果(disposition)が付いた＝「接触できた」客だけを母数にする。
- won = 成約 の数。rate = won / reached。
- 拒否(除外)は対象外。
実行: cd telegram-ai-bot && python3 -m pytest tests/test_winscore_calibration.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import winscore  # noqa: E402


def _entry(number, **kw):
    base = {"name": "客", "number": number, "area": "", "category": "不在",
            "record": "", "follow_count": 0}
    base.update(kw)
    return base


def _disp(number, result):
    return {"status": "disposition", "to": number, "disposition": result}


# S 相当（再調整希望×宇都宮×前向き）と B 相当（不在×水戸）を用意
_HIGH = _entry("+819000000001", category="再調整希望", area="宇都宮市", record="前向き 予算")
_MID = _entry("+819000000002", category="不在", area="水戸市")


def test_only_reached_counted():
    # HIGH は成約、MID は結果未登録（未接触）→ 母数は HIGH のみ
    entries = [_HIGH, _MID]
    records = [_disp("+819000000001", "成約")]
    rep = winscore.calibrate(entries, records)
    assert rep["overall"]["reached"] == 1
    assert rep["overall"]["won"] == 1


def test_won_counts_only_seiyaku():
    entries = [_HIGH, _MID]
    records = [_disp("+819000000001", "成約"), _disp("+819000000002", "検討")]
    rep = winscore.calibrate(entries, records)
    assert rep["overall"]["reached"] == 2
    assert rep["overall"]["won"] == 1
    assert abs(rep["overall"]["rate"] - 0.5) < 1e-9


def test_rate_by_grade():
    g_high = winscore.score_lead(_HIGH)["grade"]
    entries = [_HIGH, _MID]
    records = [_disp("+819000000001", "成約"), _disp("+819000000002", "検討")]
    rep = winscore.calibrate(entries, records)
    assert rep["by_grade"][g_high]["reached"] == 1
    assert rep["by_grade"][g_high]["won"] == 1
    assert rep["by_grade"][g_high]["rate"] == 1.0


def test_excluded_not_counted():
    stop = _entry("+819000000003", category="連絡停止", area="宇都宮市")
    entries = [stop]
    records = [_disp("+819000000003", "成約")]
    rep = winscore.calibrate(entries, records)
    assert rep["overall"]["reached"] == 0


def test_empty_is_safe():
    rep = winscore.calibrate([], [])
    assert rep["overall"]["reached"] == 0
    assert rep["overall"]["won"] == 0
    assert rep["overall"]["rate"] == 0.0


def test_number_normalization_match():
    # 台帳は E.164、記録はハイフン表記でも同一人物として突き合わせる
    e = _entry("+819012345678", category="再調整希望", area="宇都宮市")
    records = [_disp("090-1234-5678", "成約")]
    rep = winscore.calibrate([e], records)
    assert rep["overall"]["reached"] == 1
    assert rep["overall"]["won"] == 1


def test_calibration_route(monkeypatch):
    import importlib
    try:
        server = importlib.import_module("tac.server")
        followup = importlib.import_module("tac.followup")
        calllog = importlib.import_module("tac.calllog")
    except Exception:  # noqa: BLE001
        import pytest
        pytest.skip("flask 未導入")
    from tac.config import CONFIG
    monkeypatch.setattr(followup, "load", lambda category="": [_HIGH])
    monkeypatch.setattr(calllog, "_all", lambda: [_disp("+819000000001", "成約")])
    prev = CONFIG.outbound_token
    CONFIG.outbound_token = "tok-cal-test"
    try:
        client = server.app.test_client()
        assert client.get("/tac/winscore/calibration").status_code == 401
        r = client.get("/tac/winscore/calibration", query_string={"token": "tok-cal-test"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["overall"]["reached"] == 1
        assert body["overall"]["won"] == 1
    finally:
        CONFIG.outbound_token = prev
