"""接続チェッカーのテスト（ネットワークなし）。

秘密のマスク表示と、設定状況レポートが秘密を平文で漏らさないことを検証する。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import check  # noqa: E402
from tac.config import CONFIG  # noqa: E402


def test_mask_basic():
    assert check.mask("") == "(未設定)"
    assert check.mask("ACdeadbeefdeadbeefdeadbeefdeadbeef") == "ACde…beef"
    assert check.mask("short") == "•••••"  # head+tail 以下は全伏字


def test_status_report_masks_secrets(monkeypatch=None):
    # すべてダミーのプレースホルダ（実在しない値）
    CONFIG.twilio_account_sid = "ACdeadbeefdeadbeefdeadbeefdeadbeef"
    CONFIG.twilio_auth_token = "faketoken0123456789abcdef0123beef"
    CONFIG.anthropic_key = "sk-ant-fakeplaceholdervalue0000"
    try:
        rep = check.status_report()
        blob = json.dumps(rep, ensure_ascii=False)
        # フルの秘密が一切現れない
        assert "faketoken0123456789abcdef0123beef" not in blob
        assert "sk-ant-fakeplaceholdervalue0000" not in blob
        # マスク済みの断片は出る
        assert rep["twilio"]["account_sid"] == "ACde…beef"
        assert rep["twilio"]["configured"] is True
    finally:
        CONFIG.twilio_account_sid = ""
        CONFIG.twilio_auth_token = ""
        CONFIG.anthropic_key = ""


def test_live_check_requires_twilio():
    CONFIG.twilio_account_sid = ""
    CONFIG.twilio_auth_token = ""
    out = check.live_check()
    assert out["ok"] is False
    assert "未設定" in out["error"]
