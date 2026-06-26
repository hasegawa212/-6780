"""品質向上（おもてなしトーン＋御社知識注入）のテスト。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac.config import CONFIG  # noqa: E402
from tac.connector import TACConnector  # noqa: E402
from tac.models import Channel, Conversation  # noqa: E402


def test_system_prompt_has_hospitality_tone():
    conn = TACConnector()
    conv = Conversation(sid="C1", channel=Channel.VOICE)
    s = conn._system(conv, "")
    assert "おもてなし" in s
    assert "復唱" in s  # 大切な情報の確認


def test_business_info_injected():
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("営業時間: 平日 9:00-21:00\n料金: コーヒー 480円〜")
        path = f.name
    CONFIG.business_info_file = path
    try:
        conn = TACConnector()
        conv = Conversation(sid="C2", channel=Channel.VOICE)
        s = conn._system(conv, "")
        assert "当社の正確な情報" in s
        assert "480円" in s
    finally:
        CONFIG.business_info_file = ""


def test_business_info_absent_is_safe():
    CONFIG.business_info_file = ""
    conn = TACConnector()
    conv = Conversation(sid="C3", channel=Channel.VOICE)
    s = conn._system(conv, "")
    assert "当社の正確な情報" not in s
