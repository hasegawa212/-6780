"""架電サマリー（Call Summary）のテスト（TDD）。

架電記録（calls.jsonl）を集計し、「合計・結果別件数・ユニーク番号数」を一目で
把握できるようにする。DNC でブロックした件数を数字で示せる＝コンプライアンス
（断った相手に再発信していない）報告の土台になる。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import calllog  # noqa: E402
from tac.config import CONFIG  # noqa: E402


def _tmp_log():
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    f.close()
    CONFIG.calllog_file = f.name
    return f.name


def test_summary_empty_is_zero():
    _tmp_log()
    s = calllog.summary()
    assert s["total"] == 0
    assert s["by_status"] == {}
    assert s["unique_numbers"] == 0


def test_summary_counts_by_status():
    _tmp_log()
    calllog.append("outbound", "+81901112222", "dialed")
    calllog.append("outbound", "+81903334444", "blocked")
    calllog.append("outbound", "+81905556666", "dialed")
    calllog.append("outbound", "+81907778888", "error", stage="target")
    s = calllog.summary()
    assert s["total"] == 4
    assert s["by_status"]["dialed"] == 2
    assert s["by_status"]["blocked"] == 1
    assert s["by_status"]["error"] == 1


def test_summary_unique_numbers():
    _tmp_log()
    # 同じ番号に 2 回（例: dialed のあと DNC 登録して blocked）→ ユニークは 1
    calllog.append("outbound", "+81901112222", "dialed")
    calllog.append("outbound", "+81901112222", "blocked")
    calllog.append("outbound", "+81903334444", "dialed")
    s = calllog.summary()
    assert s["total"] == 3
    assert s["unique_numbers"] == 2


def test_summary_accepts_explicit_records():
    """records を明示で渡せる（ファイル I/O 無しで集計できる純関数）。"""
    recs = [
        {"to": "+81901112222", "status": "dialed"},
        {"to": "+81903334444", "status": "blocked"},
    ]
    s = calllog.summary(records=recs)
    assert s["total"] == 2
    assert s["by_status"] == {"dialed": 1, "blocked": 1}
    assert s["unique_numbers"] == 2


def test_summary_route_requires_token_and_returns_summary():
    """GET /tac/calls/summary: 無トークン=401、正しいトークンで集計を返す。"""
    import importlib

    try:
        server = importlib.import_module("tac.server")
    except Exception:  # noqa: BLE001 - flask 未導入環境ではスキップ
        return
    _tmp_log()
    calllog.append("outbound", "+81905556666", "dialed")
    calllog.append("outbound", "+81907778888", "blocked")
    CONFIG.outbound_token = "tok-summary-test"
    client = server.app.test_client()
    try:
        assert client.get("/tac/calls/summary").status_code == 401
        r = client.get("/tac/calls/summary", query_string={"token": "tok-summary-test"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["summary"]["total"] == 2
        assert body["summary"]["by_status"]["dialed"] == 1
        assert body["summary"]["by_status"]["blocked"] == 1
    finally:
        CONFIG.outbound_token = ""
