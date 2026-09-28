"""Twilio Webhook 署名検証。

Twilio は各 Webhook リクエストに `X-Twilio-Signature` を付ける。
署名 = base64( HMAC-SHA1( AuthToken, URL + ソート済みPOSTパラメータ連結 ) )。
これを検証することで、第三者による着信 Webhook のなりすましを弾ける。

参考: Twilio "Validating Requests"。GET の場合はパラメータ連結なし（URL のみ）。
標準ライブラリのみ（依存なし）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping


def compute_signature(auth_token: str, url: str, params: Mapping[str, str] | None = None) -> str:
    """Twilio 方式の署名を計算して返す（base64 文字列）。"""
    data = url
    if params:
        for key in sorted(params):
            data += key + str(params[key])
    digest = hmac.new(
        (auth_token or "").encode("utf-8"), data.encode("utf-8"), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def is_valid(
    auth_token: str,
    url: str,
    params: Mapping[str, str] | None,
    signature: str | None,
) -> bool:
    """受信した signature が正しいか（定数時間比較）。"""
    if not signature:
        return False
    expected = compute_signature(auth_token, url, params or {})
    return hmac.compare_digest(expected, signature)
