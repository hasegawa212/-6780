"""人間エージェントへのシームレスなハンドオフ（Flex / Studio）。

設計図2「Escalate to a human agent」の実装。AI エージェントが解決しきれない、
あるいは顧客が人間を希望したとき、同じ通話/チャットを維持したまま、AI 生成の
要約と顧客プロファイルを添えて Twilio Flex の担当者へ引き継ぐ。

実現方法:
  - コンテキスト（Summary オペレーターの出力 + traits + 任意属性）をパッケージ化
  - Twilio Studio の「Agent Handoff」テンプレートで作ったフローを起動
      * 音声     : ConversationRelay からのハンドオフ。SendToFlex がタスク属性へ反映
      * SMS/チャット: Studio Execution を開始し、ResumeConversation→SendToFlex
  - 会話ステータスを handed-off にし、二重配信(dual-delivery)を防ぐ

認証情報が無い/ドライランの場合は実呼び出しせず、組み立てたペイロードを返すので
単体テスト・デモが可能。
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .config import CONFIG
from .models import Channel, Conversation, Role, Status
from .operators import Summary


@dataclass
class HandoffResult:
    ok: bool
    handed_off: bool
    payload: dict
    studio_execution_sid: str = ""
    detail: str = ""


@dataclass
class HandoffPackage:
    """Studio/Flex のタスク属性へ渡すコンテキスト。"""

    conversation_sid: str
    channel: str
    reason: str
    summary: str = ""
    headline: str = ""
    intent: str = ""
    customer_id: str = ""
    traits: dict = field(default_factory=dict)
    transcript: str = ""
    attributes: dict = field(default_factory=dict)  # ルーティング用の追加属性

    def task_attributes(self) -> dict:
        """SendToFlex タスク属性（Flex UI のサマリー表示にも使われる）。"""
        attrs = {
            "conversationSid": self.conversation_sid,
            "channel": self.channel,
            "handoffReason": self.reason,
            "virtualAgentSummary": self.summary,
            "headline": self.headline,
            "intent": self.intent,
            "customerId": self.customer_id,
            "customerTraits": self.traits,
        }
        attrs.update(self.attributes)
        return attrs


class HandoffManager:
    """エスカレーションの組み立てと実行。"""

    def __init__(self, *, memory=None, summarizer: Summary | None = None):
        self._memory = memory
        self._summary = summarizer or Summary()

    def build_package(self, conv: Conversation, reason: str,
                      extra: dict | None = None) -> HandoffPackage:
        # AI 生成のサマリーを作る（担当者へ即提供）
        ctx = ""
        traits: dict = {}
        if self._memory and conv.customer_id:
            try:
                ctx = self._memory.enrich(conv.customer_id, reason)
                traits = self._memory.get_or_create(conv.customer_id).traits
            except Exception:
                pass
        summ = self._summary.run(conv.transcript(), context=ctx).output
        return HandoffPackage(
            conversation_sid=conv.sid,
            channel=conv.channel.value,
            reason=reason,
            summary=summ.get("summary", ""),
            headline=summ.get("headline", ""),
            intent=summ.get("intent", ""),
            customer_id=conv.customer_id,
            traits=traits,
            transcript=conv.transcript(),
            attributes=extra or {},
        )

    def escalate(self, conv: Conversation, reason: str,
                 extra: dict | None = None) -> HandoffResult:
        """会話を人間担当者へ引き継ぐ。"""
        pkg = self.build_package(conv, reason, extra)

        # システム発話として記録（監査用）
        conv.add(Role.SYSTEM, f"[ハンドオフ] 理由: {reason} / 件名: {pkg.headline}")

        if CONFIG.dry_run or not CONFIG.has_twilio or not CONFIG.studio_handoff_flow_sid:
            conv.status = Status.HANDED_OFF
            return HandoffResult(
                ok=True, handed_off=True, payload=pkg.task_attributes(),
                detail="dry-run: Studio フロー未起動（認証情報/Flow SID 未設定）",
            )

        try:
            exec_sid = self._trigger_studio(conv, pkg)
            # SendToFlex 実行時に Twilio 側が status=handed-off にし onMessageAdded webhook を削除
            conv.status = Status.HANDED_OFF
            return HandoffResult(
                ok=True, handed_off=True, payload=pkg.task_attributes(),
                studio_execution_sid=exec_sid, detail="Studio ハンドオフフローを起動しました",
            )
        except Exception as e:  # ネットワーク/権限エラー時は会話継続（AIが応対を続ける）
            return HandoffResult(
                ok=False, handed_off=False, payload=pkg.task_attributes(),
                detail=f"ハンドオフ失敗: {e}",
            )

    # --- Studio フロー起動 ---
    def _trigger_studio(self, conv: Conversation, pkg: HandoffPackage) -> str:
        """Studio Execution を開始してコンテキスト属性を渡す。

        音声(ConversationRelay)では通常リレー側のハンドオフシグナルで遷移するが、
        SMS/チャットでは REST で Execution を作成する。ここでは後者を実装し、
        どのチャネルでも task 属性を Studio パラメータとして引き渡せるようにする。
        """
        flow_sid = CONFIG.studio_handoff_flow_sid
        to = self._customer_address(conv)
        from_ = CONFIG.handoff_from
        if not to or not from_:
            raise ValueError("Studio Execution には To/From が必要です（TWILIO_HANDOFF_FROM を設定）")

        url = (
            f"https://studio.twilio.com/v2/Flows/{flow_sid}/Executions"
        )
        params = urllib.parse.urlencode({
            "To": to,
            "From": from_,
            # Studio フロー内の Set Variables / SendToFlex で参照できる属性
            "Parameters": json.dumps(pkg.task_attributes(), ensure_ascii=False),
        }).encode()

        auth = base64.b64encode(
            f"{CONFIG.twilio_account_sid}:{CONFIG.twilio_auth_token}".encode()
        ).decode()
        req = urllib.request.Request(url, data=params, method="POST")
        req.add_header("Authorization", f"Basic {auth}")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        return data.get("sid", "")

    @staticmethod
    def _customer_address(conv: Conversation) -> str:
        for p in conv.participants:
            if p.role == Role.CUSTOMER:
                return p.identity
        return conv.attributes.get("from", "")
