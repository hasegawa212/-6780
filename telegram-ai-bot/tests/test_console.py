"""運用ダッシュボード（HTML Console）のテスト（TDD）。

架電記録・サマリー・DNC を1枚の HTML にまとめて表示する。curl を使わず
ブラウザで運用状況を目で確認できる。値はエスケープして描画する（安全）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import console  # noqa: E402
from tac.config import CONFIG  # noqa: E402


def test_render_contains_html_and_summary():
    html = console.render(
        summary={"total": 3, "by_status": {"dialed": 2, "blocked": 1}, "unique_numbers": 2},
        calls=[{"ts": "2026-09-28T00:00:00+00:00", "direction": "outbound",
                "to": "+81901112222", "status": "dialed"}],
        dnc_numbers=["+81903334444"],
    )
    assert "<html" in html.lower()
    assert "</html>" in html.lower()
    # サマリーの数値が出る
    assert "3" in html
    assert "dialed" in html
    # 架電記録の番号・DNC の番号が出る
    assert "+81901112222" in html
    assert "+81903334444" in html


def test_render_escapes_values():
    """値に HTML 特殊文字が来てもエスケープされる（インジェクション防止）。"""
    html = console.render(
        summary={"total": 1, "by_status": {"dialed": 1}, "unique_numbers": 1},
        calls=[{"ts": "t", "direction": "outbound", "to": "<script>x</script>",
                "status": "dialed"}],
        dnc_numbers=[],
    )
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_handles_empty():
    html = console.render(
        summary={"total": 0, "by_status": {}, "unique_numbers": 0},
        calls=[],
        dnc_numbers=[],
    )
    assert "<html" in html.lower()


def test_console_route_requires_token_and_returns_html():
    """GET /tac/console: 無トークン=401、正しいトークンで HTML を返す（flask無ければskip）。"""
    import importlib

    try:
        server = importlib.import_module("tac.server")
    except Exception:  # noqa: BLE001 - flask 未導入環境ではスキップ
        return
    from tac import calllog

    lf = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    lf.close()
    CONFIG.calllog_file = lf.name
    calllog.append("outbound", "+81905556666", "dialed")
    CONFIG.outbound_token = "tok-console-test"
    client = server.app.test_client()
    try:
        assert client.get("/tac/console").status_code == 401
        r = client.get("/tac/console", query_string={"token": "tok-console-test"})
        assert r.status_code == 200
        assert "text/html" in r.content_type
        assert "+81905556666" in r.get_data(as_text=True)
    finally:
        CONFIG.outbound_token = ""
