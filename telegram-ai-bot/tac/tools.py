"""ユニバーサルツールシステム。

設計図のとおり、LLM 向けの汎用ツール定義を提供する。エージェントは会話の中から
外部機能（コールバック予約、パスワードリセット、人間へのエスカレーション等）を
直接呼び出して問題を解決できる。

ツールは Anthropic tool-use スキーマで公開し、実体は任意の Python callable。
特別なツールとして「escalate_to_human」（ハンドオフ）を標準提供する。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., dict]

    def spec(self) -> dict:
        """Anthropic Messages API の tools[] 要素。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def add(self, name: str, description: str, input_schema: dict,
            handler: Callable[..., dict]) -> None:
        self.register(Tool(name, description, input_schema, handler))

    def specs(self) -> list[dict]:
        return [t.spec() for t in self._tools.values()]

    def has(self, name: str) -> bool:
        return name in self._tools

    def call(self, name: str, **kwargs) -> dict:
        tool = self._tools.get(name)
        if not tool:
            return {"error": f"unknown tool: {name}"}
        try:
            return tool.handler(**kwargs)
        except Exception as e:
            return {"error": str(e)}


def build_default_registry(*, handoff_manager, conversation_getter) -> ToolRegistry:
    """標準ツール（エスカレーション + サンプルのセルフサービス）を備えた registry。

    conversation_getter(): 現在処理中の Conversation を返す callable。ツールは会話に
    紐づくので、ハンドオフ等はこの会話に対して作用する。
    """
    reg = ToolRegistry()

    def _escalate(reason: str, priority: str = "normal", department: str = "") -> dict:
        conv = conversation_getter()
        extra = {"priority": priority}
        if department:
            extra["department"] = department
        result = handoff_manager.escalate(conv, reason, extra)
        return {
            "handed_off": result.handed_off,
            "detail": result.detail,
            "summary": result.payload.get("virtualAgentSummary", ""),
        }

    reg.add(
        "escalate_to_human",
        "AIで解決できない、顧客が人間を希望、または機微/苦情案件のとき、完全な文脈を"
        "付けて人間のサポート担当へ会話を引き継ぐ。引き継ぎ後はこのツールの結果を顧客へ"
        "一言添えて伝えること。",
        {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "エスカレーションの理由（日本語で簡潔に）"},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                "department": {"type": "string", "description": "振り分け先部署（任意・ルーティング用）"},
            },
            "required": ["reason"],
        },
        _escalate,
    )

    from .config import CONFIG

    if CONFIG.screening_enabled:
        from . import screening_log

        def _record_screening(**answers) -> dict:
            conv = conversation_getter()
            result = screening_log.evaluate(answers)
            screening_log.append(getattr(conv, "sid", ""), getattr(conv, "customer_id", ""), result)
            return {"recorded": True}  # 仮ランクは返さない＝相手に伝わらない

        reg.add(
            "record_screening",
            "顧客が会話の中で自分から話した、住まい探しの前提（家賃・勤続年数・転職や転勤の予定・"
            "ご家族など決める方の同席・購入のきっかけ）を記録する。記録のために質問して聞き出さない。"
            "分からない項目は省略する。年収や借入は記録しない。記録した内容や判定を顧客に伝えない。",
            {
                "type": "object",
                "properties": {
                    "rent_yen": {"type": "integer", "description": "今の家賃（円／月）。持ち家や実家なら 0"},
                    "tenure_years": {"type": "number", "description": "今の勤め先の勤続年数（年）"},
                    "job_change": {"type": "string", "enum": list(screening_log.JOB_CHANGE_LABELS),
                                   "description": "転職・転勤の予定"},
                    "decision_maker_present": {"type": "boolean",
                                               "description": "配偶者や親など、一緒に決める方が同席できるか"},
                    "has_motivation": {"type": "boolean", "description": "住まいを買いたいきっかけ・理由があるか"},
                },
                "required": [],
            },
            _record_screening,
        )

    def _schedule_callback(when: str, phone: str = "", note: str = "") -> dict:
        # 実体はデモ。実運用では予約システム/カレンダーAPIへ接続する。
        return {"scheduled": True, "when": when, "phone": phone, "note": note}

    reg.add(
        "schedule_callback",
        "顧客の希望日時に折り返し電話を予約する。",
        {
            "type": "object",
            "properties": {
                "when": {"type": "string", "description": "希望日時（例: 明日の15時）"},
                "phone": {"type": "string"},
                "note": {"type": "string", "description": "用件メモ"},
            },
            "required": ["when"],
        },
        _schedule_callback,
    )

    return reg
