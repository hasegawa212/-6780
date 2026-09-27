"""録音同意（recording consent）のテスト（TDD）。

録音する場合、通話冒頭で必ず録音の同意告知を入れる（同意なき録音を避ける）。
`consent.prefix()` は録音ONのとき告知文を前置し、OFFなら何も足さない。
発信ブリッジ(outbound)は録音ON時、相手レッグ TwiML に告知の Say を入れる。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import consent, outbound  # noqa: E402
from tac.config import CONFIG  # noqa: E402


def test_notice_empty_when_recording_off():
    old = CONFIG.record_calls
    CONFIG.record_calls = False
    try:
        assert consent.notice() == ""
        assert consent.prefix("こんにちは") == "こんにちは"
    finally:
        CONFIG.record_calls = old


def test_notice_and_prefix_when_recording_on():
    old = CONFIG.record_calls
    CONFIG.record_calls = True
    try:
        assert "録音" in consent.notice()
        out = consent.prefix("ご用件をどうぞ")
        assert out.startswith(CONFIG.recording_consent_text)
        assert "ご用件をどうぞ" in out
    finally:
        CONFIG.record_calls = old


def test_prefix_empty_base_returns_notice_only():
    old = CONFIG.record_calls
    CONFIG.record_calls = True
    try:
        assert consent.prefix("") == CONFIG.recording_consent_text
    finally:
        CONFIG.record_calls = old


def test_outbound_target_twiml_has_consent_when_recording():
    old = CONFIG.record_calls
    try:
        CONFIG.record_calls = True
        xml_on = outbound._conf_twiml("room1", starter=False)
        assert "<Say" in xml_on and "録音" in xml_on

        CONFIG.record_calls = False
        xml_off = outbound._conf_twiml("room1", starter=False)
        assert "録音" not in xml_off
    finally:
        CONFIG.record_calls = old
