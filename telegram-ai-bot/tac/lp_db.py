"""ライフパートナーの CRM（SQLite、本番は /data/lifepartner.db）。

置くもの：顧客・連絡の許可（目的ごと）・調査・回答（選択肢とスキップだけ）・関心・架電の試行・予約・
DNC のミラー・監査ログ・通話中の会話の状態。

守ること：
- 発話の原文は保存しない（健康状態・収入などが入りうる）。回答は選択肢だけ。
- DNC の正本は `dnc.txt`（`tac/dnc.py`）のまま。ここの `dnc_entries` は集計・監査用のミラー。
- 監査ログに電話番号を書かない（顧客は customer_id で指す）。
- 読めない・壊れているときは例外にする。呼び出し側は「掛けない」に倒す（fail closed）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from .config import CONFIG

_LOCK = threading.RLock()
_READY: set[str] = set()
# 架電の試行がこの時間を過ぎても終わっていなければ、状態コールバックが届かなかったとみなす
_STALE = timedelta(minutes=30)
_ACTIVE = ("queued", "dialing", "initiated", "ringing", "in-progress")

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS customers (
        customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        phone_e164 TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS contact_permissions (
        permission_id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
        purpose TEXT NOT NULL,
        channel TEXT NOT NULL,
        status TEXT NOT NULL,
        obtained_at TEXT,
        revoked_at TEXT,
        disclosure_version TEXT NOT NULL,
        evidence_reference TEXT NOT NULL,
        provider TEXT,
        UNIQUE(customer_id, purpose, channel))""",
    """CREATE TABLE IF NOT EXISTS surveys (
        survey_id TEXT NOT NULL,
        survey_version TEXT NOT NULL,
        title TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(survey_id, survey_version))""",
    """CREATE TABLE IF NOT EXISTS survey_responses (
        response_id INTEGER PRIMARY KEY AUTOINCREMENT,
        survey_id TEXT NOT NULL,
        survey_version TEXT NOT NULL,
        customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
        call_id TEXT,
        question_id TEXT NOT NULL,
        answer_value TEXT,
        skipped INTEGER NOT NULL DEFAULT 0,
        answered_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS interest_profiles (
        customer_id INTEGER PRIMARY KEY REFERENCES customers(customer_id),
        lifestyle_interest TEXT,
        household_budget_interest TEXT,
        financial_education_interest TEXT,
        insurance_review_interest TEXT,
        future_life_interest TEXT,
        updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS call_attempts (
        attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_id TEXT UNIQUE,
        customer_id INTEGER REFERENCES customers(customer_id),
        campaign_id TEXT NOT NULL,
        call_status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        outcome TEXT,
        idempotency_key TEXT NOT NULL UNIQUE,
        result_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS appointments (
        appointment_id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
        consultation_type TEXT NOT NULL,
        provider_id TEXT NOT NULL,
        appointment_at TEXT NOT NULL,
        status TEXT NOT NULL,
        consent_reference TEXT NOT NULL,
        external_ref TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""",
    # 同じ担当・同じ時刻に有効な予約は 1 件だけ（重複予約の防止）
    """CREATE UNIQUE INDEX IF NOT EXISTS appointments_slot
        ON appointments(provider_id, appointment_at) WHERE status IN ('REQUESTED', 'CONFIRMED')""",
    """CREATE TABLE IF NOT EXISTS dnc_entries (
        phone_e164 TEXT PRIMARY KEY,
        reason TEXT,
        registered_at TEXT NOT NULL,
        source TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS audit_logs (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        entity_id TEXT,
        occurred_at TEXT NOT NULL,
        metadata TEXT)""",
    """CREATE TABLE IF NOT EXISTS call_sessions (
        call_sid TEXT PRIMARY KEY,
        phone_e164 TEXT NOT NULL,
        state_json TEXT NOT NULL,
        turn INTEGER NOT NULL,
        last_reply_json TEXT,
        updated_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS responses_customer ON survey_responses(customer_id)",
    "CREATE INDEX IF NOT EXISTS attempts_status ON call_attempts(call_status, started_at)",
)
SCHEMA_VERSION = 1


def _iso(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat()


def _open() -> sqlite3.Connection:
    path = CONFIG.lp_db_file
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init() -> None:
    """スキーマを作る（何度呼んでもよい）。"""
    path = CONFIG.lp_db_file
    with _LOCK:
        conn = _open()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("BEGIN IMMEDIATE")
            for stmt in _SCHEMA:
                conn.execute(stmt)
            conn.execute("INSERT OR IGNORE INTO schema_version VALUES (?, ?)", (SCHEMA_VERSION, _iso()))
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        _READY.add(os.path.abspath(path))


@contextmanager
def tx():
    """1 つのトランザクション（BEGIN IMMEDIATE）。失敗したら巻き戻す。"""
    if os.path.abspath(CONFIG.lp_db_file) not in _READY or not os.path.isfile(CONFIG.lp_db_file):
        init()
    with _LOCK:
        conn = _open()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def audit(conn: sqlite3.Connection, actor: str, action: str, entity_id: str | int | None = None,
          metadata: dict | None = None, *, now: datetime | None = None) -> None:
    """監査ログ。電話番号は書かない（metadata に入れない）。"""
    conn.execute(
        "INSERT INTO audit_logs(actor, action, entity_id, occurred_at, metadata) VALUES (?,?,?,?,?)",
        (actor, action, None if entity_id is None else str(entity_id), _iso(now),
         json.dumps(metadata or {}, ensure_ascii=False)),
    )


def upsert_customer(conn: sqlite3.Connection, phone_e164: str, *, name: str | None = None,
                    now: datetime | None = None) -> int:
    at = _iso(now)
    row = conn.execute("SELECT customer_id, name FROM customers WHERE phone_e164=?", (phone_e164,)).fetchone()
    if row:
        if name and not row["name"]:
            conn.execute("UPDATE customers SET name=?, updated_at=? WHERE customer_id=?", (name, at, row["customer_id"]))
        return int(row["customer_id"])
    cur = conn.execute("INSERT INTO customers(name, phone_e164, created_at, updated_at) VALUES (?,?,?,?)",
                       (name, phone_e164, at, at))
    return int(cur.lastrowid)


def customer_id(conn: sqlite3.Connection, phone_e164: str) -> int | None:
    row = conn.execute("SELECT customer_id FROM customers WHERE phone_e164=?", (phone_e164,)).fetchone()
    return int(row["customer_id"]) if row else None


def ensure_survey(conn: sqlite3.Connection, survey_id: str, version: str, title: str) -> None:
    conn.execute("INSERT OR IGNORE INTO surveys VALUES (?,?,?,?,?)", (survey_id, version, title, "ACTIVE", _iso()))


def set_permission(conn: sqlite3.Connection, cid: int, purpose: str, status: str, *, channel: str = "phone",
                   disclosure_version: str, evidence: str, provider: str = "", now: datetime | None = None) -> None:
    at = _iso(now)
    obtained = at if status == "GRANTED" else None
    conn.execute(
        """INSERT INTO contact_permissions(customer_id, purpose, channel, status, obtained_at, revoked_at,
                                           disclosure_version, evidence_reference, provider)
           VALUES (?,?,?,?,?,NULL,?,?,?)
           ON CONFLICT(customer_id, purpose, channel) DO UPDATE SET
             status=excluded.status, obtained_at=excluded.obtained_at, revoked_at=NULL,
             disclosure_version=excluded.disclosure_version, evidence_reference=excluded.evidence_reference,
             provider=excluded.provider""",
        (cid, purpose, channel, status, obtained, disclosure_version, evidence, provider),
    )


def mirror_dnc(phone_e164: str, *, source: str, reason: str = "", now: datetime | None = None) -> None:
    with tx() as c:
        c.execute("INSERT OR IGNORE INTO dnc_entries VALUES (?,?,?,?)", (phone_e164, reason, _iso(now), source))
        cid = upsert_customer(c, phone_e164, now=now)
        audit(c, source, "dnc_register", cid, {"reason": reason}, now=now)


# ---- 通話中の会話の状態（再起動・マシン入れ替えをまたいで残す） ----
def save_session(call_sid: str, phone_e164: str, state: dict, turn: int, last_reply: dict | None) -> None:
    with tx() as c:
        c.execute(
            """INSERT INTO call_sessions VALUES (?,?,?,?,?,?)
               ON CONFLICT(call_sid) DO UPDATE SET state_json=excluded.state_json, turn=excluded.turn,
                 last_reply_json=excluded.last_reply_json, updated_at=excluded.updated_at""",
            (call_sid, phone_e164, json.dumps(state, ensure_ascii=False), turn,
             None if last_reply is None else json.dumps(last_reply, ensure_ascii=False), _iso()),
        )


def load_session(call_sid: str) -> dict | None:
    with tx() as c:
        row = c.execute("SELECT * FROM call_sessions WHERE call_sid=?", (call_sid,)).fetchone()
    if row is None:
        return None
    return {"phone_e164": row["phone_e164"], "state": json.loads(row["state_json"]), "turn": int(row["turn"]),
            "last_reply": json.loads(row["last_reply_json"]) if row["last_reply_json"] else None}


def delete_session(call_sid: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM call_sessions WHERE call_sid=?", (call_sid,))


# ---- 架電の試行（同時架電数の上限・冪等性） ----
def start_attempt(phone_e164: str, *, idempotency_key: str, campaign_id: str = "lifepartner",
                  name: str | None = None, now: datetime | None = None) -> dict:
    """発信の前に試行を 1 件登録する。同じ冪等性キーが既にあれば、その記録を返す（duplicate=True）。"""
    with tx() as c:
        row = c.execute("SELECT * FROM call_attempts WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if row:
            return {"duplicate": True, "attempt_id": row["attempt_id"],
                    "result": json.loads(row["result_json"]) if row["result_json"] else None}
        cid = upsert_customer(c, phone_e164, name=name, now=now)
        cur = c.execute(
            """INSERT INTO call_attempts(customer_id, campaign_id, call_status, started_at, idempotency_key)
               VALUES (?,?,?,?,?)""", (cid, campaign_id, "queued", _iso(now), idempotency_key))
        audit(c, "system", "call_queued", cid, {"campaign": campaign_id}, now=now)
        return {"duplicate": False, "attempt_id": int(cur.lastrowid)}


def finish_dial(attempt_id: int, *, ok: bool, call_id: str = "", result: dict | None = None,
                now: datetime | None = None) -> None:
    with tx() as c:
        if ok:
            c.execute("UPDATE call_attempts SET call_id=?, call_status='dialing', result_json=? WHERE attempt_id=?",
                      (call_id or None, json.dumps(result or {}), attempt_id))
        else:
            c.execute("""UPDATE call_attempts SET call_status='failed', ended_at=?, outcome='DIAL_FAILED',
                         result_json=? WHERE attempt_id=?""", (_iso(now), json.dumps(result or {}), attempt_id))


def update_call_status(call_id: str, status: str, *, now: datetime | None = None) -> bool:
    status = (status or "").strip().lower()
    if not call_id or not status:
        return False
    ended = None if status in _ACTIVE else _iso(now)
    with tx() as c:
        cur = c.execute("UPDATE call_attempts SET call_status=?, ended_at=COALESCE(ended_at, ?) WHERE call_id=?",
                        (status, ended, call_id))
        return cur.rowcount > 0


def set_outcome(call_id: str, outcome: str) -> None:
    with tx() as c:
        c.execute("UPDATE call_attempts SET outcome=? WHERE call_id=?", (outcome, call_id))


def active_calls(*, now: datetime | None = None) -> int:
    """いま発信中・通話中の調査の電話の本数（状態が届かず古くなったものは数えない）。"""
    since = _iso((now or datetime.now(UTC)) - _STALE)
    with tx() as c:
        q = ",".join("?" * len(_ACTIVE))
        return int(c.execute(f"SELECT COUNT(*) FROM call_attempts WHERE call_status IN ({q}) AND started_at >= ?",
                             (*_ACTIVE, since)).fetchone()[0])


# ---- 保存期間・バックアップ ----
def purge_expired(*, days: int, now: datetime | None = None) -> int:
    """保存期間を過ぎた回答と関心を消す。消した件数を返す。"""
    cutoff = _iso((now or datetime.now(UTC)) - timedelta(days=days))
    with tx() as c:
        n = c.execute("DELETE FROM survey_responses WHERE answered_at < ?", (cutoff,)).rowcount
        n += c.execute("DELETE FROM interest_profiles WHERE updated_at < ?", (cutoff,)).rowcount
        c.execute("DELETE FROM call_sessions WHERE updated_at < ?", (cutoff,))
        audit(c, "system", "retention_purge", None, {"days": days, "removed": n}, now=now)
    return n


def backup(dest: str) -> dict:
    """稼働中でも一貫したコピーを作る（SQLite のオンラインバックアップ）。sha256 を返す。"""
    init()
    with _LOCK:
        src = _open()
        try:
            out = sqlite3.connect(dest)
            try:
                src.backup(out)
                ok = out.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            finally:
                out.close()
        finally:
            src.close()
    h = hashlib.sha256()
    with open(dest, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return {"ok": ok, "path": dest, "sha256": h.hexdigest()}


if __name__ == "__main__":  # python -m tac.lp_db backup /data/backup/lifepartner-YYYYMMDD.db
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "backup":
        print(json.dumps(backup(sys.argv[2])))
    else:
        print("usage: python -m tac.lp_db backup <dest.db>")
        raise SystemExit(2)
