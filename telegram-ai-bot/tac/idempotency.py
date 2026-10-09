"""Webhook 冪等性ガード（idempotency）。

Twilio は同じ StatusCallback / AMD を重複・遅延・順序逆転で送ることがある
（ネットワーク再送・タイムアウト再試行など）。処理済みの「CallSid+イベント」を
短期記憶し、二重計上（架電ログ・統計の水増し）や二重 redirect を防ぐ。

設計:
- `seen(key)` は初めて見たキーを記録して False、既に処理済みなら True を返す。
- 記録は JSON ファイル（CONFIG.idempotency_file）に永続。プロセス再起動後も有効。
- TTL（既定6時間）を超えた記録は読むたびに掃除する（肥大化防止）。
- 空キーは常に False（＝CallSid 不在時に誤って全部を抑止しない safe default）。
"""

from __future__ import annotations

import json
import os
import threading
import time

from .config import CONFIG

_lock = threading.Lock()
# 処理済み記録の保持時間（秒）。Twilio の再送はごく短時間なので6時間で十分。
_TTL_SEC = int(os.environ.get("TAC_IDEMPOTENCY_TTL_SEC", str(6 * 3600)))


def _load() -> dict:
    try:
        with open(CONFIG.idempotency_file, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    path = CONFIG.idempotency_file
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)  # アトミック置換（破損防止）


# 重複はじき件数を記録する予約キー（CallSid と衝突しない名前。TTL 掃除の対象外）。
_DUP_KEY = "__dupes__"


def seen(key: str, *, now: float | None = None) -> bool:
    """key を初めて見たら記録して False、処理済みなら True を返す。

    処理済み（True）を返すたびに重複カウンタを +1 する（観測用）。
    空キーは常に False でカウントもしない（CallSid 不在などで抑止しすぎない）。
    """
    if not key:
        return False
    ts = now if now is not None else time.time()
    with _lock:
        data = _load()
        dupes = int(data.get(_DUP_KEY, 0) or 0)
        # 期限切れを掃除（予約キーは残す）
        data = {k: v for k, v in data.items()
                if k == _DUP_KEY or (isinstance(v, (int, float)) and ts - v < _TTL_SEC)}
        already = key in data and key != _DUP_KEY
        if already:
            dupes += 1
        data[key] = ts
        data[_DUP_KEY] = dupes
        _save(data)
        return already


def duplicates_blocked() -> int:
    """これまでに重複としてはじいた Webhook の累計件数を返す。"""
    with _lock:
        return int(_load().get(_DUP_KEY, 0) or 0)


def reset() -> None:
    """テスト用: 記録をクリアする。"""
    with _lock:
        try:
            os.remove(CONFIG.idempotency_file)
        except OSError:
            pass
