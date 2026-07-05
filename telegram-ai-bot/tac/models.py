"""Conversation Orchestrator の記録モデル。

設計図の「会話そのもの / 個別のコミュニケーション / 役割付き参加者」を、チャネルに
依存しない単一の Conversation オブジェクトとして表現する。音声・SMS・WhatsApp・
チャットのいずれであっても下流（インテリジェンス、メモリ、ハンドオフ）はこの
1 つのモデルだけを参照すればよい。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


class Channel(StrEnum):
    VOICE = "voice"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    CHAT = "chat"


class Role(StrEnum):
    CUSTOMER = "customer"
    AI_AGENT = "ai_agent"
    HUMAN_AGENT = "human_agent"
    SYSTEM = "system"


class Status(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"      # 一定時間発話なし → 中間サマリーのトリガー
    HANDED_OFF = "handed-off"  # 人間エージェントへエスカレーション済み
    CLOSED = "closed"


@dataclass
class Communication:
    """会話内の 1 発話 / 1 メッセージ / 通話セグメント。"""

    role: Role
    text: str
    ts: float = field(default_factory=time.time)

    def as_line(self) -> str:
        label = {
            Role.CUSTOMER: "顧客",
            Role.AI_AGENT: "AI",
            Role.HUMAN_AGENT: "担当者",
            Role.SYSTEM: "システム",
        }.get(self.role, self.role.value)
        return f"{label}: {self.text}"


@dataclass
class Participant:
    role: Role
    identity: str  # 電話番号 / ユーザーID / エージェントSID 等


@dataclass
class Conversation:
    """チャネル横断の単一の記録システム。"""

    sid: str  # CallSid / ConversationSid 等
    channel: Channel
    participants: list[Participant] = field(default_factory=list)
    communications: list[Communication] = field(default_factory=list)
    status: Status = Status.ACTIVE
    # ハンドオフ/ルーティングに使える任意属性（顧客ID, 言語, 優先度, 用件 等）
    attributes: dict = field(default_factory=dict)
    # この会話で識別された顧客のメモリプロファイル
    customer_id: str = ""

    # --- 取り込み ---
    def add(self, role: Role, text: str) -> Communication:
        c = Communication(role=role, text=(text or "").strip())
        if c.text:
            self.communications.append(c)
        return c

    def ensure_participant(self, role: Role, identity: str) -> None:
        if not any(p.role == role and p.identity == identity for p in self.participants):
            self.participants.append(Participant(role=role, identity=identity))

    # --- 投影 ---
    def transcript(self, last: int | None = None) -> str:
        comms = self.communications[-last:] if last else self.communications
        return "\n".join(c.as_line() for c in comms)

    def llm_messages(self, last: int = 24) -> list[dict]:
        """Anthropic messages 形式（customer→user, ai→assistant）。"""
        out: list[dict] = []
        for c in self.communications[-last:]:
            if c.role == Role.CUSTOMER:
                out.append({"role": "user", "content": c.text})
            elif c.role == Role.AI_AGENT:
                out.append({"role": "assistant", "content": c.text})
        return out

    @property
    def turn_count(self) -> int:
        return len(self.communications)
