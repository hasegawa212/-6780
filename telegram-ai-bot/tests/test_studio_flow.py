"""Studio ハンドオフフロー生成のテスト（外部接続なし）。

フロー定義の構造（Trigger 起点・ウィジェット連結・SendToFlex のタスク属性）と、
ドライランでの create_or_update_flow を検証する。escalate_to_human が渡す属性キーと
SendToFlex のタスク属性キーが整合していることを確認する。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac.config import CONFIG  # noqa: E402
from tac.handoff import HandoffManager  # noqa: E402
from tac.memory import MemoryStore  # noqa: E402
from tac.models import Channel, Conversation, Role  # noqa: E402
from tac.studio_flow import (  # noqa: E402
    build_flow_definition,
    build_messaging_flow,
    build_voice_flow,
    create_or_update_flow,
)

CONFIG.dry_run = True


def _state_names(defn):
    return [s["name"] for s in defn["states"]]


def test_voice_flow_structure():
    defn = build_voice_flow("WW123", "TC123")
    assert defn["initial_state"] == "Trigger"
    names = _state_names(defn)
    assert names == ["Trigger", "set_attributes", "SendToFlex"]
    # Trigger の incomingCall が set_attributes へ遷移
    trig = defn["states"][0]
    call = next(t for t in trig["transitions"] if t["event"] == "incomingCall")
    assert call["next"] == "set_attributes"
    # SendToFlex に workflow/channel が入る
    flex = defn["states"][-1]
    assert flex["type"] == "send-to-flex"
    assert flex["properties"]["workflow"] == "WW123"
    assert flex["properties"]["channel"] == "TC123"


def test_messaging_flow_widget_chain():
    defn = build_messaging_flow("WW1", "TC1")
    names = _state_names(defn)
    assert names == [
        "Trigger", "fetch_conversationSid", "fetch_serviceSid",
        "ResumeConversation", "SendToFlex",
    ]
    # HTTP→HTTP→Resume→Flex の success 連結
    by = {s["name"]: s for s in defn["states"]}
    nxt = lambda s: next(t["next"] for t in by[s]["transitions"] if t.get("event") == "success")  # noqa: E731
    assert nxt("fetch_conversationSid") == "fetch_serviceSid"
    assert nxt("fetch_serviceSid") == "ResumeConversation"
    assert nxt("ResumeConversation") == "SendToFlex"
    # Conversations API を叩く HTTP ウィジェット
    assert "conversations.twilio.com" in by["fetch_conversationSid"]["properties"]["url"]


def test_task_attributes_match_handoff_keys():
    """SendToFlex のタスク属性キーが handoff の task_attributes と整合する。"""
    defn = build_voice_flow("WW1", "TC1")
    flex = defn["states"][-1]
    attrs = json.loads(flex["properties"]["attributes"])

    # 実際のハンドオフが生成する属性
    mem = MemoryStore()
    mem.seed("+81", traits={"name": "山田"})
    mgr = HandoffManager(memory=mem)
    conv = Conversation(sid="C1", channel=Channel.SMS, customer_id="+81")
    conv.ensure_participant(Role.CUSTOMER, "+81")
    conv.add(Role.CUSTOMER, "人を出して")
    produced = mgr.build_package(conv, "顧客希望", {"priority": "high"}).task_attributes()

    # フロー側が flow.data.<key> として参照する全キーが、ハンドオフ側に存在する
    referenced = {"conversationSid", "channel", "handoffReason", "virtualAgentSummary",
                  "headline", "intent", "customerId", "priority"}
    assert referenced.issubset(set(produced.keys()))
    # フローのタスク属性テンプレートに要約参照が含まれる
    assert attrs["virtualAgentSummary"] == "{{flow.data.virtualAgentSummary}}"


def test_build_flow_definition_dispatch():
    assert build_flow_definition("voice", "WW", "TC")["description"].endswith("(voice)")
    assert build_flow_definition("messaging", "WW", "TC")["description"].endswith("(messaging)")
    for alias in ("sms", "chat"):
        assert build_flow_definition(alias, "WW", "TC")["description"].endswith("(messaging)")
    try:
        build_flow_definition("fax", "WW", "TC")
        raise AssertionError("unknown channel should raise")
    except ValueError:
        pass


def test_create_or_update_flow_dry_run():
    defn = build_voice_flow("WW1", "TC1")
    out = create_or_update_flow("TAC Agent Handoff", defn)
    assert out["dry_run"] is True
    assert out["would_post"]["FriendlyName"] == "TAC Agent Handoff"
    assert out["would_post"]["Status"] == "published"
    assert out["definition"] == defn
