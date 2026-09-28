"""通話結果ラベル（Disposition）＋ DNC ワンクリック登録のテスト（TDD）。

架電の結果（成約/検討/不在/拒否）を記録する。「拒否」なら自動で DNC に登録して
再勧誘を仕組みで防ぐ（特定商取引法の再勧誘禁止への配慮）。明示指定で上書きも可能。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import calllog, disposition, dnc  # noqa: E402
from tac.config import CONFIG  # noqa: E402


def _tmp_stores():
    lf = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    lf.close()
    CONFIG.calllog_file = lf.name
    df = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    df.close()
    CONFIG.dnc_file = df.name


def test_decline_auto_adds_to_dnc_and_logs():
    _tmp_stores()
    res = disposition.record("+81901112222", "拒否")
    assert res["ok"] is True
    assert res["dnc_added"] is True
    assert dnc.contains("+81901112222")
    rec = calllog.recent()
    assert rec and rec[0]["status"] == "disposition"
    assert rec[0]["disposition"] == "拒否"
    assert rec[0]["to"] == "+81901112222"


def test_non_decline_does_not_add_dnc():
    _tmp_stores()
    res = disposition.record("+81903334444", "検討")
    assert res["dnc_added"] is False
    assert not dnc.contains("+81903334444")
    # 記録は残る
    assert calllog.recent()[0]["disposition"] == "検討"


def test_explicit_add_dnc_true_overrides():
    _tmp_stores()
    res = disposition.record("+81905556666", "検討", add_dnc=True)
    assert res["dnc_added"] is True
    assert dnc.contains("+81905556666")


def test_explicit_add_dnc_false_overrides_decline():
    _tmp_stores()
    res = disposition.record("+81907778888", "拒否", add_dnc=False)
    assert res["dnc_added"] is False
    assert not dnc.contains("+81907778888")


def test_empty_to_is_rejected():
    _tmp_stores()
    res = disposition.record("", "拒否")
    assert res["ok"] is False


def test_disposition_route_requires_token_and_records(monkeypatch=None):
    """POST /tac/calls/disposition: 無トークン=401、正しいトークンで記録＆DNC登録。"""
    import importlib

    try:
        server = importlib.import_module("tac.server")
    except Exception:  # noqa: BLE001 - flask 未導入環境ではスキップ
        return
    _tmp_stores()
    CONFIG.outbound_token = "tok-disp-test"
    client = server.app.test_client()
    try:
        assert client.post("/tac/calls/disposition").status_code == 401
        r = client.post(
            "/tac/calls/disposition",
            data={"token": "tok-disp-test", "to": "+81901234567", "result": "拒否"},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["dnc_added"] is True
        assert dnc.contains("+81901234567")
    finally:
        CONFIG.outbound_token = ""
