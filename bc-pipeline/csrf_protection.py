"""ログイン試行のロックアウト（総当り攻撃防止）。プロセス内メモリで管理。
bc_service.py が使用: is_locked / get_remaining_lockout / record_failure / record_success
"""
import time
import threading

_LOCK = threading.Lock()
_ATTEMPTS: dict[str, dict] = {}
MAX_FAILS = 5          # この回数の連続失敗でロック
WINDOW_SEC = 600       # 失敗カウントの有効期間（10分）
LOCKOUT_SEC = 300      # ロック時間（5分）


def _key(username: str, ip: str) -> str:
    return f"{(username or '').strip().lower()}|{ip or ''}"


def is_locked(username: str, ip: str) -> bool:
    now = time.time()
    with _LOCK:
        rec = _ATTEMPTS.get(_key(username, ip))
        return bool(rec and rec.get("locked_until", 0) > now)


def get_remaining_lockout(username: str, ip: str) -> int:
    now = time.time()
    with _LOCK:
        rec = _ATTEMPTS.get(_key(username, ip))
        if not rec:
            return 0
        return max(0, int(rec.get("locked_until", 0) - now))


def record_failure(username: str, ip: str) -> None:
    now = time.time()
    k = _key(username, ip)
    with _LOCK:
        rec = _ATTEMPTS.get(k)
        if not rec or now - rec.get("first", now) > WINDOW_SEC:
            rec = {"fails": 0, "first": now, "locked_until": 0}
        rec["fails"] += 1
        if rec["fails"] >= MAX_FAILS:
            rec["locked_until"] = now + LOCKOUT_SEC
            rec["fails"] = 0
            rec["first"] = now
        _ATTEMPTS[k] = rec


def record_success(username: str, ip: str) -> None:
    with _LOCK:
        _ATTEMPTS.pop(_key(username, ip), None)
