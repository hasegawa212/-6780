"""架電記録の CSV 書き出し（GET /tac/calls.csv）。

監査への提出・月次報告・Excel 集計に、架電記録（calls.jsonl）をそのまま渡せる形にする。
- 日時は発信時間帯ガードと同じ UTC オフセット（既定 JST=UTC+9）の現地時刻で出す
- BOM 付き UTF-8・CRLF（Excel で文字化けせず開ける）
- Excel で数式として実行される値（= + - @ タブ 改行 始まり）は先頭に ' を付けて無害化する
  （CSV インジェクション対策。+81... の電話番号も文字列として保たれる）
電話番号＝個人情報を含むので、配信側（server.py）でトークン認証を必須にする。
flask 非依存の純関数だけを置く。
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, timedelta

# (見出し, 記録のキー)
COLUMNS = (
    ("日時", "ts"),
    ("方向", "direction"),
    ("番号", "to"),
    ("結果", "status"),
    ("理由", "reason"),
    ("通話結果", "disposition"),
    ("DNC登録", "dnc_added"),
    ("名乗り", "disclosed"),
    ("商品", "product"),
    ("会議名", "room"),
    ("段階", "stage"),
)
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _parse(ts) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def to_local(ts, offset: int) -> str:
    """UTC の ISO 時刻を現地時刻「YYYY-MM-DD HH:MM:SS」に。読めなければそのまま返す。"""
    dt = _parse(ts)
    if dt is None:
        return "" if ts is None else str(ts)
    return (dt.astimezone(UTC) + timedelta(hours=offset)).strftime("%Y-%m-%d %H:%M:%S")


def in_range(records: list, start: date | None, end: date | None, offset: int) -> list:
    """現地日付で [start, end]（両端含む）に入る記録。どちらも None なら全件そのまま。"""
    if start is None and end is None:
        return list(records)
    out = []
    for r in records:
        dt = _parse(r.get("ts")) if isinstance(r, dict) else None
        if dt is None:
            continue  # 絞り込み時は日付の分からない記録を含めない
        d = (dt.astimezone(UTC) + timedelta(hours=offset)).date()
        if (start is None or d >= start) and (end is None or d <= end):
            out.append(r)
    return out


def safe_cell(value) -> str:
    """Excel で数式として実行されないよう、危ない先頭文字の値に ' を付ける。"""
    s = "" if value is None else str(value)
    return "'" + s if s.startswith(_FORMULA_START) else s


def _cell(key: str, value, offset: int) -> str:
    if key == "ts":
        return safe_cell(to_local(value, offset))
    if isinstance(value, bool):
        return "はい" if value else "いいえ"
    return safe_cell(value)


def to_csv(records: list, offset: int) -> str:
    """見出し＋記録を BOM 付き・CRLF の CSV 文字列にする。辞書でない記録は飛ばす。"""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([label for label, _ in COLUMNS])
    for rec in records:
        if isinstance(rec, dict):
            w.writerow([_cell(key, rec.get(key), offset) for _, key in COLUMNS])
    return "﻿" + buf.getvalue()
