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


def _norm_number(raw: str) -> str:
    """キューに入る番号を自動で E.164(+81…) に正規化する。

    iPhone 入力の 090-1234-5678 / 全角 / 00 国際プレフィックス等を +81 形式へ。
    読めない番号は生のまま保持（データを落とさない。発信側ガードが弾く）。
    """
    from . import phone
    return phone.to_e164(raw) or (raw or "")


def load(*, sort: str = "", q: str = "", folder: str | None = None) -> list[dict]:
    """リストを読み込む。sort/q で並び替え・絞り込み。folder で分離。

    folder=None: 全件（後方互換）
    folder="名前": そのフォルダだけ
    folder="": フォルダなしエントリだけ
    """
    entries = _read()
    if folder is not None:
        entries = [e for e in entries if (e.get("folder") or "") == folder]
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


def add(*, number: str, name: str = "", area: str = "", score: int = 0, note: str = "", folder: str = "") -> dict:
    """1 件追加する。"""
    entry = {"number": _norm_number(number), "name": name, "area": area, "score": score, "note": note, "folder": folder}
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
                "number": _norm_number(item.get("number", "")),
                "name": item.get("name", ""),
                "area": item.get("area", ""),
                "score": item.get("score", 0),
                "note": item.get("note", ""),
                "folder": item.get("folder", ""),
            })
        _write(entries)
    return len(items)


def replace(items: list[dict]) -> int:
    """リストをまるごと入れ替える（既存を全消去して items で上書き）。件数を返す。

    #09 等を再取り込みする際、add_bulk だと重複が積み上がるため、同期用途では
    こちらで総入れ替えする。空リストを渡せばクリアになる。
    """
    new_entries = [{
        "number": _norm_number(item.get("number", "")),
        "name": item.get("name", ""),
        "area": item.get("area", ""),
        "score": item.get("score", 0),
        "note": item.get("note", ""),
        "folder": item.get("folder", ""),
    } for item in items]
    with _lock:
        _write(new_entries)
    return len(new_entries)


def folders() -> list[dict]:
    """フォルダ名と件数の一覧を返す。"""
    entries = _read()
    counts: dict[str, int] = {}
    for e in entries:
        name = e.get("folder") or ""
        counts[name] = counts.get(name, 0) + 1
    return [{"name": n, "count": c} for n, c in sorted(counts.items())]


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
