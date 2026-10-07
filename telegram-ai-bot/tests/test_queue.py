"""スマートリスト（Smart Queue）のテスト（TDD）。

優先順リストをサーバーが返す。CSV/JSON ファイルから読み込み、名前・エリア・
スコアでの並び替え・検索に対応する。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac.config import CONFIG  # noqa: E402


def _tmp_queue(entries: list[dict] | None = None) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(entries or [], f, ensure_ascii=False)
    f.close()
    CONFIG.queue_file = f.name
    return f.name


# ---------------- ファイル読み書き ----------------

def test_load_empty_file():
    from tac import queue
    _tmp_queue([])
    assert queue.load() == []


def test_load_entries():
    from tac import queue
    entries = [
        {"number": "+819011112222", "name": "田中", "area": "茨城", "score": 85},
        {"number": "+819033334444", "name": "鈴木", "area": "栃木", "score": 70},
    ]
    _tmp_queue(entries)
    result = queue.load()
    assert len(result) == 2
    assert result[0]["name"] == "田中"


def test_load_missing_file():
    from tac import queue
    CONFIG.queue_file = "/nonexistent/queue.json"
    assert queue.load() == []


def test_add_entry():
    from tac import queue
    _tmp_queue([])
    queue.add(number="+819055556666", name="佐藤", area="埼玉", score=60)
    entries = queue.load()
    assert len(entries) == 1
    assert entries[0]["number"] == "+819055556666"
    assert entries[0]["name"] == "佐藤"


def test_add_bulk():
    from tac import queue
    _tmp_queue([])
    items = [
        {"number": "+819011111111", "name": "A"},
        {"number": "+819022222222", "name": "B"},
    ]
    queue.add_bulk(items)
    assert len(queue.load()) == 2


# ---------------- ソート ----------------

def test_sort_by_score_desc():
    from tac import queue
    _tmp_queue([
        {"number": "+1", "name": "低", "area": "", "score": 30},
        {"number": "+2", "name": "高", "area": "", "score": 90},
        {"number": "+3", "name": "中", "area": "", "score": 60},
    ])
    result = queue.load(sort="score")
    assert result[0]["score"] == 90
    assert result[1]["score"] == 60
    assert result[2]["score"] == 30


def test_sort_by_name():
    from tac import queue
    _tmp_queue([
        {"number": "+1", "name": "鈴木", "area": "", "score": 0},
        {"number": "+2", "name": "田中", "area": "", "score": 0},
        {"number": "+3", "name": "佐藤", "area": "", "score": 0},
    ])
    result = queue.load(sort="name")
    names = [r["name"] for r in result]
    assert names == sorted(names)


def test_sort_by_area():
    from tac import queue
    _tmp_queue([
        {"number": "+1", "name": "", "area": "栃木", "score": 0},
        {"number": "+2", "name": "", "area": "茨城", "score": 0},
        {"number": "+3", "name": "", "area": "埼玉", "score": 0},
    ])
    result = queue.load(sort="area")
    areas = [r["area"] for r in result]
    assert areas == sorted(areas)


# ---------------- 検索 ----------------

def test_search_by_name():
    from tac import queue
    _tmp_queue([
        {"number": "+1", "name": "田中太郎", "area": "茨城", "score": 85},
        {"number": "+2", "name": "鈴木一郎", "area": "栃木", "score": 70},
    ])
    result = queue.load(q="田中")
    assert len(result) == 1
    assert result[0]["name"] == "田中太郎"


def test_search_by_area():
    from tac import queue
    _tmp_queue([
        {"number": "+1", "name": "田中", "area": "茨城", "score": 85},
        {"number": "+2", "name": "鈴木", "area": "栃木", "score": 70},
    ])
    result = queue.load(q="栃木")
    assert len(result) == 1
    assert result[0]["area"] == "栃木"


def test_search_by_number():
    from tac import queue
    _tmp_queue([
        {"number": "+819011112222", "name": "田中", "area": "", "score": 0},
        {"number": "+819033334444", "name": "鈴木", "area": "", "score": 0},
    ])
    result = queue.load(q="3333")
    assert len(result) == 1
    assert result[0]["name"] == "鈴木"


def test_search_no_match():
    from tac import queue
    _tmp_queue([{"number": "+1", "name": "田中", "area": "", "score": 0}])
    assert queue.load(q="存在しない") == []


# ---------------- API ルート ----------------

def test_queue_route_requires_token():
    import importlib
    try:
        server = importlib.import_module("tac.server")
    except Exception:
        return
    _tmp_queue([])
    CONFIG.outbound_token = "tok-queue-test"
    client = server.app.test_client()
    try:
        assert client.get("/tac/calls/queue").status_code == 401
        r = client.get("/tac/calls/queue", query_string={"token": "tok-queue-test"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert isinstance(body["queue"], list)
    finally:
        CONFIG.outbound_token = ""


def test_queue_route_with_sort_and_search():
    import importlib
    try:
        server = importlib.import_module("tac.server")
    except Exception:
        return
    _tmp_queue([
        {"number": "+1", "name": "田中", "area": "茨城", "score": 85},
        {"number": "+2", "name": "鈴木", "area": "栃木", "score": 70},
    ])
    CONFIG.outbound_token = "tok-queue-test"
    client = server.app.test_client()
    try:
        r = client.get("/tac/calls/queue", query_string={
            "token": "tok-queue-test", "sort": "score",
        })
        body = r.get_json()
        assert body["queue"][0]["score"] == 85

        r = client.get("/tac/calls/queue", query_string={
            "token": "tok-queue-test", "q": "鈴木",
        })
        body = r.get_json()
        assert len(body["queue"]) == 1
    finally:
        CONFIG.outbound_token = ""


def test_queue_post_adds_entry():
    import importlib
    try:
        server = importlib.import_module("tac.server")
    except Exception:
        return
    _tmp_queue([])
    CONFIG.outbound_token = "tok-queue-test"
    client = server.app.test_client()
    try:
        r = client.post("/tac/calls/queue",
                        data=json.dumps({"number": "+819012345678", "name": "新規"}),
                        content_type="application/json",
                        headers={"X-TAC-Token": "tok-queue-test"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        # 追加されたことを確認
        from tac import queue
        entries = queue.load()
        assert any(e["number"] == "+819012345678" for e in entries)
    finally:
        CONFIG.outbound_token = ""


# ---------------- まるごと入れ替え（replace） ----------------

def test_replace_overwrites_all():
    from tac import queue
    _tmp_queue([
        {"number": "08011112222", "name": "旧A", "score": 10},
        {"number": "08033334444", "name": "旧B", "score": 20},
    ])
    n = queue.replace([
        {"number": "09099998888", "name": "新X", "area": "茨城", "score": 90},
    ])
    assert n == 1
    entries = queue.load()
    assert len(entries) == 1
    assert entries[0]["name"] == "新X"
    assert entries[0]["number"] == "09099998888"


def test_replace_with_empty_clears():
    from tac import queue
    _tmp_queue([{"number": "08011112222", "name": "旧A", "score": 10}])
    n = queue.replace([])
    assert n == 0
    assert queue.load() == []


def test_replace_normalizes_fields():
    from tac import queue
    _tmp_queue([])
    queue.replace([{"number": "09011112222"}])
    e = queue.load()[0]
    assert e["name"] == "" and e["area"] == "" and e["score"] == 0 and e["note"] == ""


# ============================================================
# フォルダ（購入者名簿 等、リストの分離）
# ============================================================

def test_add_with_folder():
    """folder付きで追加するとfolderフィールドが保存される。"""
    from tac import queue
    _tmp_queue([])
    queue.add(number="09011112222", name="田中", folder="購入者名簿")
    entries = queue.load()
    assert len(entries) == 1
    assert entries[0]["folder"] == "購入者名簿"


def test_load_filter_by_folder():
    """folder指定でロードすると該当フォルダだけ返る。"""
    from tac import queue
    _tmp_queue([
        {"number": "+1", "name": "既存A", "area": "", "score": 0, "note": ""},
        {"number": "+2", "name": "購入者B", "area": "", "score": 0, "note": "", "folder": "購入者名簿"},
        {"number": "+3", "name": "購入者C", "area": "", "score": 0, "note": "", "folder": "購入者名簿"},
    ])
    result = queue.load(folder="購入者名簿")
    assert len(result) == 2
    assert all(e["folder"] == "購入者名簿" for e in result)


def test_load_no_folder_returns_all():
    """folder未指定なら全件返る（後方互換）。"""
    from tac import queue
    _tmp_queue([
        {"number": "+1", "name": "既存A", "area": "", "score": 0, "note": ""},
        {"number": "+2", "name": "購入者B", "area": "", "score": 0, "note": "", "folder": "購入者名簿"},
    ])
    result = queue.load()
    assert len(result) == 2


def test_load_default_folder():
    """folder=""(空文字)でロードするとフォルダなしエントリだけ返る。"""
    from tac import queue
    _tmp_queue([
        {"number": "+1", "name": "既存A", "area": "", "score": 0, "note": ""},
        {"number": "+2", "name": "購入者B", "area": "", "score": 0, "note": "", "folder": "購入者名簿"},
    ])
    result = queue.load(folder="")
    assert len(result) == 1
    assert result[0]["name"] == "既存A"


def test_add_bulk_with_folder():
    """add_bulkでもfolderフィールドが保存される。"""
    from tac import queue
    _tmp_queue([])
    items = [
        {"number": "09011111111", "name": "A", "folder": "購入者名簿"},
        {"number": "09022222222", "name": "B", "folder": "購入者名簿"},
    ]
    queue.add_bulk(items)
    entries = queue.load(folder="購入者名簿")
    assert len(entries) == 2


def test_replace_with_folder():
    """replaceでもfolderが保持される。"""
    from tac import queue
    _tmp_queue([{"number": "08011112222", "name": "旧A", "score": 10, "folder": "購入者名簿"}])
    queue.replace([
        {"number": "09099998888", "name": "新X", "folder": "購入者名簿"},
    ])
    entries = queue.load(folder="購入者名簿")
    assert len(entries) == 1
    assert entries[0]["folder"] == "購入者名簿"


def test_folders_list():
    """folders()がフォルダ名と件数の一覧を返す。"""
    from tac import queue
    _tmp_queue([
        {"number": "+1", "name": "A", "area": "", "score": 0, "note": ""},
        {"number": "+2", "name": "B", "area": "", "score": 0, "note": "", "folder": "購入者名簿"},
        {"number": "+3", "name": "C", "area": "", "score": 0, "note": "", "folder": "購入者名簿"},
        {"number": "+4", "name": "D", "area": "", "score": 0, "note": "", "folder": "バックれ名簿"},
    ])
    result = queue.folders()
    assert isinstance(result, list)
    names = {f["name"]: f["count"] for f in result}
    assert names.get("購入者名簿") == 2
    assert names.get("バックれ名簿") == 1
    # フォルダなしも含む
    assert any(f["name"] == "" for f in result)


def test_folder_with_sort_and_search():
    """folder + sort + search の組み合わせ。"""
    from tac import queue
    _tmp_queue([
        {"number": "+1", "name": "田中", "area": "茨城", "score": 30, "folder": "購入者名簿"},
        {"number": "+2", "name": "鈴木", "area": "栃木", "score": 90, "folder": "購入者名簿"},
        {"number": "+3", "name": "佐藤", "area": "埼玉", "score": 60, "folder": "その他"},
    ])
    result = queue.load(folder="購入者名簿", sort="score")
    assert len(result) == 2
    assert result[0]["score"] == 90

    result = queue.load(folder="購入者名簿", q="田中")
    assert len(result) == 1


# ============================================================
# フォルダ API ルート
# ============================================================

def test_queue_api_folder_filter():
    """APIのfolderクエリパラメータでフィルタできる。"""
    import importlib
    try:
        server = importlib.import_module("tac.server")
    except Exception:
        return
    _tmp_queue([
        {"number": "+1", "name": "既存", "area": "", "score": 0, "note": ""},
        {"number": "+2", "name": "購入者", "area": "", "score": 0, "note": "", "folder": "購入者名簿"},
    ])
    CONFIG.outbound_token = "tok-folder-test"
    client = server.app.test_client()
    try:
        r = client.get("/tac/calls/queue", query_string={
            "token": "tok-folder-test", "folder": "購入者名簿",
        })
        body = r.get_json()
        assert body["ok"] is True
        assert body["count"] == 1
        assert body["queue"][0]["folder"] == "購入者名簿"
    finally:
        CONFIG.outbound_token = ""


def test_queue_api_folders_endpoint():
    """GET /tac/calls/queue/folders がフォルダ一覧を返す。"""
    import importlib
    try:
        server = importlib.import_module("tac.server")
    except Exception:
        return
    _tmp_queue([
        {"number": "+1", "name": "A", "area": "", "score": 0, "note": ""},
        {"number": "+2", "name": "B", "area": "", "score": 0, "note": "", "folder": "購入者名簿"},
        {"number": "+3", "name": "C", "area": "", "score": 0, "note": "", "folder": "購入者名簿"},
    ])
    CONFIG.outbound_token = "tok-folder-test"
    client = server.app.test_client()
    try:
        r = client.get("/tac/calls/queue/folders", query_string={
            "token": "tok-folder-test",
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        folders = body["folders"]
        names = {f["name"]: f["count"] for f in folders}
        assert names.get("購入者名簿") == 2
    finally:
        CONFIG.outbound_token = ""


def test_queue_post_with_folder():
    """POST /tac/calls/queue でfolderを指定して追加。"""
    import importlib
    try:
        server = importlib.import_module("tac.server")
    except Exception:
        return
    _tmp_queue([])
    CONFIG.outbound_token = "tok-folder-test"
    client = server.app.test_client()
    try:
        r = client.post("/tac/calls/queue",
                        data=json.dumps({"number": "+819012345678", "name": "新規", "folder": "購入者名簿"}),
                        content_type="application/json",
                        headers={"X-TAC-Token": "tok-folder-test"})
        assert r.status_code == 200
        from tac import queue
        entries = queue.load(folder="購入者名簿")
        assert len(entries) == 1
        assert entries[0]["folder"] == "購入者名簿"
    finally:
        CONFIG.outbound_token = ""


def test_queue_bulk_post_with_folder():
    """POST /tac/calls/queue で一括追加時にfolderが保持される。"""
    import importlib
    try:
        server = importlib.import_module("tac.server")
    except Exception:
        return
    _tmp_queue([])
    CONFIG.outbound_token = "tok-folder-test"
    client = server.app.test_client()
    try:
        payload = [
            {"number": "09011111111", "name": "川口 竜平", "folder": "購入者名簿"},
            {"number": "09022222222", "name": "小松 龍生", "folder": "購入者名簿"},
        ]
        r = client.post("/tac/calls/queue",
                        data=json.dumps(payload),
                        content_type="application/json",
                        headers={"X-TAC-Token": "tok-folder-test"})
        assert r.status_code == 200
        from tac import queue
        entries = queue.load(folder="購入者名簿")
        assert len(entries) == 2
    finally:
        CONFIG.outbound_token = ""


# ============================================================
# 購入者名簿109名インポート E2E
# ============================================================

def test_import_109_customers_e2e():
    """#28チャンネルの109名を購入者名簿フォルダへ一括投入。既存を壊さない。"""
    import importlib
    try:
        server = importlib.import_module("tac.server")
    except Exception:
        return
    _tmp_queue([
        {"number": "+819099999999", "name": "既存太郎", "area": "東京", "score": 50, "note": ""},
    ])
    CONFIG.outbound_token = "tok-import-test"
    client = server.app.test_client()
    try:
        # 109名分の模擬データ
        customers = [
            {"number": f"+8190{i:08d}", "name": f"顧客{i}", "folder": "購入者名簿", "note": f"No.{100+i}"}
            for i in range(109)
        ]
        r = client.post("/tac/calls/queue",
                        data=json.dumps(customers),
                        content_type="application/json",
                        headers={"X-TAC-Token": "tok-import-test"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["added"] == 109

        # 購入者名簿フォルダに109件
        from tac import queue
        folder_entries = queue.load(folder="購入者名簿")
        assert len(folder_entries) == 109

        # 既存の一般エントリは壊れていない
        all_entries = queue.load()
        assert len(all_entries) == 110  # 1 + 109

        # フォルダ一覧に両方ある
        f = queue.folders()
        names = {x["name"]: x["count"] for x in f}
        assert names.get("購入者名簿") == 109
        assert names.get("") == 1
    finally:
        CONFIG.outbound_token = ""
