"""Webhook 通知（Notify）。

成約 disposition 時に外部 Webhook（Slack/Telegram 等）へ通知する。
TAC_NOTIFY_WEBHOOK 未設定なら何もしない（デフォルトOFF）。
通知失敗は握り潰す（通話処理を止めない）。
"""

from __future__ import annotations

import json
import urllib.request

from .config import CONFIG


def send(*, to: str, result: str, note: str = "") -> bool:
    """Webhook に通知を送る。送信成功なら True、スキップ/失敗なら False。"""
    url = CONFIG.notify_webhook
    if not url:
        return False
    payload = {
        "text": f"🎉 成約通知: {to} — {result}",
        "to": to,
        "result": result,
    }
    if note:
        payload["note"] = note
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=10).close()
        return True
    except Exception:  # noqa: BLE001
        return False
