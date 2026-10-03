"""担当者名簿（Agent Roster）。

複数の担当者を環境変数 TAC_AGENTS に登録し、発信ごとに「名前」または「番号」で
選べるようにする。未指定ならラウンドロビンで自動振り分け。各担当者が1件ずつ
受け持つので、人数分の並行発信になる（一斉自動発信=オートダイヤラーではない。
1担当者=1通話のまま、正直な範囲での効率化）。

TAC_AGENTS の書式（カンマ区切り。名前は省略可）::

    田中:+818011112222,佐藤:+818033334444
    +818011112222,+818033334444          # 番号のみも可

未設定なら単一の TAC_AGENT_NUMBER にフォールバック（後方互換）。
"""

from __future__ import annotations

import threading

from .config import CONFIG

_lock = threading.Lock()
_rr = {"i": 0}  # ラウンドロビンの位置（プロセス内）


def roster() -> list[dict]:
    """登録済み担当者を [{"name","number"}, ...] で返す。"""
    raw = CONFIG.agents or ""
    out: list[dict] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            name, _, num = item.partition(":")
            name, num = name.strip(), num.strip()
        else:
            name, num = "", item
        if num:
            out.append({"name": name, "number": num})
    if not out and CONFIG.agent_number:
        out.append({"name": getattr(CONFIG, "agent_name", "") or "",
                    "number": CONFIG.agent_number})
    return out


def resolve(ident: str | None) -> str | None:
    """名前または番号から担当者番号(E.164)を返す。

    名簿内の名前/番号に一致すればその番号。名簿に無くても E.164(+始まり)なら
    そのまま許可。未指定/不明は None。
    """
    if not ident:
        return None
    ident = ident.strip()
    for a in roster():
        if ident == a["number"] or (a["name"] and ident == a["name"]):
            return a["number"]
    if ident.startswith("+"):
        return ident
    return None


def next_agent() -> str | None:
    """ラウンドロビンで次の担当者番号を返す。名簿が空なら None。"""
    r = roster()
    if not r:
        return None
    with _lock:
        i = _rr["i"] % len(r)
        _rr["i"] = (i + 1) % len(r)
    return r[i]["number"]
