"""成約/高スコア通知（Webhook Notification）のテスト（TDD）。

成約 disposition 時に Webhook へ通知する。TAC_NOTIFY_WEBHOOK 未設定なら
安全にスキップ（デフォルトOFF）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac.config import CONFIG  # noqa: E402


def _tmp_stores():
    lf = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    lf.close()
    CONFIG.calllog_file = lf.name
    df = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    df.close()
    CONFIG.dnc_file = df.name


def test_notify_skips_when_no_webhook():
    """TAC_NOTIFY_WEBHOOK 未設定なら送信しない。"""
    from tac import notify
    old = CONFIG.notify_webhook
    CONFIG.notify_webhook = ""
    try:
        result = notify.send(to="+819012345678", result="成約")
        assert result is False
    finally:
        CONFIG.notify_webhook = old


def test_notify_sends_when_webhook_set(monkeypatch):
    """TAC_NOTIFY_WEBHOOK 設定時は JSON POST する。"""
    from tac import notify
    old = CONFIG.notify_webhook
    CONFIG.notify_webhook = "https://hooks.example.com/test"
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = json.loads(req.data.decode("utf-8"))
        captured["method"] = req.method
        captured["content_type"] = req.get_header("Content-type")
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.close = MagicMock()
        return mock_resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    try:
        result = notify.send(to="+819012345678", result="成約")
        assert result is True
        assert captured["url"] == "https://hooks.example.com/test"
        assert captured["data"]["to"] == "+819012345678"
        assert "成約" in captured["data"]["text"]
        assert captured["method"] == "POST"
        assert captured["content_type"] == "application/json"
    finally:
        CONFIG.notify_webhook = old


def test_notify_includes_note(monkeypatch):
    """メモ付き通知が正しく送られる。"""
    from tac import notify
    old = CONFIG.notify_webhook
    CONFIG.notify_webhook = "https://hooks.example.com/test"
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["data"] = json.loads(req.data.decode("utf-8"))
        mock_resp = MagicMock()
        mock_resp.close = MagicMock()
        return mock_resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    try:
        notify.send(to="+819012345678", result="成約", note="初回面談済み")
        assert captured["data"]["note"] == "初回面談済み"
    finally:
        CONFIG.notify_webhook = old


def test_notify_failure_is_swallowed(monkeypatch):
    """通知失敗は握り潰す（通話処理を止めない）。"""
    from tac import notify
    old = CONFIG.notify_webhook
    CONFIG.notify_webhook = "https://hooks.example.com/test"

    def fake_urlopen(req, timeout=None):
        raise Exception("network error")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    try:
        result = notify.send(to="+819012345678", result="成約")
        assert result is False
    finally:
        CONFIG.notify_webhook = old


def test_disposition_seiyaku_triggers_notify(monkeypatch):
    """成約 disposition 時に notify.send が呼ばれる。"""
    from tac import disposition, notify
    _tmp_stores()
    old = CONFIG.notify_webhook
    CONFIG.notify_webhook = "https://hooks.example.com/test"
    calls = []
    monkeypatch.setattr(notify, "send", lambda **kw: calls.append(kw) or False)
    try:
        disposition.record("+819012345678", "成約")
        assert len(calls) == 1
        assert calls[0]["to"] == "+819012345678"
        assert calls[0]["result"] == "成約"
    finally:
        CONFIG.notify_webhook = old


def test_disposition_non_seiyaku_no_notify(monkeypatch):
    """成約以外の disposition では notify.send を呼ばない。"""
    from tac import disposition, notify
    _tmp_stores()
    old = CONFIG.notify_webhook
    CONFIG.notify_webhook = "https://hooks.example.com/test"
    calls = []
    monkeypatch.setattr(notify, "send", lambda **kw: calls.append(kw) or False)
    try:
        disposition.record("+819012345678", "検討")
        assert len(calls) == 0
    finally:
        CONFIG.notify_webhook = old
