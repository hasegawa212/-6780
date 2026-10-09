"""go-live プリフライト点検 tac/check.preflight の TDD。

本番で実顧客に発信して安全かを一発判定する。creds・発信元番号(03)・
担当者名簿・安全ゲートを点検し、critical が全て通れば go_live=True。
秘密の実値（番号・トークン）は結果に出さない（真偽と件数だけ）。

実行: cd telegram-ai-bot && python3 -m pytest tests/test_preflight.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import check  # noqa: E402
from tac.config import CONFIG  # noqa: E402


def _set_all_critical_ok(mp):
    """critical を全て満たす最小構成に揃える。"""
    mp.setattr(CONFIG, "twilio_account_sid", "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    mp.setattr(CONFIG, "twilio_auth_token", "tok_secret_value_1234567890")
    mp.setattr(CONFIG, "caller_id", "+81312345678")
    mp.setattr(CONFIG, "outbound_token", "secret-outbound-token")
    mp.setattr(CONFIG, "agents", "酒井:+817038266253")
    mp.setattr(CONFIG, "agent_number", "")


def _set_all_recommended_ok(mp):
    mp.setattr(CONFIG, "enforce_call_hours", True)
    mp.setattr(CONFIG, "daily_call_cap", 50)
    mp.setattr(CONFIG, "verify_twilio_signature", True)
    mp.setattr(CONFIG, "disclosure_enabled", True)


# ---- 何も設定されてない → go-live 不可 ----
def test_empty_config_not_go_live(monkeypatch):
    monkeypatch.setattr(CONFIG, "twilio_account_sid", "")
    monkeypatch.setattr(CONFIG, "twilio_auth_token", "")
    monkeypatch.setattr(CONFIG, "caller_id", "")
    monkeypatch.setattr(CONFIG, "outbound_token", "")
    monkeypatch.setattr(CONFIG, "agents", "")
    monkeypatch.setattr(CONFIG, "agent_number", "")
    rep = check.preflight()
    assert rep["go_live"] is False
    assert rep["summary"]["critical_fail"] >= 1


def test_caller_id_is_critical(monkeypatch):
    # creds/token/名簿は揃えて caller_id(03) だけ欠く → go-live 不可
    _set_all_critical_ok(monkeypatch)
    monkeypatch.setattr(CONFIG, "caller_id", "")
    rep = check.preflight()
    assert rep["go_live"] is False
    failed = {c["key"] for c in rep["checks"] if not c["ok"]}
    assert "caller_id" in failed


def test_empty_roster_is_critical(monkeypatch):
    _set_all_critical_ok(monkeypatch)
    monkeypatch.setattr(CONFIG, "agents", "")
    monkeypatch.setattr(CONFIG, "agent_number", "")
    rep = check.preflight()
    assert rep["go_live"] is False
    failed = {c["key"] for c in rep["checks"] if not c["ok"]}
    assert "agent_roster" in failed


# ---- critical 全部OK → go-live 可（推奨はまだOFF） ----
def test_critical_ok_go_live_true(monkeypatch):
    _set_all_critical_ok(monkeypatch)
    monkeypatch.setattr(CONFIG, "enforce_call_hours", False)
    monkeypatch.setattr(CONFIG, "daily_call_cap", 0)
    monkeypatch.setattr(CONFIG, "verify_twilio_signature", False)
    monkeypatch.setattr(CONFIG, "disclosure_enabled", False)
    rep = check.preflight()
    assert rep["go_live"] is True
    assert rep["ready_for_production"] is False
    assert rep["summary"]["critical_fail"] == 0
    assert rep["summary"]["recommended_fail"] >= 1


# ---- critical + recommended 全部OK → 本番完全体 ----
def test_fully_hardened(monkeypatch):
    _set_all_critical_ok(monkeypatch)
    _set_all_recommended_ok(monkeypatch)
    rep = check.preflight()
    assert rep["go_live"] is True
    assert rep["ready_for_production"] is True
    assert rep["summary"]["critical_fail"] == 0
    assert rep["summary"]["recommended_fail"] == 0


# ---- 秘密の実値を漏らさない ----
def test_no_secret_values_leaked(monkeypatch):
    _set_all_critical_ok(monkeypatch)
    _set_all_recommended_ok(monkeypatch)
    rep = check.preflight()
    import json
    blob = json.dumps(rep, ensure_ascii=False)
    assert "+81312345678" not in blob        # caller_id 実値
    assert "secret-outbound-token" not in blob
    assert "tok_secret_value_1234567890" not in blob
    assert "+817038266253" not in blob        # 名簿の番号


# ---- /tac/preflight ルート（発信APIと同じトークン認証） ----
def test_preflight_route_requires_token(monkeypatch):
    import importlib
    try:
        server = importlib.import_module("tac.server")
    except Exception:  # noqa: BLE001 - flask 未導入ならskip
        pytest.skip("flask 未導入")
    _set_all_critical_ok(monkeypatch)
    monkeypatch.setattr(CONFIG, "outbound_token", "tok-preflight-test")
    client = server.app.test_client()
    # トークン無し → 401
    assert client.get("/tac/preflight").status_code == 401
    # 正しいトークン → 200 + go_live 判定
    r = client.get("/tac/preflight", query_string={"token": "tok-preflight-test"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["go_live"] is True
    assert "checks" in body


# ---- 構造の最低保証 ----
def test_report_shape(monkeypatch):
    _set_all_critical_ok(monkeypatch)
    rep = check.preflight()
    assert set(["go_live", "ready_for_production", "summary", "checks"]).issubset(rep)
    for c in rep["checks"]:
        assert set(["key", "label", "level", "ok", "detail"]).issubset(c)
        assert c["level"] in ("critical", "recommended")
