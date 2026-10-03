"""スマートリスト（Smart Queue）。

優先順の発信リストを JSON ファイルで管理する。名前・エリア・スコアでの
並び替え、キーワード検索に対応。ファイルが無ければ空リスト（安全にdegrade）。
"""

from __future__ import annotations

import json
import os
import threading

from .config import CONFIG

_lock = threading.Lock()


def load(*, sort: str = "", q: str = "") -> list[dict]:
    """リストを読み込む。sort/q で並び替え・絞り込み。"""
    entries = _read()
    if q:
        q_lower = q.lower()
        entries = [
            e for e in entries
            if q_lower in (e.get("name") or "").lower()
            or q_lower in (e.get("area") or "").lower()
            or q_lower in (e.get("number") or "").lower()
        ]
    if sort == "score":
        entries.sort(key=lambda e: e.get("score", 0), reverse=True)
    elif sort == "name":
        entries.sort(key=lambda e: e.get("name") or "")
    elif sort == "area":
        entries.sort(key=lambda e: e.get("area") or "")
    return entries


def add(*, number: str, name: str = "", area: str = "", score: int = 0, note: str = "") -> dict:
    """1 件追加する。"""
    entry = {"number": number, "name": name, "area": area, "score": score, "note": note}
    with _lock:
        entries = _read()
        entries.append(entry)
        _write(entries)
    return entry


def add_bulk(items: list[dict]) -> int:
    """複数件を一括追加する。追加件数を返す。"""
    with _lock:
        entries = _read()
        for item in items:
            entries.append({
                "number": item.get("number", ""),
                "name": item.get("name", ""),
                "area": item.get("area", ""),
                "score": item.get("score", 0),
                "note": item.get("note", ""),
            })
        _write(entries)
    return len(items)


def _read() -> list[dict]:
    try:
        with open(CONFIG.queue_file, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write(entries: list[dict]) -> None:
    path = CONFIG.queue_file
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
