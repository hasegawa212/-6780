"""TAC — Twilio Agent Connect 参照実装。

3 つの Twilio ソリューション設計図をひとつのパッケージで再現する:
  1. 対話型エージェント（TAC コア: ライフサイクル/ブリッジ/推論ループ/配信）
  2. 人間エージェントへのシームレスなハンドオフ（Flex / Studio）
  3. 人間エージェント拡張（Conversation Intelligence の言語演算子・リアルタイム/事後）

主要な公開API:
  from tac import TACConnector, Channel
  conn = TACConnector()
  conn.start("CA123", Channel.VOICE, customer_identity="+8190...")
  result = conn.handle("CA123", "解約したいんですが")
  conn.close("CA123")
"""

from .config import CONFIG, Config
from .connector import TACConnector, TurnResult
from .handoff import HandoffManager, HandoffPackage, HandoffResult
from .intelligence import ConversationIntelligence, Rule, Trigger
from .memory import KnowledgeBase, MemoryStore, Profile
from .models import Channel, Communication, Conversation, Participant, Role, Status
from .operators import (
    CustomOperator,
    NextBestResponse,
    ScriptAdherence,
    Sentiment,
    Summary,
    standard_operators,
)
from .tools import Tool, ToolRegistry, build_default_registry

__all__ = [
    "CONFIG", "Config",
    "TACConnector", "TurnResult",
    "HandoffManager", "HandoffPackage", "HandoffResult",
    "ConversationIntelligence", "Rule", "Trigger",
    "MemoryStore", "Profile", "KnowledgeBase",
    "Channel", "Conversation", "Communication", "Participant", "Role", "Status",
    "Sentiment", "Summary", "NextBestResponse", "ScriptAdherence",
    "CustomOperator", "standard_operators",
    "Tool", "ToolRegistry", "build_default_registry",
]
