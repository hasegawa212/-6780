"""DNC（Do Not Call / 発信禁止リスト）のテスト。

TDD: 実装(tac/dnc.py, outbound への組込み)より先にこの仕様を固定する。
発信禁止＝「断られた相手に再発信しない」を仕組みで担保するための最小単位。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import dnc, outbound  # noqa: E402
from tac.config import CONFIG  # noqa: E402


def _tmp_dnc():
    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    f.close()
    CONFIG.dnc_file = f.name
    return f.name


def test_normalize_strips_formatting():
    assert dnc.normalize("+81 70-6654-6780") == "+817066546780"
    # 国内表記は E.164 にそろえる（以前は 07066546780 のままで、E.164 の発信と照合できなかった）
    assert dnc.normalize(" (070) 6654 6780 ") == "+817066546780"
    assert dnc.normalize("") == ""


def test_add_contains_remove_roundtrip():
    _tmp_dnc()
    assert dnc.contains("+817066546780") is False
    assert dnc.add("+81 70-6654-6780") is True      # 整形して登録
    assert dnc.contains("+817066546780") is True     # 別表記でも一致
    assert dnc.add("+817066546780") is False         # 二重登録はしない（冪等）
    assert dnc.remove("+817066546780") is True
    assert dnc.contains("+817066546780") is False


def test_all_lists_entries():
    _tmp_dnc()
    dnc.add("+819011112222")
    dnc.add("+819033334444")
    assert set(dnc.all()) == {"+819011112222", "+819033334444"}


def test_missing_file_is_safe():
    CONFIG.dnc_file = "/nonexistent/dir/dnc.txt"
    assert dnc.contains("+810000000000") is False
    assert dnc.all() == []


def test_dnc_route_add_and_block(monkeypatch=None):
    """/tac/dnc で登録 → /tac/call がその番号をブロックする（flask 無い環境はskip）。"""
    import importlib

    try:
        server = importlib.import_module("tac.server")
    except Exception:  # noqa: BLE001 - flask 未導入環境ではスキップ
        return
    _tmp_dnc()
    CONFIG.outbound_token = "tok-dnc-test"
    CONFIG.agent_number = "+818094662479"
    client = server.app.test_client()
    # 認証なしは 401
    assert client.post("/tac/dnc", data={"number": "+819000000000"}).status_code == 401
    # 登録
    r = client.post("/tac/dnc", data={"number": "+819000000000", "token": "tok-dnc-test"})
    assert r.status_code == 200 and r.get_json()["changed"] is True
    # 一覧に出る
    g = client.get("/tac/dnc", query_string={"token": "tok-dnc-test"}).get_json()
    assert "+819000000000" in g["numbers"]
    # 発信は blocked
    r2 = client.post(
        "/tac/call",
        data={"to": "+819000000000", "token": "tok-dnc-test"},
    )
    assert r2.get_json().get("blocked") is True
    CONFIG.outbound_token = ""


def test_bridge_call_blocks_dnc_number():
    """DNC 登録済みの相手には発信しない（Twilioを呼ぶ前に拒否）。"""
    _tmp_dnc()
    old_agent = CONFIG.agent_number
    CONFIG.agent_number = "+818094662479"
    dnc.add("+817066546780")
    try:
        r = outbound.bridge_call("+817066546780")
        assert r["ok"] is False
        assert r.get("blocked") is True
        # 発信レッグは一切作られていない（Twilio未接続でも到達しない）
        assert "target_leg" not in r
    finally:
        CONFIG.agent_number = old_agent


def test_domestic_and_e164_forms_match():
    """表記ゆれで抑止をすり抜けない（090-… で登録 → +8190… の発信を止める、逆も同じ）。"""
    _tmp_dnc()
    dnc.add("090-1234-5678")
    assert dnc.contains("+819012345678") is True
    assert dnc.contains("０９０１２３４５６７８") is True  # 全角
    dnc.add("+81 70-1111-2222")
    assert dnc.contains("070-1111-2222") is True


def test_hand_edited_domestic_entry_still_blocks():
    """dnc.txt に手で国内表記を書いた行も、E.164 の発信を止める。"""
    path = _tmp_dnc()
    Path(path).write_text("# 手入力\n03-6899-5464\n", encoding="utf-8")
    assert dnc.contains("+81368995464") is True


def test_dnc_route_domestic_number_blocks_e164_call():
    """/tac/dnc に国内表記で登録しても /tac/call（E.164 に正規化される）を止める。"""
    import importlib

    try:
        server = importlib.import_module("tac.server")
    except Exception:  # noqa: BLE001 - flask 未導入環境ではスキップ
        return
    _tmp_dnc()
    CONFIG.outbound_token = "tok-dnc-jp"
    CONFIG.agent_number = "+818094662479"
    client = server.app.test_client()
    try:
        r = client.post("/tac/dnc", data={"number": "090-5555-6666", "token": "tok-dnc-jp"})
        assert r.status_code == 200
        r2 = client.post("/tac/call", data={"to": "09055556666", "token": "tok-dnc-jp"})
        assert r2.get_json().get("blocked") is True
    finally:
        CONFIG.outbound_token = ""
