"""架電記録の CSV 書き出し（GET /tac/calls.csv）のテスト（TDD）。

監査への提出・月次報告・Excel 集計に、架電記録をそのまま渡せるようにする。
電話番号＝個人情報なのでトークン認証必須。Excel で数式として実行される値は無害化する。
"""

from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import calls_export  # noqa: E402
from tac.config import CONFIG  # noqa: E402

JST = 9
HEADER = ["日時", "方向", "番号", "結果", "理由", "通話結果", "DNC登録", "名乗り", "商品", "会議名", "段階"]


def rows_of(text: str) -> list[list[str]]:
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text.lstrip("﻿"))))


# --- 純関数 ----------------------------------------------------------------------


def test_header_bom_and_crlf():
    text = calls_export.to_csv([], JST)
    assert text == "﻿" + ",".join(HEADER) + "\r\n"


def test_to_local_converts_utc_to_jst_across_midnight():
    assert calls_export.to_local("2026-09-29T15:30:00+00:00", JST) == "2026-09-30 00:30:00"
    assert calls_export.to_local("2026-09-30T02:24:05.123456+00:00", JST) == "2026-09-30 11:24:05"


def test_to_local_keeps_unreadable_values():
    assert calls_export.to_local("壊れた時刻", JST) == "壊れた時刻"
    assert calls_export.to_local(None, JST) == ""


def test_rows_map_fields_and_leave_missing_blank():
    recs = [
        {"ts": "2026-09-30T02:00:00+00:00", "direction": "outbound", "to": "0312345678", "status": "dialed",
         "room": "tac-abc", "disclosed": True, "product": "中古住宅"},
        {"ts": "2026-09-30T03:00:00+00:00", "direction": "outbound", "to": "0312345678", "status": "disposition",
         "disposition": "拒否", "dnc_added": True},
        {"ts": "2026-09-30T04:00:00+00:00", "direction": "outbound", "to": "0312345678", "status": "blocked",
         "reason": "dnc"},
    ]
    _, r1, r2, r3 = rows_of(calls_export.to_csv(recs, JST))
    assert r1 == ["2026-09-30 11:00:00", "outbound", "0312345678", "dialed", "", "", "", "はい", "中古住宅", "tac-abc", ""]
    assert r2[5:7] == ["拒否", "はい"]
    assert r3[3:5] == ["blocked", "dnc"]


def test_non_dict_records_are_skipped():
    assert len(rows_of(calls_export.to_csv(["x", None, 3, {"status": "dialed"}], JST))) == 2


@pytest.mark.parametrize("raw", ['=HYPERLINK("http://evil","x")', "@SUM(A1)", "-1+1", "+819012345678", "\t=1", "\r=1"])
def test_formula_like_values_are_neutralized(raw):
    assert calls_export.safe_cell(raw) == "'" + raw
    [_, row] = rows_of(calls_export.to_csv([{"to": raw}], JST))
    assert row[2] == "'" + raw


def test_plain_values_and_csv_special_chars_survive():
    assert calls_export.safe_cell("0312345678") == "0312345678"
    [_, row] = rows_of(calls_export.to_csv([{"to": "03,12", "reason": 'a"b\nc'}], JST))
    assert row[2] == "03,12" and row[4] == 'a"b\nc'


def test_in_range_uses_local_dates_inclusive():
    recs = [
        {"ts": "2026-08-31T14:59:59+00:00", "to": "a"},  # JST 8/31 23:59:59 → 範囲外
        {"ts": "2026-08-31T15:00:00+00:00", "to": "b"},  # JST 9/1 00:00 → 範囲内（開始日）
        {"ts": "2026-09-30T14:59:59+00:00", "to": "c"},  # JST 9/30 23:59:59 → 範囲内（終了日）
        {"ts": "2026-09-30T15:00:00+00:00", "to": "d"},  # JST 10/1 → 範囲外
        {"ts": "壊れた", "to": "e"},                     # 絞り込み時は日付不明を落とす
    ]
    got = calls_export.in_range(recs, date(2026, 9, 1), date(2026, 9, 30), JST)
    assert [r["to"] for r in got] == ["b", "c"]
    assert [r["to"] for r in calls_export.in_range(recs, None, None, JST)] == ["a", "b", "c", "d", "e"]


# --- ルート（flask 未導入ならスキップ） ---------------------------------------------


@pytest.fixture
def client():
    import importlib

    try:
        server = importlib.import_module("tac.server")
    except Exception:  # noqa: BLE001 - flask 未導入環境ではスキップ
        pytest.skip("flask 未導入")
    saved = (CONFIG.outbound_token, CONFIG.calllog_file)
    log = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for ts, to in (("2026-09-01T01:00:00+00:00", "+81901"), ("2026-09-20T01:00:00+00:00", "+81902")):
        log.write(json.dumps({"ts": ts, "direction": "outbound", "to": to, "status": "dialed"}) + "\n")
    log.close()
    CONFIG.calllog_file = log.name
    yield server.app.test_client()
    CONFIG.outbound_token, CONFIG.calllog_file = saved


def test_route_requires_token(client):
    CONFIG.outbound_token = ""
    assert client.get("/tac/calls.csv").status_code == 503
    CONFIG.outbound_token = "t0ken"
    assert client.get("/tac/calls.csv?token=wrong").status_code == 401


def test_route_returns_csv_download(client):
    CONFIG.outbound_token = "t0ken"
    r = client.get("/tac/calls.csv", headers={"X-TAC-Token": "t0ken"})
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    assert "attachment" in r.headers["Content-Disposition"] and ".csv" in r.headers["Content-Disposition"]
    assert "no-store" in r.headers["Cache-Control"]
    rows = rows_of(r.get_data(as_text=True))
    assert rows[0] == HEADER and len(rows) == 3


def test_route_filters_by_date(client):
    CONFIG.outbound_token = "t0ken"
    r = client.get("/tac/calls.csv?from=2026-09-10&to=2026-09-30", headers={"X-TAC-Token": "t0ken"})
    rows = rows_of(r.get_data(as_text=True))
    assert [row[2] for row in rows[1:]] == ["'+81902"]


def test_route_rejects_bad_dates(client):
    CONFIG.outbound_token = "t0ken"
    r = client.get("/tac/calls.csv?from=2026/09/01", headers={"X-TAC-Token": "t0ken"})
    assert r.status_code == 400
    assert "YYYY-MM-DD" in r.get_json()["error"]
