"""pytest 共通初期化。

Webhook 冪等性ガード（tac/idempotency.py）は処理済み CallSid を JSON ファイルに
永続する。既定パスは実ファイル tac/idempotency.json のため、テスト間・実行間で
記録が残り「処理済み」と誤判定→AMD/StatusCallback 系テストが落ちる相互汚染が起きる。

各テストごとに idempotency_file を一時パスへ隔離し、空状態から始める。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tac.config import CONFIG  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_idempotency():
    fd, path = tempfile.mkstemp(suffix=".json", prefix="idem_test_")
    os.close(fd)
    os.remove(path)  # 空（未作成）状態から開始
    prev = CONFIG.idempotency_file
    CONFIG.idempotency_file = path
    try:
        from tac import idempotency
        idempotency.reset()
        yield
    finally:
        CONFIG.idempotency_file = prev
        try:
            os.remove(path)
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _isolate_lifepartner_state(tmp_path):
    """CRM（SQLite）と緊急停止スイッチの状態を、テストごとに一時パスへ隔離する（実ファイルに書かない）。"""
    prev = (CONFIG.lp_db_file, CONFIG.kill_switch_file)
    CONFIG.lp_db_file = str(tmp_path / "lifepartner.db")
    CONFIG.kill_switch_file = str(tmp_path / "kill_switch.json")
    try:
        yield
    finally:
        CONFIG.lp_db_file, CONFIG.kill_switch_file = prev
