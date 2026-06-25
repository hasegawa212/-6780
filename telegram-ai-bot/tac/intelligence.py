"""Conversation Intelligence: 言語演算子を「いつ」走らせるかを司る層。

設計図のトリガー体系を実装:
  - リアルタイム (各発話/メッセージごと) … Sentiment, Next Best Response
  - マイルストーン (非アクティブ化 等)     … 中間 Summary
  - 会話終了時                              … 最終 Summary, Script Adherence

出力は人間エージェント向けシステム（エージェントデスクトップ等）へ配信される。
同じ会話・実行モデルでリアルタイムと会話後の両方を賄う（設計図の核心）。

会話インサイト: 複数会話をまたいでオペレーター結果を集約し、QA/コーチング/分析へ。
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

from .models import Conversation, Role, Status
from .operators import LanguageOperator, standard_operators


class Trigger(str, Enum):
    REALTIME = "realtime"        # 発話ごと
    MILESTONE = "milestone"      # 非アクティブ等の節目
    ON_CLOSE = "on_close"        # 終了/ハングアップ


@dataclass
class Rule:
    """インテリジェンス設定の 1 ルール: どのオペレーターをどのトリガーで実行するか。"""

    operator: str
    trigger: Trigger


@dataclass
class AssistFrame:
    """ある瞬間にエージェントデスクトップへ出す支援情報のスナップショット。"""

    conversation_sid: str
    ts: float = field(default_factory=time.time)
    signals: dict = field(default_factory=dict)  # operator_name -> output


class ConversationIntelligence:
    """ルールセットに基づき会話イベントへオペレーターを適用するエンジン。"""

    def __init__(self, operators: dict[str, LanguageOperator] | None = None,
                 rules: list[Rule] | None = None, *, enricher=None):
        self.operators = operators or standard_operators()
        # 既定のルールセット（設計図の例に対応）
        self.rules = rules or [
            Rule("sentiment", Trigger.REALTIME),
            Rule("next_best_response", Trigger.REALTIME),
            Rule("summary", Trigger.MILESTONE),
            Rule("summary", Trigger.ON_CLOSE),
            Rule("script_adherence", Trigger.ON_CLOSE),
        ]
        # customer_id, query を受けて文脈文字列を返す callable（memory.MemoryStore.enrich）
        self._enricher = enricher
        # 会話インサイト用の集約バッファ
        self._aggregate: list[AssistFrame] = []

    # --- 文脈 ---
    def _context(self, conv: Conversation) -> str:
        if not self._enricher or not conv.customer_id:
            return ""
        last_customer = next(
            (c.text for c in reversed(conv.communications) if c.role == Role.CUSTOMER), ""
        )
        try:
            return self._enricher(conv.customer_id, last_customer)
        except Exception:
            return ""

    # --- トリガー実行 ---
    def run(self, conv: Conversation, trigger: Trigger) -> AssistFrame:
        ctx = self._context(conv)
        transcript = conv.transcript()
        frame = AssistFrame(conversation_sid=conv.sid)
        for rule in self.rules:
            if rule.trigger != trigger:
                continue
            op = self.operators.get(rule.operator)
            if not op:
                continue
            result = op.run(transcript, context=ctx)
            frame.signals[result.name] = result.output
        if frame.signals:
            self._aggregate.append(frame)
        return frame

    def on_utterance(self, conv: Conversation) -> AssistFrame:
        return self.run(conv, Trigger.REALTIME)

    def on_inactive(self, conv: Conversation) -> AssistFrame:
        conv.status = Status.INACTIVE
        return self.run(conv, Trigger.MILESTONE)

    def on_close(self, conv: Conversation) -> AssistFrame:
        frame = self.run(conv, Trigger.ON_CLOSE)
        if conv.status != Status.HANDED_OFF:
            conv.status = Status.CLOSED
        return frame

    # --- 会話インサイト（集約） ---
    def insights(self) -> dict:
        """蓄積した全フレームを横断集計（QA/コーチング/レポート向け）。"""
        sentiments = []
        adherences = []
        risks = []
        intents = defaultdict(int)
        for f in self._aggregate:
            s = f.signals.get("sentiment", {})
            if isinstance(s.get("score"), (int, float)):
                sentiments.append(s["score"])
            sa = f.signals.get("script_adherence", {})
            if isinstance(sa.get("adherence"), (int, float)):
                adherences.append(sa["adherence"])
            if sa.get("compliance_risk"):
                risks.append({"conversation": f.conversation_sid, "risk": sa["compliance_risk"]})
            summ = f.signals.get("summary", {})
            if summ.get("intent"):
                intents[summ["intent"]] += 1

        def avg(xs):
            return round(sum(xs) / len(xs), 3) if xs else None

        return {
            "conversations_analyzed": len({f.conversation_sid for f in self._aggregate}),
            "avg_sentiment": avg(sentiments),
            "avg_script_adherence": avg(adherences),
            "compliance_flags": risks,
            "top_intents": sorted(intents.items(), key=lambda x: x[1], reverse=True),
        }
