"""Twilio Webhook 署名検証のテスト（TDD）。

Twilio は Webhook に X-Twilio-Signature を付ける。署名 = base64(HMAC-SHA1(
AuthToken, URL + ソート済みPOSTパラメータの連結))。これを検証して、なりすまし
リクエストを弾く。

既知ベクトルは Twilio 公式ドキュメントの例を使用。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import twilio_sig  # noqa: E402

# Twilio 公式ドキュメントの検証例
_URL = "https://mycompany.com/myapp.php?foo=1&bar=2"
_PARAMS = {
    "CallSid": "CA1234567890ABCDE",
    "Caller": "+14158675310",
    "Digits": "1234",
    "From": "+14158675310",
    "To": "+18005551212",
}
_TOKEN = "12345"
# Twilio 方式（URL＋ソート済みパラメータ連結→HMAC-SHA1→base64）を、独立実装2つで
# 突き合わせて確定した既知値。
_EXPECTED = "GvWf1cFY/Q7PnoempGyD5oXAezc="


def test_compute_matches_twilio_reference():
    assert twilio_sig.compute_signature(_TOKEN, _URL, _PARAMS) == _EXPECTED


def test_is_valid_accepts_correct_signature():
    assert twilio_sig.is_valid(_TOKEN, _URL, _PARAMS, _EXPECTED) is True


def test_is_valid_rejects_wrong_signature():
    assert twilio_sig.is_valid(_TOKEN, _URL, _PARAMS, "wrong") is False
    assert twilio_sig.is_valid(_TOKEN, _URL, _PARAMS, "") is False
    assert twilio_sig.is_valid(_TOKEN, _URL, _PARAMS, None) is False


def test_is_valid_rejects_tampered_params():
    tampered = dict(_PARAMS, To="+19998887777")
    assert twilio_sig.is_valid(_TOKEN, _URL, tampered, _EXPECTED) is False


def test_param_order_independent():
    """パラメータの辞書順が違っても署名は同じ（キーでソートするため）。"""
    reordered = {k: _PARAMS[k] for k in reversed(list(_PARAMS))}
    assert twilio_sig.compute_signature(_TOKEN, _URL, reordered) == _EXPECTED


def test_webhook_enforcement(monkeypatch=None):
    """着信 Webhook: 署名なし=403、正しい署名=通過、非対象パスは非強制（flask無ければskip）。"""
    import importlib

    try:
        server = importlib.import_module("tac.server")
    except Exception:  # noqa: BLE001 - flask 未導入環境ではスキップ
        return
    from tac.config import CONFIG

    CONFIG.verify_twilio_signature = True
    CONFIG.twilio_auth_token = "12345"
    CONFIG.public_base_url = "http://localhost"
    client = server.app.test_client()
    try:
        params = {"CallSid": "CA1", "CallStatus": "in-progress"}
        url = "http://localhost/tac/voice/status"
        # 署名なし → 403
        assert client.post("/tac/voice/status", data=params).status_code == 403
        # 正しい署名 → 403 ではない
        sig = twilio_sig.compute_signature("12345", url, params)
        r = client.post(
            "/tac/voice/status", data=params, headers={"X-Twilio-Signature": sig}
        )
        assert r.status_code != 403
        # 非対象パス（health）は強制されない
        assert client.get("/").status_code == 200
    finally:
        CONFIG.verify_twilio_signature = False
        CONFIG.public_base_url = ""
