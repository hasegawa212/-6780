"""自動フォロー操作API（status/toggle/run）のテスト（TDD）。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("TAC_DRY_RUN", "1")
os.environ.setdefault("TAC_OUTBOUND_TOKEN", "test-token-42")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from tac.config import CONFIG
    monkeypatch.setattr(CONFIG, "calllog_file", str(tmp_path / "calls.jsonl"))
    monkeypatch.setattr(CONFIG, "follow_file", str(tmp_path / "follow.json"))
    monkeypatch.setattr(CONFIG, "queue_file", str(tmp_path / "queue.json"))
    monkeypatch.setattr(CONFIG, "dnc_file", str(tmp_path / "dnc.txt"))
    monkeypatch.setattr(CONFIG, "autofollow_file", str(tmp_path / "af.json"))
    monkeypatch.setattr(CONFIG, "autofollow_enabled", False)
    monkeypatch.setattr(CONFIG, "outbound_token", "test-token-42")
    yield


@pytest.fixture
def client():
    from tac.server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _auth():
    return {"X-TAC-Token": "test-token-42"}


def test_status_requires_token(client):
    r = client.get("/tac/autofollow/status")
    assert r.status_code == 401


def test_status_default_off(client):
    r = client.get("/tac/autofollow/status", headers=_auth())
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["engine"]["enabled"] is False


def test_toggle_enables_engine(client):
    r = client.post("/tac/autofollow/toggle", json={"enabled": True}, headers=_auth())
    assert r.status_code == 200
    assert r.get_json()["engine"]["enabled"] is True
    # 状態が永続化され status に反映される
    r2 = client.get("/tac/autofollow/status", headers=_auth())
    assert r2.get_json()["engine"]["enabled"] is True


def test_run_preview_does_not_execute(client, monkeypatch):
    from tac import autofollow
    called = []
    monkeypatch.setattr(autofollow, "run_once", lambda **kw: called.append(1))
    r = client.post("/tac/autofollow/run", json={}, headers=_auth())
    assert r.status_code == 200
    body = r.get_json()
    assert body["preview"] is True
    assert called == []  # 実発信ロジックは呼ばれない


def test_run_execute_invokes_engine(client, monkeypatch):
    from tac import autofollow
    monkeypatch.setattr(autofollow, "run_once",
                        lambda **kw: {"placed": False, "reason": "自動フォローOFF"})
    r = client.post("/tac/autofollow/run", json={"execute": True}, headers=_auth())
    assert r.status_code == 200
    body = r.get_json()
    assert body["preview"] is False
    assert body["placed"] is False


# --- IVR DTMF webhook（Twilioが叩く・トークン不要） -------------------
def test_dtmf_webhook_returns_twiml(client):
    r = client.post("/tac/autofollow/dtmf", data={"Digits": "1", "num": "+819011110000"})
    assert r.status_code == 200
    assert "text/xml" in r.headers["Content-Type"]
    assert "<Response>" in r.get_data(as_text=True)


def test_dtmf_9_records_decline(client, monkeypatch):
    from tac import autofollow
    rec = {}
    monkeypatch.setattr(
        autofollow.disposition, "record",
        lambda to, result, add_dnc=None, **kw: rec.update(to=to, dnc=add_dnc),
    )
    r = client.post("/tac/autofollow/dtmf", data={"Digits": "9", "num": "09011110000"})
    assert r.status_code == 200
    assert "停止" in r.get_data(as_text=True)
    assert rec.get("dnc") is True  # DNC登録の副作用が走る


# --- ダッシュボード（HTML・トークンはページに埋め込まない） ----------
def test_dashboard_renders_without_token(client):
    r = client.get("/tac/autofollow/dashboard")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "自動フォロー ダッシュボード" in html
    assert "次に掛ける1件" in html
    assert "/tac/autofollow/status" in html   # JSが状態APIを叩く
    assert "/tac/autofollow/toggle" in html   # ON/OFF
    assert "tac_token" in html                # トークンは端末localStorageから
    assert _auth()["X-TAC-Token"] not in html  # 実トークンは埋め込まれない
