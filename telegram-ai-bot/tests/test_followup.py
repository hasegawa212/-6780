"""自動フォロー（分類・台帳）のテスト（TDD）。

設計図「自動フォロー｜情報連携・AI分類設計」/「訪問営業｜自動フォロー設計図」より、
壊さない追加モジュールとして実装する。既存の DNC（拒否）・発信時間帯・1日上限・
スマートリスト（queue）はそのまま再利用する。

検証する不変条件（設計のチェックリスト）:
- 同じ投稿・行を重複登録しない
- 別人・番号相違は発信対象にしない（要確認へ）
- 拒否（連絡停止）は他の記録より優先／拒否済みは再び対象にしない
- 古い・矛盾する記録は要確認
- フォローは合計2回まで（上限）
- 出典（元の記録）と最終更新を保存する
- 出典の文章を命令として実行しない（分類だけ。テキストは data 扱い）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from tac import dnc, followup, queue  # noqa: E402
from tac.config import CONFIG  # noqa: E402


@pytest.fixture()
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(CONFIG, "follow_file", str(tmp_path / "followup.json"))
    monkeypatch.setattr(CONFIG, "dnc_file", str(tmp_path / "dnc.txt"))
    monkeypatch.setattr(CONFIG, "queue_file", str(tmp_path / "queue.json"))
    yield


# ---------------- 分類エンジン ----------------


def test_classify_reschedule():
    cat, _ = followup.classify("訪問の日程を別日に変更したいとのご連絡がありました。")
    assert cat == "再調整希望"


def test_classify_absent():
    cat, _ = followup.classify("訪問時にご不在。応答がありませんでした。")
    assert cat == "不在"


def test_classify_stop_is_top_priority():
    # 「日程変更したい」等の前向き語があっても、拒否語があれば連絡停止が最優先
    cat, _ = followup.classify("日程変更の相談もありましたが、今後の連絡は希望しないとのこと。")
    assert cat == "連絡停止"


def test_classify_cancel_and_noshow_are_absent_callable():
    # キャンセル・バックれ・居留守は「不在（発信可）」＝フォロー対象
    for s in ("キャンセル", "バックれ", "ばっくれ", "当日バックれ", "居留守"):
        cat, _ = followup.classify(s)
        assert cat == "不在", f"{s} -> {cat}"
        assert cat in followup.CALLABLE


def test_classify_next_date_undecided_is_reschedule():
    for s in ("次回日時決めれず", "次回日時決められず"):
        cat, _ = followup.classify(s)
        assert cat == "再調整希望"


def test_classify_cancel_with_decline_still_stops():
    # キャンセルでも「今後連絡不要/拒否」があれば連絡停止が最優先（発信しない）
    cat, _ = followup.classify("キャンセル。今後の連絡は希望しないとのこと。")
    assert cat == "連絡停止"
    cat2, _ = followup.classify("当日バックれ、着信拒否された")
    assert cat2 == "連絡停止"


def test_ingest_backfills_missing_name_without_duplicating(tmp_path, monkeypatch):
    from tac.config import CONFIG
    monkeypatch.setattr(CONFIG, "follow_file", str(tmp_path / "f.json"))
    monkeypatch.setattr(CONFIG, "dnc_file", str(tmp_path / "d.txt"))
    # 1回目: 名前なしで取り込み
    rec = {"number": "09011112222", "record": "留守。応答なし", "status": "留守"}
    r1 = followup.ingest([rec])
    assert r1["added"] == 1
    e = followup._read()[0]
    assert e["name"] == ""
    # 2回目: 同じ番号・同じ記録＋名前あり → 重複追加せず名前を補完
    rec2 = dict(rec, name="山田太郎")
    r2 = followup.ingest([rec2])
    assert r2["added"] == 0
    assert r2.get("updated") == 1
    ents = followup._read()
    assert len(ents) == 1  # 重複していない
    assert ents[0]["name"] == "山田太郎"
    # 3回目: 既に名前あり → 上書きしない・重複もしない
    r3 = followup.ingest([dict(rec, name="別名")])
    assert r3.get("updated", 0) == 0
    assert followup._read()[0]["name"] == "山田太郎"


def test_classify_mismatch_is_youkakunin():
    cat, _ = followup.classify("別人の可能性。番号相違の記録あり。")
    assert cat == "要確認"


def test_classify_ambiguous_defaults_to_youkakunin():
    cat, _ = followup.classify("メモなし")
    assert cat == "要確認"


# ---------------- 取り込み（ingest）----------------


def test_ingest_stores_source_and_updated(_tmp):
    r = followup.ingest([{
        "name": "A", "number": "090-1111-2222", "assignee": "佐藤",
        "record": "日程変更の依頼", "source": {"type": "slack", "ref": "C09:168"},
        "updated_at": "2025-11-08T10:24",
    }])
    assert r["added"] == 1
    e = followup.load()[0]
    assert e["category"] == "再調整希望"
    assert e["source"]["type"] == "slack"
    assert e["updated_at"] == "2025-11-08T10:24"
    assert e["record"] == "日程変更の依頼"  # 元の記録を保存


def test_ingest_dedupes_same_source_row(_tmp):
    rec = {"name": "A", "number": "090-1111-2222",
           "record": "日程変更", "source": {"type": "slack", "ref": "C09:168"}}
    followup.ingest([rec])
    r2 = followup.ingest([dict(rec)])  # 同じ出典 ref → 重複登録しない
    assert r2["added"] == 0
    assert r2["skipped"] >= 1
    assert len(followup.load()) == 1


def test_ingest_declined_number_is_not_callable(_tmp):
    dnc.add("+819099990000")
    followup.ingest([{"name": "B", "number": "090-9999-0000",
                      "record": "日程調整したい",
                      "source": {"type": "slack", "ref": "x:1"}}])
    e = followup.load()[0]
    assert e["category"] == "連絡停止"
    assert followup.can_follow(e) is False  # 拒否済みは再び対象にしない


def test_mismatch_record_not_callable(_tmp):
    followup.ingest([{"name": "C", "number": "090-3333-4444",
                      "record": "番号相違の記録", "source": {"type": "sheet", "ref": "s:9"}}])
    e = followup.load()[0]
    assert e["category"] == "要確認"
    assert followup.can_follow(e) is False  # 別人・番号相違は発信しない


def test_follow_cap_two(_tmp):
    followup.ingest([{"name": "D", "number": "090-5555-6666",
                      "record": "不在だった", "source": {"type": "slack", "ref": "y:2"}}])
    e = followup.load()[0]
    assert followup.can_follow(e) is True
    followup.record_follow("090-5555-6666")
    followup.record_follow("090-5555-6666")
    e = followup.load()[0]
    assert e["follow_count"] == 2
    assert followup.can_follow(e) is False  # 合計2回まで


def test_correct_category(_tmp):
    followup.ingest([{"name": "E", "number": "090-7777-8888",
                      "record": "不在", "source": {"type": "slack", "ref": "z:3"}}])
    eid = followup.load()[0]["id"]
    followup.correct(eid, "再調整希望")
    assert followup.load()[0]["category"] == "再調整希望"


def test_promote_pushes_callable_to_queue(_tmp):
    followup.ingest([
        {"name": "F", "number": "090-1000-0001", "record": "日程変更希望",
         "source": {"type": "slack", "ref": "a:1"}},   # 再調整希望 → callable
        {"name": "G", "number": "090-1000-0002", "record": "番号相違",
         "source": {"type": "slack", "ref": "a:2"}},   # 要確認 → 対象外
    ])
    ids = [e["id"] for e in followup.load()]
    moved = followup.promote(ids)
    nums = [q["number"] for q in queue.load()]
    assert moved == 1
    assert "+819010000001" in nums
    assert "+819010000002" not in nums  # 要確認はキューに出さない


# ---------------- API ルート ----------------


def _client():
    import importlib
    try:
        server = importlib.import_module("tac.server")
    except Exception:  # noqa: BLE001 - flask 未導入ならskip
        return None
    return server.app.test_client()


def test_follow_api_requires_token(_tmp):
    client = _client()
    if client is None:
        return
    CONFIG.outbound_token = ""  # 未設定なら無効（503）
    try:
        r = client.get("/tac/follow")
        assert r.status_code == 503
    finally:
        CONFIG.outbound_token = ""


def test_follow_api_ingest_list_promote(_tmp):
    client = _client()
    if client is None:
        return
    CONFIG.outbound_token = "tok-follow"
    h = {"X-TAC-Token": "tok-follow"}
    try:
        r = client.post("/tac/follow/ingest", headers=h, json={"records": [
            {"name": "山田", "number": "090-1111-2222", "record": "日程変更の依頼",
             "source": {"type": "slack", "ref": "C09:1"}},
            {"name": "高橋", "number": "090-9000-0000", "record": "今後の連絡を希望しない",
             "source": {"type": "slack", "ref": "C09:2"}},
        ]})
        assert r.status_code == 200 and r.get_json()["added"] == 2

        j = client.get("/tac/follow", headers=h).get_json()
        assert j["count"] == 2
        assert j["counts"]["再調整希望"] == 1
        assert j["counts"]["連絡停止"] == 1

        yamada = [e for e in j["items"] if e["name"] == "山田"][0]
        mv = client.post("/tac/follow/promote", json={"ids": [yamada["id"]]}, headers=h)
        assert mv.get_json()["moved"] == 1

        cr = client.post("/tac/follow/correct",
                         json={"id": yamada["id"], "category": "不在"}, headers=h)
        assert cr.status_code == 200 and cr.get_json()["ok"] is True
    finally:
        CONFIG.outbound_token = ""
