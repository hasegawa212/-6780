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
