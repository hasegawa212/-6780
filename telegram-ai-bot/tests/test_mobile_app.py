"""iPhone 用 発信アプリ（Mobile App）のテスト（TDD）。

iPhone の Safari から開いて「ホーム画面に追加」すれば、アプリのように発信・結果記録・
今日の集計ができる。発信は1件ずつ担当者がタップして行う（一斉自動発信はしない）。
番号は 090-1234-5678 のような国内表記で入力しても E.164 に直して受け付ける。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import mobile_app, phone  # noqa: E402
from tac.config import CONFIG  # noqa: E402

# ---------------- 電話番号の正規化 ----------------


def test_to_e164_domestic_mobile_with_hyphens():
    assert phone.to_e164("090-1234-5678") == "+819012345678"


def test_to_e164_domestic_landline_and_spaces():
    assert phone.to_e164("03 6899 5464") == "+81368995464"


def test_to_e164_keeps_international():
    assert phone.to_e164("+81 90-1234-5678") == "+819012345678"
    assert phone.to_e164("+1 (659) 210-3801") == "+16592103801"


def test_to_e164_international_prefix_00():
    assert phone.to_e164("0081312345678") == "+81312345678"


def test_to_e164_fullwidth_digits():
    # iPhone の日本語キーボードで全角数字になっても受け付ける
    assert phone.to_e164("０９０－１２３４－５６７８") == "+819012345678"


def test_to_e164_rejects_garbage():
    assert phone.to_e164("") is None
    assert phone.to_e164("abc") is None
    assert phone.to_e164("123") is None
    assert phone.to_e164("+81") is None


# ---------------- ページ本体 ----------------


def test_page_is_mobile_ready():
    page = mobile_app.render()
    assert '<meta name="viewport"' in page
    assert "apple-mobile-web-app-capable" in page  # ホーム画面に追加でアプリ化
    assert 'rel="manifest"' in page
    assert 'lang="ja"' in page


def test_page_uses_token_header_and_existing_apis():
    page = mobile_app.render()
    assert "X-TAC-Token" in page
    for api in ("/tac/call", "/tac/calls/disposition", "/tac/calls/summary",
                "/tac/calls", "/tac/agents"):
        assert api in page


def test_page_offers_disposition_buttons_including_decline():
    page = mobile_app.render()
    for label in ("成約", "検討", "不在", "拒否"):
        assert label in page


def test_page_never_auto_dials_on_a_timer():
    # 一斉自動発信（オートダイヤラー）にしない: タイマーで発信を繰り返さない
    page = mobile_app.render()
    assert "setInterval" not in page


def test_manifest_is_standalone_app():
    m = mobile_app.manifest()
    assert m["display"] == "standalone"
    assert m["start_url"] == "/tac/app"
    assert m["name"]


# ---------------- ルート ----------------


def _client():
    import importlib

    try:
        server = importlib.import_module("tac.server")
    except Exception:  # noqa: BLE001 - flask 未導入ならskip
        return None
    return server.app.test_client()


def test_app_route_serves_page_without_leaking_token():
    client = _client()
    if client is None:
        return
    CONFIG.outbound_token = "tok-secret-mobile"
    try:
        r = client.get("/tac/app")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "X-TAC-Token" in body
        assert "tok-secret-mobile" not in body  # トークンはページに埋め込まない
    finally:
        CONFIG.outbound_token = ""


def test_manifest_route():
    client = _client()
    if client is None:
        return
    r = client.get("/tac/app/manifest.webmanifest")
    assert r.status_code == 200
    assert r.get_json()["display"] == "standalone"


def test_call_route_accepts_domestic_number(monkeypatch):
    client = _client()
    if client is None:
        return
    import tac.outbound as outbound

    seen = {}

    def fake_bridge(to, *, agent=None, agent_name=None):
        seen["to"] = to
        return {"ok": True, "to": to}

    monkeypatch.setattr(outbound, "bridge_call", fake_bridge)
    monkeypatch.setattr(CONFIG, "agents", "")
    CONFIG.outbound_token = "tok-mobile"
    try:
        r = client.post("/tac/call", data={"to": "090-1234-5678", "token": "tok-mobile"})
        assert r.status_code == 200
        assert seen["to"] == "+819012345678"
        bad = client.post("/tac/call", data={"to": "abc", "token": "tok-mobile"})
        assert bad.status_code == 400
    finally:
        CONFIG.outbound_token = ""
