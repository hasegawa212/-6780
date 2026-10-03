"""担当者名簿（Agent Roster）のテスト（TDD）。

複数の担当者を TAC_AGENTS に登録し、発信ごとに名前/番号で選ぶ・未指定なら
ラウンドロビンで自動振り分け。各担当者が1件ずつ受け持てば人数分の並行発信になる
（一斉自動発信=オートダイヤラーではなく、1担当者=1通話のまま）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import agents  # noqa: E402
from tac.config import CONFIG  # noqa: E402


def test_roster_parses_name_and_number():
    CONFIG.agents = "田中:+818011112222, 佐藤:+818033334444"
    r = agents.roster()
    assert r == [
        {"name": "田中", "number": "+818011112222"},
        {"name": "佐藤", "number": "+818033334444"},
    ]


def test_roster_number_only():
    CONFIG.agents = "+818011112222,+818033334444"
    r = agents.roster()
    assert [a["number"] for a in r] == ["+818011112222", "+818033334444"]
    assert all(a["name"] == "" for a in r)


def test_roster_fallback_to_single_agent():
    CONFIG.agents = ""
    CONFIG.agent_number = "+818099998888"
    assert agents.roster()[0]["number"] == "+818099998888"


def test_resolve_by_name_and_number():
    CONFIG.agents = "田中:+818011112222,佐藤:+818033334444"
    assert agents.resolve("田中") == "+818011112222"
    assert agents.resolve("佐藤") == "+818033334444"
    assert agents.resolve("+818033334444") == "+818033334444"


def test_resolve_unknown_name_is_none():
    CONFIG.agents = "田中:+818011112222"
    assert agents.resolve("鈴木") is None


def test_resolve_passthrough_e164_not_in_roster():
    CONFIG.agents = "田中:+818011112222"
    assert agents.resolve("+819000001111") == "+819000001111"


def test_next_agent_round_robin():
    CONFIG.agents = "A:+818000000001,B:+818000000002,C:+818000000003"
    agents._rr["i"] = 0
    got = [agents.next_agent() for _ in range(4)]
    assert got == [
        "+818000000001",
        "+818000000002",
        "+818000000003",
        "+818000000001",
    ]


def test_agents_route_requires_token(monkeypatch=None):
    import importlib

    try:
        server = importlib.import_module("tac.server")
    except Exception:  # noqa: BLE001 - flask 未導入ならskip
        return
    CONFIG.agents = "田中:+818011112222,佐藤:+818033334444"
    CONFIG.outbound_token = "tok-agents-test"
    client = server.app.test_client()
    try:
        assert client.get("/tac/agents").status_code == 401
        r = client.get("/tac/agents", query_string={"token": "tok-agents-test"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert any(a["name"] == "田中" for a in body["agents"])
    finally:
        CONFIG.outbound_token = ""
