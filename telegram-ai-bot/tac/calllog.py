"""架電記録（Call Log）。

発信（bridge_call）の監査証跡を JSONL（1行1レコード）で残す。
「いつ・どの方向・誰に・結果」を追え、コンプライアンス（正直な架電・DNC 遵守の
証明）や運用可視化の土台になる。電話番号を含むため保存ファイルは gitignore。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime

from .config import CONFIG

_lock = threading.Lock()


def append(direction: str, to: str, status: str, **extra) -> dict:
    """1 レコード追記する。direction=outbound/inbound, status=dialed/blocked/error 等。"""
    rec = {
        "ts": datetime.now(UTC).isoformat(),
        "direction": direction,
        "to": to,
        "status": status,
    }
    rec.update(extra)
    path = CONFIG.calllog_file
    try:
        with _lock:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        # 記録失敗で通話処理を落とさない
        pass
    return rec


def recent(limit: int = 50) -> list[dict]:
    """直近 limit 件を新しい順で返す。"""
    try:
        with open(CONFIG.calllog_file, encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
    except OSError:
        return []
    out: list[dict] = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    out.reverse()
    return out


def _all() -> list[dict]:
    """記録を全件（古い順）読み込む。集計用。ファイル無しは空。"""
    try:
        with open(CONFIG.calllog_file, encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
    except OSError:
        return []
    out: list[dict] = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def summary(records: list[dict] | None = None) -> dict:
    """架電記録を集計する。

    records 省略時は保存ファイル全件から計算。返す集計:
      - total          : 総件数
      - by_status      : 結果別件数（dialed/blocked/error 等）
      - unique_numbers : 発信先番号のユニーク数
    DNC でブロックした件数（by_status["blocked"]）は「断った相手に再発信して
    いない」ことの証明になる。
    """
    if records is None:
        records = _all()
    by_status: dict[str, int] = {}
    numbers: set[str] = set()
    for rec in records:
        status = rec.get("status", "")
        if status:
            by_status[status] = by_status.get(status, 0) + 1
        to = rec.get("to")
        if to:
            numbers.add(to)
    return {
        "total": len(records),
        "by_status": by_status,
        "unique_numbers": len(numbers),
    }
