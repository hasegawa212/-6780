"""GenAI 言語演算子（Language Operators）。

設計図のとおり、生の会話テキストを「感情・要約・次に最適な回答・スクリプト遵守」
といった構造化された意味へ変換する、モジュラーで再利用可能・構成可能な部品。
Twilio の標準4オペレーターに対応し、カスタムオペレーターも追加できる。

各オペレーターは Memory/Knowledge の文脈を任意で受け取り、会話テキストだけでは
推測できない、より正確でビジネスに即した出力を返す。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .config import CONFIG

try:
    from anthropic import Anthropic
except ImportError:  # テスト環境では未インストールでも import 可能に
    Anthropic = None


@dataclass
class OperatorResult:
    name: str
    output: dict


def _client() -> "Anthropic | None":
    if Anthropic is None or not CONFIG.anthropic_key:
        return None
    return Anthropic(api_key=CONFIG.anthropic_key, max_retries=4, timeout=40.0)


def _run_json(system: str, user: str, *, max_tokens: int = 500) -> dict:
    """Claude にJSONを返させる薄いラッパ。失敗時は空 dict。"""
    client = _client()
    if client is None:
        return {}
    try:
        resp = client.messages.create(
            model=CONFIG.operator_model,
            max_tokens=max_tokens,
            system=system + "\n\n必ず有効なJSONのみを出力してください。前置きや```は不要です。",
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(text)
    except Exception:
        return {}


class LanguageOperator:
    """全オペレーターの基底。name と run(transcript, context) を持つ。"""

    name: str = "operator"

    def run(self, transcript: str, *, context: str = "") -> OperatorResult:  # pragma: no cover
        raise NotImplementedError


class Sentiment(LanguageOperator):
    """感情（positive/neutral/negative）とスコア、感情の変化点を抽出。"""

    name = "sentiment"

    def run(self, transcript: str, *, context: str = "") -> OperatorResult:
        out = _run_json(
            "あなたは会話の感情分析オペレーターです。顧客の感情を判定します。",
            f"次の会話を分析し、JSONで返してください。\n"
            f'キー: label("positive"|"neutral"|"negative"), '
            f'score(-1.0〜1.0), shift(感情が変化したなら短い説明、無ければ"")。\n\n'
            f"{context}\n\n会話:\n{transcript}",
            max_tokens=200,
        )
        return OperatorResult(self.name, out or {"label": "neutral", "score": 0.0, "shift": ""})


class Summary(LanguageOperator):
    """要約（人間エージェント引き継ぎ / アフターコール用）。"""

    name = "summary"

    def run(self, transcript: str, *, context: str = "") -> OperatorResult:
        out = _run_json(
            "あなたは会話要約オペレーターです。担当者が一目で状況を掴める要約を作ります。",
            f"次の会話を要約し、JSONで返してください。\n"
            f'キー: headline(20字以内の件名), summary(3〜5文の要約), '
            f"intent(顧客の用件), resolution(解決済みか/未解決か), "
            f"action_items(担当者が次にすべきことの配列)。\n\n"
            f"{context}\n\n会話:\n{transcript}",
            max_tokens=600,
        )
        return OperatorResult(self.name, out or {"headline": "", "summary": "", "intent": "",
                                                 "resolution": "", "action_items": []})


class NextBestResponse(LanguageOperator):
    """エージェントへの「次に最適な回答」提案（リアルタイム支援）。"""

    name = "next_best_response"

    def run(self, transcript: str, *, context: str = "") -> OperatorResult:
        out = _run_json(
            "あなたはエージェント支援オペレーターです。担当者が顧客へ返す一言を提案します。"
            "提供された社内ナレッジに反する内容は提案しないでください。",
            f"会話の流れに沿って、担当者が次に返すべき自然な一文を提案してください。JSONで返す。\n"
            f"キー: suggestion(提案する返答), rationale(根拠/参照したナレッジ), "
            f"upsell(アップセル機会があれば内容、無ければ\"\")。\n\n"
            f"{context}\n\n会話:\n{transcript}",
            max_tokens=400,
        )
        return OperatorResult(self.name, out or {"suggestion": "", "rationale": "", "upsell": ""})


class ScriptAdherence(LanguageOperator):
    """スクリプト/コンプライアンス遵守チェック。"""

    name = "script_adherence"

    def __init__(self, script: str = ""):
        self.script = script or CONFIG.agent_script

    def run(self, transcript: str, *, context: str = "") -> OperatorResult:
        script = self.script or "(スクリプト未設定: 一般的な丁寧応対・本人確認・要約確認を基準とする)"
        out = _run_json(
            "あなたはコンプライアンス/スクリプト遵守オペレーターです。",
            f"以下のスクリプト/ポリシーに対し、担当(またはAI)の応対がどれだけ遵守しているか評価。JSONで返す。\n"
            f"キー: adherence(0.0〜1.0), met(満たした項目の配列), "
            f"missed(欠けている項目の配列), compliance_risk(リスクがあれば説明、無ければ\"\")。\n\n"
            f"【スクリプト/ポリシー】\n{script}\n\n会話:\n{transcript}",
            max_tokens=500,
        )
        return OperatorResult(self.name, out or {"adherence": 1.0, "met": [], "missed": [],
                                                 "compliance_risk": ""})


class CustomOperator(LanguageOperator):
    """ドメイン固有のカスタム言語演算子。指示文とJSONスキーマ説明を渡すだけ。"""

    def __init__(self, name: str, instruction: str, keys: str):
        self.name = name
        self.instruction = instruction
        self.keys = keys

    def run(self, transcript: str, *, context: str = "") -> OperatorResult:
        out = _run_json(
            f"あなたは「{self.name}」というカスタム会話オペレーターです。{self.instruction}",
            f"次の会話を分析し、JSONで返してください。キー: {self.keys}。\n\n"
            f"{context}\n\n会話:\n{transcript}",
        )
        return OperatorResult(self.name, out or {})


# 標準4オペレーターのファクトリ
def standard_operators(script: str = "") -> dict[str, LanguageOperator]:
    ops = [Sentiment(), Summary(), NextBestResponse(), ScriptAdherence(script)]
    return {op.name: op for op in ops}
