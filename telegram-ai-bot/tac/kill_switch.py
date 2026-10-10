"""緊急停止スイッチ（再起動なしで、すべての発信を即時に止める）。

状態は CONFIG.kill_switch_file（本番は /data）に JSON で置き、発信のたびに読み直す。
読めない・壊れているときは「停止中」とみなす（fail closed）。ファイルが無いときだけ解除扱い。
解除・停止の履歴（誰が・いつ・理由）を残す。電話番号などの個人情報は書かない。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime

from .config import CONFIG

_LOCK = threading.Lock()
_HISTORY_MAX = 200


def _read() -> dict | None:
    """状態を読む。ファイルが無ければ {}、壊れていれば None。"""
    path = CONFIG.kill_switch_file
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def engaged() -> bool:
    data = _read()
    if data is None:
        return True
    return data.get("engaged") is True


def status() -> dict:
    data = _read()
    if data is None:
        return {"engaged": True, "reason": "状態ファイルを読めないため停止中とみなしています", "readable": False}
    return {
        "engaged": data.get("engaged") is True,
        "reason": str(data.get("reason") or ""),
        "since": str(data.get("since") or ""),
        "actor": str(data.get("actor") or ""),
        "readable": True,
    }


def _write(engage: bool, actor: str, reason: str) -> dict:
    path = CONFIG.kill_switch_file
    now = datetime.now(UTC).isoformat()
    with _LOCK:
        data = _read() or {}
        history = list(data.get("history") or [])[-(_HISTORY_MAX - 1):]
        history.append({"action": "engage" if engage else "release", "actor": actor,
                        "reason": reason, "at": now})
        state = {"engaged": engage, "actor": actor, "reason": reason, "since": now, "history": history}
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    return state


def engage(*, actor: str, reason: str = "") -> dict:
    return _write(True, actor or "unknown", reason)


def release(*, actor: str, reason: str = "") -> dict:
    return _write(False, actor or "unknown", reason)
