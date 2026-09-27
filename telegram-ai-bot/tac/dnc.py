"""DNC（Do Not Call / 発信禁止リスト）。

「断られた相手には再発信しない」を仕組みで担保する最小実装。
ファイル(1行1番号)で永続化し、発信ブリッジ(outbound.bridge_call)から参照する。

方針:
- 番号は E.164（例 +817066546780）で保持するのを推奨。normalize で空白/記号を除去。
- ここは「発信しない」ためのブロックリスト。安全側（判定できなければブロックしない）
  ではなく、登録があれば確実に止める。
"""

from __future__ import annotations

import os
import re
import threading

from .config import CONFIG

_lock = threading.Lock()


def normalize(number: str) -> str:
    """空白・ハイフン・括弧を除去。先頭の + と数字は保持する。"""
    if not number:
        return ""
    return re.sub(r"[\s\-().]", "", number.strip())


def _read_set() -> set[str]:
    try:
        with open(CONFIG.dnc_file, encoding="utf-8") as f:
            return {
                normalize(line) for line in f
                if line.strip() and not line.lstrip().startswith("#")
            }
    except OSError:
        return set()


def contains(number: str) -> bool:
    """この番号が発信禁止リストに載っているか。"""
    n = normalize(number)
    return bool(n) and n in _read_set()


def add(number: str) -> bool:
    """登録する。新規に追加したら True、既に有る/無効なら False。"""
    n = normalize(number)
    if not n:
        return False
    with _lock:
        if n in _read_set():
            return False
        path = CONFIG.dnc_file
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(n + "\n")
    return True


def remove(number: str) -> bool:
    """解除する。実際に削除したら True。"""
    n = normalize(number)
    if not n:
        return False
    with _lock:
        current = _read_set()
        if n not in current:
            return False
        current.discard(n)
        with open(CONFIG.dnc_file, "w", encoding="utf-8") as f:
            for x in sorted(current):
                f.write(x + "\n")
    return True


def all() -> list[str]:  # noqa: A001 - 意図的に一覧APIとして all を使う
    """登録されている番号の一覧（ソート済み）。"""
    return sorted(_read_set())
