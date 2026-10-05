"""自動フォロー架電エンジン（Auto-Follow Dialer）。

「同意済みのお客様に、順番に自動でフォロー架電する」を"安全に"行う中核。
既存の守りの機能（同意／DNC／発信時間帯／合計上限／レート制限）を一つに束ね、
設計図（訪問営業｜自動フォロー設計図）のルールを機械で強制する。

守りの絶対ルール:
  - 同意なし・拒否済み(DNC)は発信しない
  - 1相手あたり 1日1回・合計 CONFIG.follow_cap 回（既定2回）を超えない
  - 希望時間帯（発信時間帯ガード）外には発信しない
  - 全体の1日上限（レート制限）を超えない
  - 応答・拒否・「連絡不要(DTMF 9)」で以降の自動発信を止める
  - 本日発信済みは二重に掛けない
  - エンジンが OFF／一時停止のときは1件も発信しない（既定 OFF＝明示ONするまで実発信ゼロ）

実架電は placer（発信関数）を注入する設計。テストはモックを渡し、本物の発信は呼ばない。
一斉無差別のオートダイヤラーではなく、同意済みの既存顧客への"フォロー"に限定した仕組み。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from . import calling_hours, disposition, dnc, followup, rate_limit
from .config import CONFIG

_lock = threading.Lock()

# IVR のプッシュ操作（DTMF）。設計図のメニューに対応。
DTMF = {
    "1": "日程変更",
    "2": "担当者と話す",
    "9": "連絡不要",
}


@dataclass(frozen=True)
class Decision:
    """1件の発信可否と、その理由（画面の「判定の根拠」に出す）。"""

    place: bool
    reason: str


# ======================================================================
# エンジンの状態（ON/OFF・一時停止）
# ======================================================================
def _default_state() -> dict:
    return {"enabled": bool(CONFIG.autofollow_enabled), "paused": False}


def status() -> dict:
    """現在のエンジン状態 {enabled, paused} を返す。"""
    path = CONFIG.autofollow_file
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {
                "enabled": bool(data.get("enabled", CONFIG.autofollow_enabled)),
                "paused": bool(data.get("paused", False)),
            }
    except (OSError, ValueError):
        pass
    return _default_state()


def _save(state: dict) -> None:
    path = CONFIG.autofollow_file
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


def is_enabled() -> bool:
    return status()["enabled"]


def set_enabled(value: bool) -> None:
    with _lock:
        st = status()
        st["enabled"] = bool(value)
        _save(st)


def set_paused(value: bool) -> None:
    with _lock:
        st = status()
        st["paused"] = bool(value)
        _save(st)


# ======================================================================
# 発信可否の判定（純粋ロジック・テスト可能）
# ======================================================================
def _global_block(now: datetime, enabled: bool, paused: bool,
                  records: list[dict] | None) -> str:
    """全体に効くブロック理由（空文字＝ブロック無し）。"""
    if not enabled:
        return "自動フォローOFF"
    if paused:
        return "一時停止中"
    if not calling_hours.allowed(now):
        return f"発信時間帯外（{calling_hours.window_text()}）"
    if not rate_limit.allowed(now, records):
        return "本日の全体発信上限に到達"
    return ""


def followed_today(entry: dict, now: datetime) -> bool:
    """この相手に本日（ローカル日付）すでにフォロー発信したか。"""
    last = entry.get("last_follow_at")
    if not last:
        return False
    try:
        dt = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return False
    off = CONFIG.call_hours_utc_offset
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    today = (now + timedelta(hours=off)).date()
    return (dt + timedelta(hours=off)).date() == today


def _contact_block(entry: dict, now: datetime) -> str:
    """相手ごとのブロック理由（空文字＝発信可）。"""
    cat = entry.get("category")
    if cat not in followup.CALLABLE:
        return f"対象外カテゴリ（{cat}）"
    if entry.get("consent") == "拒否":
        return "連絡拒否"
    num = entry.get("number") or ""
    if not num.startswith("+"):
        return "電話番号が不正（E.164でない）"
    if dnc.contains(num):
        return "DNC登録済み"
    cap = CONFIG.follow_cap
    if cap > 0 and int(entry.get("follow_count", 0)) >= cap:
        return f"合計上限に到達（{cap}回）"
    if followed_today(entry, now):
        return "本日発信済み"
    return ""


def decide(entry: dict, *, now: datetime | None = None,
           enabled: bool | None = None, paused: bool = False,
           records: list[dict] | None = None) -> Decision:
    """1件に発信してよいか（全ガード込み）と理由を返す。"""
    now = now or datetime.now(UTC)
    if enabled is None:
        enabled = is_enabled()
    g = _global_block(now, enabled, paused, records)
    if g:
        return Decision(False, g)
    c = _contact_block(entry, now)
    if c:
        return Decision(False, c)
    return Decision(True, "送信可")


def _score(entry: dict) -> int:
    return followup._SCORE.get(entry.get("category"), 60)


def select_next(entries: list[dict], *, now: datetime | None = None,
                enabled: bool | None = None, paused: bool = False,
                records: list[dict] | None = None) -> tuple[dict | None, Decision]:
    """次に掛ける1件を選ぶ。優先度（カテゴリスコア）降順→古いフォロー順。

    発信可能な先頭1件と Decision を返す。全体ブロック時や対象ゼロ時は (None, 理由)。
    """
    now = now or datetime.now(UTC)
    if enabled is None:
        enabled = is_enabled()
    g = _global_block(now, enabled, paused, records)
    if g:
        return None, Decision(False, g)
    ordered = sorted(entries, key=lambda e: (-_score(e), e.get("last_follow_at") or ""))
    for e in ordered:
        if not _contact_block(e, now):
            return e, Decision(True, "送信可")
    return None, Decision(False, "発信対象なし")


# ======================================================================
# 1件発信（実架電は placer 注入）
# ======================================================================
def _default_placer(entry: dict) -> dict:
    """本番の発信関数。既存の outbound を使って1件だけ発信する。

    ※ run_once は既定 OFF のため、enabled を明示 ON にしない限り呼ばれない。
    """
    from . import outbound

    return outbound.bridge_call(entry.get("number", ""), agent_name="自動フォロー")


def run_once(*, now: datetime | None = None, entries: list[dict] | None = None,
             placer=None, enabled: bool | None = None, paused: bool = False,
             records: list[dict] | None = None) -> dict:
    """次の1件を選んで発信し、フォロー回数を記録する。

    enabled/paused・全ガードを満たさなければ発信しない（placer を呼ばない）。
    結果 {placed, reason?, entry?, result?} を返す。
    """
    now = now or datetime.now(UTC)
    if enabled is None:
        enabled = is_enabled()
    if entries is None:
        entries = followup.load()
    entry, dec = select_next(entries, now=now, enabled=enabled, paused=paused, records=records)
    if entry is None:
        return {"placed": False, "reason": dec.reason}
    placer = placer or _default_placer
    result = placer(entry)
    followup.record_follow(entry.get("number", ""))
    return {"placed": True, "entry": entry, "result": result}


# ======================================================================
# IVR（DTMF）と発信結果の反映
# ======================================================================
def on_dtmf(entry: dict, digit: str) -> dict:
    """IVR のプッシュ操作を処理し、やることを返す。

    9=今後の連絡不要 → 連絡停止＋DNC 登録（以降の自動発信を止める）
    2=担当者と話す   → 担当者へ引き継ぎ要求
    1=日程変更       → 再調整の意思あり
    """
    digit = str(digit)
    out: dict = {"digit": digit, "action": DTMF.get(digit, "")}
    if digit == "9":
        disposition.record(entry.get("number", ""), disposition.DECLINE, add_dnc=True)
        out["stop"] = True
    elif digit == "2":
        out["handoff"] = True
    elif digit == "1":
        out["reschedule"] = True
    return out


def register_result(entry: dict, *, answered: bool, digit: str | None = None) -> dict:
    """発信結果を反映し、以降の自動発信を止めるべきかを返す。

    応答して「連絡不要(9)」または「担当者へ(2)」なら、そのサイクルは完了＝停止扱い。
    """
    stop = False
    detail: dict = {}
    if digit is not None:
        detail = on_dtmf(entry, digit)
        stop = bool(detail.get("stop") or detail.get("handoff"))
    return {"stop": stop, "answered": bool(answered), **({"dtmf": detail} if detail else {})}
