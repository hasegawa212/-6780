"""自動フォロー 常駐オート運転（無人スケジューラ）。

「自動運転(auto)」が ON のとき、一定間隔で連続発信バッチ(run_batch)を自動実行する。
人が張り付かなくても、同意済みフォロー対象へ安全ガード付きで掛け続ける。

安全の多重ロック（すべて満たしたときだけ発信）:
  1. 常駐スケジューラ自体の起動（CONFIG.autofollow_scheduler / 既定OFF）
  2. 自動運転トグル auto（既定OFF・UIから切替）
  3. エンジン enabled（既定OFF）
  4. 一時停止 paused でない
  5. 発信時間帯内（calling_hours）
  6. run_batch 内の全ガード（同意/DNC/本日発信済み/上限）＋ max_calls 上限

tick() は純粋に近い判定関数でテスト可能。実スレッドは start() で1つだけ起動。
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from . import autofollow, calling_hours
from .config import CONFIG

_thread: threading.Thread | None = None
_lock = threading.Lock()
_last: dict = {"at": None, "ran": False, "reason": "", "placed": 0}


def tick(*, now: datetime | None = None, state: dict | None = None, run=None) -> dict:
    """1回ぶんの判定＋実行。全ロックを満たせば run_batch を呼ぶ。

    実発信を避けたいテストでは run を注入する（既定は autofollow.run_batch）。
    """
    now = now or datetime.now(UTC)
    st = state or autofollow.status()
    if not st.get("auto"):
        return {"ran": False, "reason": "自動運転OFF"}
    if not st.get("enabled"):
        return {"ran": False, "reason": "エンジンOFF"}
    if st.get("paused"):
        return {"ran": False, "reason": "一時停止中"}
    if not calling_hours.allowed(now):
        return {"ran": False, "reason": f"発信時間帯外（{calling_hours.window_text()}）"}
    run = run or autofollow.run_batch
    res = run()
    return {"ran": True, "result": res, "placed": res.get("placed", 0)}


def last_run() -> dict:
    """直近の自動実行の結果（可視化・監査用）。"""
    return dict(_last)


def _record(result: dict) -> None:
    _last["at"] = datetime.now(UTC).isoformat()
    _last["ran"] = result.get("ran", False)
    _last["reason"] = result.get("reason", "")
    if result.get("ran"):
        _last["placed"] = (result.get("result") or {}).get("placed", 0)


def _loop(interval: int, stop: threading.Event) -> None:
    while not stop.is_set():
        # 最初に interval 待ってから動く（起動直後の暴発を避ける）
        if stop.wait(interval):
            break
        try:
            _record(tick())
        except Exception as e:  # noqa: BLE001 - 常駐は落とさない
            _last["reason"] = f"error: {e}"


_stop = threading.Event()


def start(interval: int | None = None) -> bool:
    """常駐スレッドを1つだけ起動する。多重起動はしない。起動したら True。"""
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return False
        iv = interval or CONFIG.autofollow_auto_interval_sec
        _stop.clear()
        _thread = threading.Thread(
            target=_loop, args=(iv, _stop), name="autofollow-scheduler", daemon=True
        )
        _thread.start()
        return True


def stop() -> None:
    _stop.set()
