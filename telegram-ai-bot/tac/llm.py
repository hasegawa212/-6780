"""LLM プロバイダ抽象（Claude / OpenAI 互換）。

TAC の推論ループは既定で Claude（Anthropic）を使うが、ローカル/オープンソースの
モデルでも動かせるよう、OpenAI 互換の Chat Completions エンドポイント
（Ollama / vLLM / LM Studio など）を叩く軽量バックエンドを提供する。

外部依存を増やさないため標準ライブラリ（urllib）だけで実装する。
オープンモデル経路は「会話応答（テキスト）」に特化する。Claude 専用機能
（カスタムツール実行・サーバーサイド Web 検索）はこの経路では使えない。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


def openai_chat(
    system: str,
    messages: list[dict],
    *,
    base_url: str,
    model: str,
    api_key: str = "",
    max_tokens: int = 400,
    timeout: int = 60,
) -> str:
    """OpenAI 互換 /chat/completions を呼び、アシスタントのテキストを返す。

    messages は [{"role": "user"|"assistant", "content": str}, ...]（Conversation.
    llm_messages() の出力をそのまま渡せる）。base_url 例: http://localhost:11434/v1
    （Ollama）。失敗時は例外を送出する（呼び出し側で degrade する）。
    """
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": ([{"role": "system", "content": system}] if system else []) + messages,
    }
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode())
    return (out["choices"][0]["message"]["content"] or "").strip()
