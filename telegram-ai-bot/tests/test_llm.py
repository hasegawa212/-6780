"""LLM プロバイダ抽象のテスト（ネットワークなし・urlopen をモック）。

OpenAI 互換バックエンドの payload 組み立てとレスポンス解析、および
TAC_LLM_PROVIDER=openai 時に connector がオープンモデル経路を通ることを検証する。
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import llm  # noqa: E402
from tac.config import CONFIG  # noqa: E402
from tac.connector import TACConnector  # noqa: E402
from tac.models import Channel  # noqa: E402


def _fake_response(content: str):
    body = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
    resp = io.BytesIO(body)
    resp.__enter__ = lambda s=resp: s
    resp.__exit__ = lambda *a: False
    return resp


def test_openai_chat_builds_request_and_parses():
    captured = {}

    def fake_urlopen(req, timeout=60):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        captured["auth"] = req.get_header("Authorization")
        return _fake_response("こんにちは、ご用件をどうぞ。")

    with mock.patch("tac.llm.urllib.request.urlopen", fake_urlopen):
        out = llm.openai_chat(
            "あなたはサポート担当です。",
            [{"role": "user", "content": "もしもし"}],
            base_url="http://localhost:11434/v1",
            model="llama3.1",
            api_key="ollama",
            max_tokens=200,
        )

    assert out == "こんにちは、ご用件をどうぞ。"
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    assert captured["auth"] == "Bearer ollama"
    msgs = captured["body"]["messages"]
    assert msgs[0] == {"role": "system", "content": "あなたはサポート担当です。"}
    assert msgs[1] == {"role": "user", "content": "もしもし"}
    assert captured["body"]["model"] == "llama3.1"
    assert captured["body"]["max_tokens"] == 200


def test_connector_uses_openai_provider():
    """TAC_LLM_PROVIDER=openai のとき openai_chat 経由で応答する。"""
    CONFIG.llm_provider = "openai"
    try:
        with mock.patch("tac.connector.openai_chat", return_value="はい、承知しました。") as m:
            conn = TACConnector()
            conn.start("c-open", Channel.SMS, customer_identity="+81")
            result = conn.handle("c-open", "予約を変更したい", realtime_assist=False)
        assert result.text == "はい、承知しました。"
        assert m.called
        # オープンモデル経路はサーバーツールを使わない
        assert result.tool_calls == []
    finally:
        CONFIG.llm_provider = "anthropic"


def test_connector_openai_degrades_on_error():
    """ローカルモデル未起動でも通話を落とさず定型文を返す。"""
    CONFIG.llm_provider = "openai"
    try:
        with mock.patch("tac.connector.openai_chat", side_effect=OSError("connection refused")):
            conn = TACConnector()
            conn.start("c-open2", Channel.SMS, customer_identity="+81")
            result = conn.handle("c-open2", "こんにちは", realtime_assist=False)
        assert "オープンモデル未接続" in result.text
    finally:
        CONFIG.llm_provider = "anthropic"
