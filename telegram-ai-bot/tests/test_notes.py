"""通話メモ（Call Notes）のテスト（TDD）。

通話後のメモを call record に保存する。外部送信はenv設定時のみ。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac.config import CONFIG  # noqa: E402


def _tmp_log():
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    f.close()
    CONFIG.calllog_file = f.name
    return f.name


def test_save_note():
    from tac import notes
    _tmp_log()
    res = notes.save("+819012345678", "お客様は午後連絡希望")
    assert res["ok"] is True
    assert res["to"] == "+819012345678"
    assert res["note"] == "お客様は午後連絡希望"


def test_save_note_recorded_in_calllog():
    from tac import calllog, notes
    _tmp_log()
    notes.save("+819012345678", "テストメモ")
    rec = calllog.recent()[0]
    assert rec["status"] == "note"
    assert rec["note"] == "テストメモ"
    assert rec["to"] == "+819012345678"


def test_save_empty_note_rejected():
    from tac import notes
    _tmp_log()
    res = notes.save("+819012345678", "")
    assert res["ok"] is False


def test_save_empty_to_rejected():
    from tac import notes
    _tmp_log()
    res = notes.save("", "メモ内容")
    assert res["ok"] is False


def test_note_does_not_send_webhook_by_default():
    """TAC_NOTIFY_WEBHOOK 未設定なら外部送信しない。"""
    from tac import notes
    _tmp_log()
    old = CONFIG.notify_webhook
    CONFIG.notify_webhook = ""
    try:
        res = notes.save("+819012345678", "メモ", send_webhook=True)
        assert res["ok"] is True
        assert res.get("notified") is False
    finally:
        CONFIG.notify_webhook = old


def test_note_route_requires_token():
    import importlib
    try:
        server = importlib.import_module("tac.server")
    except Exception:
        return
    _tmp_log()
    CONFIG.outbound_token = "tok-notes-test"
    client = server.app.test_client()
    try:
        assert client.post("/tac/calls/note").status_code == 401
        r = client.post("/tac/calls/note", data={
            "token": "tok-notes-test",
            "to": "+819012345678",
            "note": "テストメモ",
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
    finally:
        CONFIG.outbound_token = ""
