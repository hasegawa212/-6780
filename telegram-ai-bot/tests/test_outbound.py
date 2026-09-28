"""アウトバウンド click-to-call ブリッジのテスト（TwiML 生成とガード）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import outbound  # noqa: E402
from tac.config import CONFIG  # noqa: E402


def test_target_twiml_waits_on_hold():
    """相手側は startConferenceOnEnter=false（会議開始まで保留）。"""
    xml = outbound._conf_twiml("room-x", starter=False)
    assert 'startConferenceOnEnter="false"' in xml
    assert "room-x" in xml
    assert "<Conference" in xml


def test_agent_twiml_starts_conference():
    """担当者側は会議を開始し、退出で通話終了。"""
    xml = outbound._conf_twiml("room-x", starter=True)
    assert 'startConferenceOnEnter="true"' in xml
    assert 'endConferenceOnExit="true"' in xml


def test_bridge_requires_to():
    assert outbound.bridge_call("")["ok"] is False


def test_bridge_requires_agent_number():
    old = CONFIG.agent_number
    CONFIG.agent_number = ""
    try:
        r = outbound.bridge_call("+81901234567")
        assert r["ok"] is False
        assert "担当者番号" in r["error"]
    finally:
        CONFIG.agent_number = old


def test_bridge_requires_twilio_creds():
    old_a, old_t, old_c = (
        CONFIG.twilio_account_sid, CONFIG.twilio_auth_token, CONFIG.agent_number,
    )
    CONFIG.twilio_account_sid = ""
    CONFIG.agent_number = "+818094662479"
    try:
        r = outbound.bridge_call("+81901234567")
        assert r["ok"] is False
    finally:
        CONFIG.twilio_account_sid, CONFIG.twilio_auth_token, CONFIG.agent_number = (
            old_a, old_t, old_c,
        )


def test_token_compare_handles_non_ascii():
    """非ASCIIトークンでも例外にならず False を返す（compare_digest の回帰）。"""
    import hmac
    # str のままだと TypeError。bytes 比較なら安全に不一致判定できる。
    assert hmac.compare_digest("日本語".encode(), b"abc") is False
    assert hmac.compare_digest(b"tok", b"tok") is True


def test_outbound_route_auth(monkeypatch=None):
    """/tac/call: トークン未設定=503、非ASCII/誤トークン=401（500にならない）。"""
    import importlib

    try:
        server = importlib.import_module("tac.server")
    except Exception:  # noqa: BLE001 - flask 未導入環境ではスキップ
        return
    client = server.app.test_client()

    # トークン未設定 → 503（fail closed）
    CONFIG.outbound_token = ""
    r = client.post("/tac/call", data={"to": "+81901234567"})
    assert r.status_code == 503

    # 誤ったトークン（非ASCII含む）→ 401（500 でない＝回帰しない）
    CONFIG.outbound_token = "secret-token"
    r = client.post("/tac/call", data={"to": "+81901234567", "token": "誤り"})
    assert r.status_code == 401
    CONFIG.outbound_token = ""


def test_hangup_call_safe_without_creds():
    """認証情報が無くても _hangup_call は例外を出さない（後始末用）。"""
    old = CONFIG.twilio_account_sid
    CONFIG.twilio_account_sid = ""
    try:
        outbound._hangup_call("CAxxxx")  # 例外が出ないこと
        outbound._hangup_call(None)
    finally:
        CONFIG.twilio_account_sid = old
