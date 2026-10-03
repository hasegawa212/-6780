"""通話メモ（Call Notes）。

通話後のメモを calllog に保存する。外部送信（Webhook）はenv設定時のみ。
"""

from __future__ import annotations


def save(to: str, note: str, *, send_webhook: bool = False) -> dict:
    """メモを calllog に追記する。"""
    from . import calllog, notify

    to = (to or "").strip()
    note = (note or "").strip()
    if not to:
        return {"ok": False, "error": "発信先 to が空です"}
    if not note:
        return {"ok": False, "error": "メモ note が空です"}

    calllog.append("outbound", to, "note", note=note)

    notified = False
    if send_webhook:
        notified = notify.send(to=to, result="メモ", note=note)

    return {"ok": True, "to": to, "note": note, "notified": notified}
