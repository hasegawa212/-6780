"""音声の実ハンドオフ（ライブ通話を Flex ワークフローへ <Enqueue> 転送）のテスト。

外部接続なし。escalate が VOICE チャネルでタスク属性を会話に保存し、
server の TwiML が workflowSid 付き <Enqueue> を返すことを検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac.config import CONFIG  # noqa: E402
from tac.handoff import HandoffManager  # noqa: E402
from tac.memory import MemoryStore  # noqa: E402
from tac.models import Channel, Conversation, Role  # noqa: E402


def test_escalate_voice_transfers_to_workflow():
    """VOICE + flex_workflow_sid 設定時は Studio を起動せず属性を保存し handed_off。"""
    CONFIG.flex_workflow_sid = "WWtest123"
    try:
        mem = MemoryStore()
        mem.seed("+81", traits={"name": "山田"})
        mgr = HandoffManager(memory=mem)
        conv = Conversation(sid="CA1", channel=Channel.VOICE, customer_id="+81")
        conv.ensure_participant(Role.CUSTOMER, "+81")
        conv.add(Role.CUSTOMER, "人を出して")
        res = mgr.escalate(conv, "顧客希望", {"priority": "high"})
        assert res.handed_off is True
        # サーバーが <Enqueue><Task> に積むための属性が会話に保存される
        attrs = conv.attributes.get("handoff_task_attributes")
        assert attrs and attrs["handoffReason"] == "顧客希望"
        assert attrs["priority"] == "high"
        assert "音声" in res.detail
    finally:
        CONFIG.flex_workflow_sid = ""


def test_server_voice_handoff_twiml():
    """handed_off の通話で workflowSid 付き <Enqueue> TwiML を返す。"""
    import pytest

    pytest.importorskip("flask")  # flask 未導入環境ではスキップ
    CONFIG.flex_workflow_sid = "WWtest123"
    try:
        from tac import server

        server.conn.start("CA2", Channel.VOICE, customer_identity="+81")
        conv = server.conn.get("CA2")
        conv.attributes["handoff_task_attributes"] = {
            "handoffReason": "苦情", "virtualAgentSummary": "返金希望<注意>",
        }
        resp = server._twiml_handoff("CA2", "担当者におつなぎします。")
        body = resp.get_data(as_text=True)
        assert '<Enqueue workflowSid="WWtest123">' in body
        assert "<Task>" in body and "</Task>" in body
        # 属性内の XML 特殊文字はエスケープされる（< が生で混ざらない）
        assert "返金希望&lt;注意&gt;" in body
    finally:
        CONFIG.flex_workflow_sid = ""


def test_voice_relay_twiml():
    """ConversationRelay 開始の TwiML（<Connect><ConversationRelay>）を返す。"""
    import pytest

    pytest.importorskip("flask")
    from tac import server

    with server.app.test_request_context("/tac/voice-relay", method="POST",
                                         headers={"Host": "example.ngrok-free.app"}):
        resp = server.voice_relay()
        body = resp.get_data(as_text=True)
    assert "<Connect><ConversationRelay" in body
    assert 'url="wss://example.ngrok-free.app/tac/relay"' in body
    assert 'language="ja-JP"' in body
    assert "welcomeGreeting=" in body
