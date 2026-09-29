"""架電記録の永続化と堅牢化のテスト（TDD）。

架電記録は監査証跡であり、発信の1日上限（rate_limit）の数え元でもある。
- 再デプロイで消えないよう、fly.toml で永続ボリューム（/data）に置く
- 1 行壊れても集計・上限判定・コンソールを止めない（壊れた行は飛ばす）
- 同じ番号の表記ゆれ（空白・ハイフン・括弧）を DNC と同じ揃え方で 1 番号と数える
"""

from __future__ import annotations

import json
import sys
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import calllog, rate_limit  # noqa: E402
from tac.config import CONFIG  # noqa: E402

FLY_TOML = Path(__file__).resolve().parent.parent / "tac" / "fly.toml"
NOW = datetime(2026, 9, 29, 3, 0, tzinfo=UTC)  # JST 12:00


def _log_with(lines: list[bytes]) -> str:
    """任意のバイト列の行で架電記録ファイルを作り、CONFIG をそこへ向ける。"""
    f = tempfile.NamedTemporaryFile("wb", suffix=".jsonl", delete=False)
    f.write(b"".join(ln + b"\n" for ln in lines))
    f.close()
    CONFIG.calllog_file = f.name
    return f.name


def _rec(to: str = "+819011112222", status: str = "dialed", ts: str = "2026-09-29T02:00:00+00:00") -> bytes:
    return json.dumps({"ts": ts, "direction": "outbound", "to": to, "status": status}).encode()


# --- 壊れた行を飛ばす ------------------------------------------------------------


def test_non_object_json_lines_are_skipped():
    _log_with([_rec("+81901"), b"123", b'"x"', b"[]", b"null", _rec("+81902")])
    assert [r["to"] for r in calllog._all()] == ["+81901", "+81902"]
    assert [r["to"] for r in calllog.recent()] == ["+81902", "+81901"]


def test_invalid_utf8_does_not_raise():
    _log_with([_rec("+81901"), b'{"to": "\xff\xfe broken', _rec("+81902")])
    assert [r["to"] for r in calllog._all()] == ["+81901", "+81902"]
    assert len(calllog.recent()) == 2


def test_summary_survives_broken_lines_and_non_string_fields():
    _log_with([
        _rec("+81901", "dialed"),
        b"[]",
        json.dumps({"to": ["+81903"], "status": {"x": 1}}).encode(),
        b"\xff\xfe",
        _rec("+81902", "blocked"),
    ])
    s = calllog.summary()
    assert s["total"] == 3  # 辞書として読めた行の数（status/to が壊れた行も 1 件と数える）
    assert s["by_status"] == {"dialed": 1, "blocked": 1}
    assert s["unique_numbers"] == 2


# --- 番号の表記ゆれ --------------------------------------------------------------


def test_unique_numbers_uses_dnc_normalization():
    records = [
        {"to": "+81 90-1234-5678", "status": "dialed"},
        {"to": "+819012345678", "status": "dialed"},
        {"to": "+81(90)1234-5678", "status": "blocked"},
        {"to": "+819099998888", "status": "dialed"},
    ]
    assert calllog.summary(records)["unique_numbers"] == 2


# --- 1日上限（rate_limit）が壊れた記録で止まらない ---------------------------------


def test_dialed_today_ignores_broken_lines():
    _log_with([
        _rec("+81901"),
        b"42",
        b"\xff broken",
        _rec("+81902", ts="2026-09-28T02:00:00+00:00"),  # 前日（JST）
        _rec("+81903", status="blocked"),
        _rec("+81904"),
    ])
    assert rate_limit.dialed_today(now=NOW) == 2


def test_dialed_today_skips_non_dict_records_passed_in():
    records = [{"status": "dialed", "ts": "2026-09-29T02:00:00+00:00"}, "oops", None, 7]
    assert rate_limit.dialed_today(now=NOW, records=records) == 1


# --- 永続化（fly.toml） ----------------------------------------------------------


def test_fly_toml_keeps_calllog_on_the_persistent_volume():
    conf = tomllib.loads(FLY_TOML.read_text(encoding="utf-8"))
    mount = conf["mounts"]["destination"]
    path = conf["env"]["TAC_CALLLOG_FILE"]
    assert path == "/data/calls.jsonl"
    assert path.startswith(mount.rstrip("/") + "/")
    # DNC と同じボリュームに置く（どちらも監査・再勧誘防止の根拠）
    assert Path(path).parent == Path(conf["env"]["TAC_DNC_FILE"]).parent
