"""電話5問 → 仮ランク（sales-rank）の判定と記録。

判定ロジックは sales-rank（リポジトリ直下の sales-rank/screening.py）の 1 か所に置き、
ここはそれを呼んで結果を JSONL に残すだけ（判定を二重化しない）。

さくらは相手が会話の中で自分から話した内容だけを記録する（聞き出さない・年収は扱わない）。
仮ランクは社内の判断材料で、相手には伝えない。電話番号を含むので保存ファイルは gitignore、
閲覧はトークン認証のコンソールだけ。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from .config import CONFIG

FIELDS = ("rent_yen", "tenure_years", "job_change", "decision_maker_present", "has_motivation")
JOB_CHANGE_LABELS = ("なし", "転勤の可能性あり", "転職を検討中")
MAX_RENT_YEN = 10_000_000
MAX_TENURE_YEARS = 60

_lock = threading.Lock()


def sales_rank_dir() -> Path:
    """sales-rank の場所。既定はリポジトリ直下（手元・CI・本番イメージで同じ並び）。"""
    if CONFIG.sales_rank_dir:
        return Path(CONFIG.sales_rank_dir)
    return Path(__file__).resolve().parents[2] / "sales-rank"


@cache
def _sales_rank():
    """sales-rank/screening.py をファイルの場所で読む（ありふれた名前の衝突を避ける）。"""
    name = "tac_sales_rank_screening"
    spec = importlib.util.spec_from_file_location(name, sales_rank_dir() / "screening.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclass が自モジュールを引けるように
    spec.loader.exec_module(mod)
    return mod


def _number(value, lo: float, hi: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if lo <= value <= hi else None


def _normalize(answers: dict) -> tuple[dict, list[str]]:
    """道具の引数を検算済みの値に揃える。型違い・範囲外は未確認（None）にし、そのキーを返す。"""
    out: dict = {}
    ignored: list[str] = []
    for key in FIELDS:
        raw = answers.get(key)
        if key == "rent_yen":
            n = _number(raw, 0, MAX_RENT_YEN)
            value = round(n) if n is not None else None
        elif key == "tenure_years":
            value = _number(raw, 0, MAX_TENURE_YEARS)
        elif key == "job_change":
            value = raw if raw in JOB_CHANGE_LABELS else None
        else:
            value = raw if isinstance(raw, bool) else None
        if raw is not None and value is None:
            ignored.append(key)
        out[key] = value
    return out, ignored


def evaluate(answers: dict) -> dict:
    """5問の答え（どれも省略可）から、sales-rank の仮ランク・理由・未確認項目を返す。"""
    values, ignored = _normalize(answers or {})
    sr = _sales_rank()
    job = {j.value: j for j in sr.JobChange}.get(values["job_change"])
    result = sr.provisional_rank(sr.PhoneAnswers(**{**values, "job_change": job}))
    return {
        "rank": result.rank.value,
        "reasons": list(result.reasons),
        "missing": list(result.missing),
        "answers": values,
        "ignored": ignored,
    }


def append(call_sid: str, caller: str, result: dict) -> dict:
    """判定結果を 1 件追記する。記録に失敗しても通話は止めない。"""
    rec = {"ts": datetime.now(UTC).isoformat(), "call_sid": call_sid, "caller": caller, **result}
    path = CONFIG.screening_file
    try:
        with _lock:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return rec


def recent(limit: int = 50) -> list[dict]:
    """直近 limit 件を新しい順で。壊れた行・辞書でない行は飛ばす。"""
    try:
        with open(CONFIG.screening_file, encoding="utf-8", errors="replace") as f:
            lines = [ln for ln in f if ln.strip()]
    except OSError:
        return []
    out: list[dict] = []
    for ln in lines:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out[-limit:][::-1]
