"""TAC コア: 会話ライフサイクルと推論ループ（ブリッジ → LLM → 実行）。

設計図1の中核ミドルウェア。各チャネルからのイベントを取り込んで単一の
Conversation を保ち、

  1. 初期化/オーケストレーション … 会話を作成し顧客を識別
  2. コンテキスト・エンリッチ(ブリッジ) … Memory から traits/observations + Knowledge
  3. 推論ループ … 充実したプロンプトを LLM へ。tool 呼び出し（ハンドオフ等）を実行
  4. 実行と配信 … 応答テキストを返す（チャネル変換は server 層）

同時に Conversation Intelligence を回し、人間エージェント支援シグナルを生成する。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import CONFIG
from .handoff import HandoffManager
from .intelligence import ConversationIntelligence
from .llm import openai_chat
from .memory import MemoryStore
from .models import Channel, Conversation, Role, Status
from .tools import ToolRegistry, build_default_registry

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None


@dataclass
class TurnResult:
    text: str
    handed_off: bool = False
    tool_calls: list = field(default_factory=list)
    assist: dict = field(default_factory=dict)  # 直近のリアルタイム支援シグナル


class TACConnector:
    """会話を保持し、1 ターンを処理する中核。"""

    def __init__(self, *, memory: MemoryStore | None = None,
                 intelligence: ConversationIntelligence | None = None,
                 registry: ToolRegistry | None = None):
        self.memory = memory or MemoryStore()
        self.handoff = HandoffManager(memory=self.memory)
        self.intelligence = intelligence or ConversationIntelligence(
            enricher=self.memory.enrich
        )
        self._conversations: dict[str, Conversation] = {}
        self._active_sid: str = ""
        self.registry = registry or build_default_registry(
            handoff_manager=self.handoff,
            conversation_getter=lambda: self._conversations[self._active_sid],
        )
        self._client = (
            Anthropic(api_key=CONFIG.anthropic_key, max_retries=4, timeout=60.0)
            if Anthropic is not None and CONFIG.anthropic_key else None
        )

    # --- 1. 初期化 / オーケストレーション ---
    def start(self, sid: str, channel: Channel, *, customer_identity: str = "",
              goal: str = "", attributes: dict | None = None) -> Conversation:
        conv = Conversation(sid=sid, channel=channel, attributes=attributes or {})
        if goal:
            conv.attributes["goal"] = goal
        if customer_identity:
            conv.ensure_participant(Role.CUSTOMER, customer_identity)
            # Memory プロファイルでユーザーを識別（作成または検索）
            conv.customer_id = customer_identity
            self.memory.get_or_create(customer_identity)
        conv.ensure_participant(Role.AI_AGENT, "tac-virtual-agent")
        self._conversations[sid] = conv
        return conv

    def get(self, sid: str) -> Conversation | None:
        return self._conversations.get(sid)

    def add_agent_line(self, sid: str, text: str) -> None:
        """AI の発話を会話履歴に記録する（固定の第一声など、LLM を介さない発話用）。"""
        conv = self._conversations.get(sid)
        if conv is not None and text:
            conv.add(Role.AI_AGENT, text)

    # --- 2+3+4. 1 ターン処理 ---
    def handle(self, sid: str, user_text: str, *, realtime_assist: bool = True) -> TurnResult:
        """1 ターンを処理して AI 応答を返す。

        realtime_assist=False のときはリアルタイム会話インテリジェンス
        （sentiment / next_best_response 等のオペレーター LLM 呼び出し）を
        スキップする。これらは人間エージェント支援用シグナルで AI 発話には
        不要なため、通話/チャットの低遅延応答ではホットパスから外す。
        必要なときは GET /tac/assist/<sid> でオンデマンドに取得できる。
        """
        conv = self._conversations.get(sid)
        if conv is None:
            raise KeyError(f"unknown conversation: {sid}")
        self._active_sid = sid
        if conv.status == Status.HANDED_OFF:
            # すでに人間が応対中。AI は割り込まない。
            return TurnResult(text="", handed_off=True)

        conv.add(Role.CUSTOMER, user_text)

        # 2. ブリッジ: Memory + Knowledge でプロンプトを充実
        context = self.memory.enrich(conv.customer_id, user_text) if conv.customer_id else ""

        # 3. 推論ループ（tool-use 対応）
        text, tool_calls, handed_off = self._reason(conv, context)
        if text:
            conv.add(Role.AI_AGENT, text)

        # 4. リアルタイム会話インテリジェンス（任意・低遅延時はスキップ）
        assist = self.intelligence.on_utterance(conv).signals if realtime_assist else {}

        return TurnResult(text=text, handed_off=handed_off, tool_calls=tool_calls, assist=assist)

    def _system(self, conv: Conversation, context: str) -> str:
        goal = conv.attributes.get("goal", "")
        s = CONFIG.persona + (
            " これは実時間の顧客対応です。簡潔で自然な日本語で、一度に1つの用件を進めます。"
            " 解決できない/顧客が人間を希望/苦情や機微な内容のときは escalate_to_human を使い、"
            " 折り返し予約などの定型業務はツールで完結させます。"
        )
        if goal:
            s += f"\n\nこの会話の目的: {goal}"
        if context:
            s += f"\n\n--- 参照コンテキスト（顧客記憶・社内ナレッジ）---\n{context}"
        return s

    def _reason(self, conv: Conversation, context: str):
        """LLM 呼び出し＋ツール実行ループ。client 無しなら安全に degrade。"""
        tool_calls: list = []
        handed_off = False

        # オープンソースモデル経路（OpenAI 互換: Ollama/vLLM/LM Studio 等）。
        # 会話応答に特化（Claude 専用のツール実行・Web 検索は使わない）。
        if CONFIG.llm_provider == "openai":
            return (self._reason_openai(conv, context), tool_calls, handed_off)

        if self._client is None:
            # LLM 未設定時のフォールバック（テスト/デモ）
            return ("(LLM未設定) ご用件を承りました。担当者に確認いたします。",
                    tool_calls, handed_off)

        messages = conv.llm_messages()
        system = self._system(conv, context)
        # カスタムツール＋（任意で）サーバーサイド Web 検索。検索はモデルが
        # 必要と判断したときだけ走るので、雑談は速いまま最新情報にも対応できる。
        tools = list(self.registry.specs())
        if CONFIG.web_search:
            tools.append({
                "type": CONFIG.web_search_type,
                "name": "web_search",
                "max_uses": CONFIG.web_search_max_uses,
            })
        text_parts: list[str] = []
        for _ in range(4):  # tool 連鎖の上限
            resp = self._client.messages.create(
                model=CONFIG.model,
                max_tokens=CONFIG.max_tokens,
                system=system,
                tools=tools,
                messages=messages,
            )
            text_parts = [b.text for b in resp.content if b.type == "text"]
            tool_uses = [b for b in resp.content if b.type == "tool_use"]

            if not tool_uses:
                # サーバーツール（Web検索）が上限に達した場合は継続して完了させる
                if resp.stop_reason == "pause_turn":
                    messages.append({"role": "assistant", "content": resp.content})
                    continue
                return ("".join(text_parts).strip(), tool_calls, handed_off)

            # ツールを実行し、結果をモデルへ返す
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for tu in tool_uses:
                out = self.registry.call(tu.name, **(tu.input or {}))
                tool_calls.append({"name": tu.name, "input": tu.input, "output": out})
                if tu.name == "escalate_to_human" and out.get("handed_off"):
                    handed_off = True
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": str(out),
                })
            messages.append({"role": "user", "content": results})
            if handed_off:
                # ハンドオフが起きたら最後の一言を生成して終了
                tail = "".join(text_parts).strip()
                return (tail or "担当者におつなぎします。少々お待ちください。",
                        tool_calls, handed_off)

        return ("".join(text_parts).strip(), tool_calls, handed_off)

    def _reason_openai(self, conv: Conversation, context: str) -> str:
        """オープンソースモデル（OpenAI 互換 API）で会話応答を生成する。"""
        system = self._system(conv, context)
        messages = conv.llm_messages()
        try:
            text = openai_chat(
                system, messages,
                base_url=CONFIG.openai_base_url,
                model=CONFIG.openai_model,
                api_key=CONFIG.openai_api_key,
                max_tokens=CONFIG.max_tokens,
            )
            return text or "恐れ入ります、もう一度お願いできますか。"
        except Exception:
            # ローカルモデル未起動などでも通話/チャットを落とさない
            return "(オープンモデル未接続) ご用件を承りました。担当者に確認いたします。"

    # --- ライフサイクル終端 ---
    def inactive(self, sid: str) -> dict:
        conv = self._conversations.get(sid)
        return self.intelligence.on_inactive(conv).signals if conv else {}

    def close(self, sid: str) -> dict:
        """会話終了。最終サマリー等を生成し、観測を Memory へ書き戻す。"""
        conv = self._conversations.get(sid)
        if conv is None:
            return {}
        signals = self.intelligence.on_close(conv).signals
        # 設計図: 会話の成果が Memory を更新し、次回のターンに活きる
        if conv.customer_id:
            summ = signals.get("summary", {})
            note = summ.get("summary") or summ.get("headline")
            if note:
                self.memory.get_or_create(conv.customer_id).remember(note)
        return signals
