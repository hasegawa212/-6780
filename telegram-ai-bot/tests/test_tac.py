"""TAC パッケージの単体テスト（LLM/Twilio 認証情報なしで動く）。

外部呼び出しを行わずに、データモデル・メモリ・ハンドオフのパッケージ化・
インテリジェンスの集約・ツールレジストリ・コネクタのライフサイクルを検証する。
既存の test_helpers.py と同じく、telegram-ai-bot をパスに通して import する。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac.config import CONFIG  # noqa: E402
from tac.connector import TACConnector  # noqa: E402
from tac.handoff import HandoffManager  # noqa: E402
from tac.intelligence import ConversationIntelligence, Trigger  # noqa: E402
from tac.memory import KnowledgeBase, MemoryStore  # noqa: E402
from tac.models import Channel, Conversation, Role, Status  # noqa: E402
from tac.operators import OperatorResult  # noqa: E402
from tac.tools import build_default_registry  # noqa: E402

CONFIG.dry_run = True  # 外部呼び出しを抑止


# ---------- models ----------
def test_conversation_transcript_and_messages():
    conv = Conversation(sid="C1", channel=Channel.SMS)
    conv.add(Role.CUSTOMER, "解約したい")
    conv.add(Role.AI_AGENT, "承知しました")
    assert "顧客: 解約したい" in conv.transcript()
    msgs = conv.llm_messages()
    assert msgs == [
        {"role": "user", "content": "解約したい"},
        {"role": "assistant", "content": "承知しました"},
    ]
    assert conv.turn_count == 2


def test_conversation_skips_empty():
    conv = Conversation(sid="C2", channel=Channel.VOICE)
    conv.add(Role.CUSTOMER, "   ")
    assert conv.turn_count == 0


# ---------- memory ----------
def test_memory_enrich_includes_traits_observations_knowledge():
    kb = KnowledgeBase()
    kb.add("解約は30日前の通知が必要です。", "返金ポリシーは初回購入のみ対象。")
    mem = MemoryStore(knowledge=kb)
    mem.seed("+8190", traits={"name": "山田", "plan": "premium"},
             observations=["前回は請求について問い合わせ"])
    ctx = mem.enrich("+8190", "解約 通知")
    assert "山田" in ctx and "premium" in ctx
    assert "請求" in ctx
    assert "解約は30日前" in ctx


def test_knowledge_local_retrieval_ranks():
    kb = KnowledgeBase()
    kb.add("パスワードのリセット手順", "解約の手順", "配送について")
    hits = kb.retrieve("解約", k=2)
    assert any("解約" in h for h in hits)


# ---------- handoff ----------
def test_handoff_package_attributes():
    mem = MemoryStore()
    mem.seed("+8190", traits={"name": "山田"})
    mgr = HandoffManager(memory=mem)
    conv = Conversation(sid="C3", channel=Channel.SMS, customer_id="+8190")
    conv.ensure_participant(Role.CUSTOMER, "+8190")
    conv.add(Role.CUSTOMER, "話にならない、人を出して")
    pkg = mgr.build_package(conv, "顧客が人間を希望", {"priority": "high"})
    attrs = pkg.task_attributes()
    assert attrs["conversationSid"] == "C3"
    assert attrs["handoffReason"] == "顧客が人間を希望"
    assert attrs["priority"] == "high"
    assert attrs["customerTraits"] == {"name": "山田"}


def test_handoff_escalate_dry_run_sets_status():
    mgr = HandoffManager(memory=MemoryStore())
    conv = Conversation(sid="C4", channel=Channel.VOICE, customer_id="+81")
    conv.ensure_participant(Role.CUSTOMER, "+81")
    conv.add(Role.CUSTOMER, "苦情です")
    result = mgr.escalate(conv, "苦情対応")
    assert result.ok and result.handed_off
    assert conv.status == Status.HANDED_OFF
    # 監査用のシステム発話が記録される
    assert any(c.role == Role.SYSTEM for c in conv.communications)


# ---------- intelligence (operators をスタブ) ----------
class _StubOp:
    def __init__(self, name, output):
        self.name = name
        self._out = output

    def run(self, transcript, *, context=""):
        return OperatorResult(self.name, self._out)


def test_intelligence_triggers_and_insights():
    ops = {
        "sentiment": _StubOp("sentiment", {"label": "negative", "score": -0.6, "shift": ""}),
        "summary": _StubOp("summary", {"headline": "解約", "summary": "解約希望",
                                       "intent": "解約", "resolution": "未解決",
                                       "action_items": ["本人確認"]}),
        "script_adherence": _StubOp("script_adherence",
                                    {"adherence": 0.8, "met": [], "missed": [],
                                     "compliance_risk": "本人確認未実施"}),
    }
    ci = ConversationIntelligence(operators=ops)
    conv = Conversation(sid="C5", channel=Channel.SMS)
    conv.add(Role.CUSTOMER, "解約したい")

    rt = ci.run(conv, Trigger.REALTIME)
    assert rt.signals["sentiment"]["label"] == "negative"

    closed = ci.on_close(conv)
    assert "summary" in closed.signals and "script_adherence" in closed.signals

    ins = ci.insights()
    assert ins["conversations_analyzed"] == 1
    assert ins["avg_sentiment"] == -0.6
    assert ins["compliance_flags"]
    assert ("解約", 1) in ins["top_intents"]


# ---------- tools ----------
def test_registry_escalate_tool():
    mem = MemoryStore()
    mgr = HandoffManager(memory=mem)
    conv = Conversation(sid="C6", channel=Channel.SMS, customer_id="+1")
    conv.ensure_participant(Role.CUSTOMER, "+1")
    conv.add(Role.CUSTOMER, "人間を出して")
    reg = build_default_registry(handoff_manager=mgr, conversation_getter=lambda: conv)
    names = [s["name"] for s in reg.specs()]
    assert "escalate_to_human" in names and "schedule_callback" in names
    out = reg.call("escalate_to_human", reason="顧客希望", priority="high")
    assert out["handed_off"] is True
    assert conv.status == Status.HANDED_OFF


def test_registry_unknown_tool():
    reg = build_default_registry(handoff_manager=HandoffManager(),
                                 conversation_getter=lambda: None)
    assert "error" in reg.call("nope")


# ---------- connector lifecycle (LLM 無しのフォールバック経路) ----------
def test_connector_lifecycle_without_llm():
    conn = TACConnector()
    conn._client = None  # LLM 未設定を強制
    conv = conn.start("C7", Channel.VOICE, customer_identity="+8190", goal="サポート")
    assert conv.customer_id == "+8190"
    assert any(p.role == Role.AI_AGENT for p in conv.participants)

    res = conn.handle("C7", "こんにちは")
    assert isinstance(res.text, str) and res.text
    assert conv.turn_count >= 2  # 顧客 + AI

    signals = conn.close("C7")
    assert conn.get("C7").status in (Status.CLOSED, Status.HANDED_OFF)
    assert isinstance(signals, dict)


def test_connector_blocks_after_handoff():
    conn = TACConnector()
    conn._client = None
    conn.start("C8", Channel.SMS, customer_identity="+1")
    conn.get("C8").status = Status.HANDED_OFF
    res = conn.handle("C8", "まだいる？")
    assert res.handed_off and res.text == ""
