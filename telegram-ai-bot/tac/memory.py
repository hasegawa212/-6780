"""Conversation Memory ＋ Enterprise Knowledge ブリッジ（コンテキスト・エンリッチ）。

設計図の「ブリッジ」ステップ: LLM に問い合わせる前に、Memory から
  - 特性 (traits)      … 耐久的な顧客プロファイル（名前/プラン/言語/好み 等）
  - 観測 (observations)… 過去のやり取りの履歴
を取得し、Enterprise Knowledge から関連ポリシー/手順を意味検索して、
標準化されたコンテキスト文字列にまとめる。

実体ストアは差し替え可能にしてある。デフォルトはインメモリ（テスト/デモ用）。
SUPABASE_URL/SERVICE_ROLE_KEY が設定されていれば、既存の semantic_search.py と
同じ Supabase ベクトル検索を Knowledge 取得に使う。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import CONFIG


@dataclass
class Profile:
    """耐久的な顧客プロファイル。"""

    customer_id: str
    traits: dict = field(default_factory=dict)        # 構造化された事実
    observations: list[str] = field(default_factory=list)  # 履歴メモ（新しい順で追加）

    def remember(self, note: str) -> None:
        note = (note or "").strip()
        if note:
            self.observations.append(note)


class MemoryStore:
    """プロファイルの作成/検索とエンリッチを行う。

    本番では Twilio Conversation Memory / 自社DBに差し替える。ここではインメモリ＋
    Supabase Knowledge 検索の最小実装で設計図の振る舞いを再現する。
    """

    def __init__(self, knowledge=None):
        self._profiles: dict[str, Profile] = {}
        self._knowledge = knowledge if knowledge is not None else KnowledgeBase()

    # --- プロファイル: 作成または検索 ---
    def get_or_create(self, customer_id: str) -> Profile:
        if customer_id not in self._profiles:
            self._profiles[customer_id] = Profile(customer_id=customer_id)
        return self._profiles[customer_id]

    def seed(self, customer_id: str, *, traits: dict | None = None,
             observations: list[str] | None = None) -> Profile:
        p = self.get_or_create(customer_id)
        if traits:
            p.traits.update(traits)
        for o in observations or []:
            p.remember(o)
        return p

    # --- エンリッチ: traits + observations + knowledge を 1 つの文脈に ---
    def enrich(self, customer_id: str, query: str = "", *, knowledge_k: int = 3) -> str:
        p = self.get_or_create(customer_id)
        blocks: list[str] = []

        if p.traits:
            kv = "、".join(f"{k}={v}" for k, v in p.traits.items())
            blocks.append(f"【顧客プロファイル】{kv}")

        if p.observations:
            recent = p.observations[-5:]
            blocks.append("【これまでの経緯】\n- " + "\n- ".join(recent))

        if query:
            facts = self._knowledge.retrieve(query, k=knowledge_k)
            if facts:
                blocks.append("【社内ナレッジ（回答の根拠）】\n- " + "\n- ".join(facts))

        return "\n\n".join(blocks)


class KnowledgeBase:
    """Enterprise Knowledge: ポリシー/手順の意味検索。

    Supabase が設定されていれば semantic_search の match_messages RPC を使い、
    無ければローカルに登録された文書から素朴なキーワード一致で返す。
    """

    def __init__(self):
        self._docs: list[str] = []

    def add(self, *docs: str) -> None:
        self._docs.extend(d.strip() for d in docs if d and d.strip())

    def retrieve(self, query: str, k: int = 3) -> list[str]:
        query = (query or "").strip()
        if not query:
            return []
        if CONFIG.has_memory and CONFIG.openai_key:
            hits = self._retrieve_supabase(query, k)
            if hits:
                return hits
        return self._retrieve_local(query, k)

    def _retrieve_local(self, query: str, k: int) -> list[str]:
        terms = [t for t in query.lower().split() if t]
        scored = []
        for d in self._docs:
            dl = d.lower()
            score = sum(1 for t in terms if t in dl)
            # 日本語向け: クエリ全体の部分一致も加点
            if query in d:
                score += 2
            if score:
                scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for _, d in scored[:k]]

    def _retrieve_supabase(self, query: str, k: int) -> list[str]:
        try:
            from . import _knowledge_supabase as ks  # 遅延 import（任意依存）
            return ks.match(query, k)
        except Exception:
            return []
