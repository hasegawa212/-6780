"""成約確度エンジン tac/winscore の TDD（当社の勝ち筋ベース）。

さくら発信リストを「当社が実際に決済した勝ちパターン」で採点し、成約確度の高い
順に並べ替える。採点は全て説明可能（reasons 付き）で、根拠は社内の実データ
（closed-deals-winning-points の実決済エリア＋自動フォロー分類の温度）。
架空の根拠は作らない。拒否(連絡停止)は常に除外。

グレード帯は社内スコアリング（S:80+ / A:65-79 / B:50-64 / C:35-49 / D:<35）に揃える。
実行: cd telegram-ai-bot && python3 -m pytest tests/test_winscore.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import winscore  # noqa: E402


def _lead(**kw):
    base = {"name": "テスト", "number": "+819000000000", "area": "",
            "category": "不在", "basis": "", "record": "", "follow_count": 0}
    base.update(kw)
    return base


# ---- 高確度：再調整希望 × 実決済エリア × 前向き × 未架電 ----
def test_hot_lead_scores_high():
    lead = _lead(category="再調整希望", area="栃木県宇都宮市西川田町",
                 record="予算OKで前向き、内見したいとのこと", follow_count=0)
    r = winscore.score_lead(lead)
    assert r["score"] >= 80
    assert r["grade"] == "S"
    # 根拠が説明可能に並ぶ
    assert any("エリア" in x["factor"] for x in r["reasons"])


# ---- 拒否は常に除外（エリア一致でも発信対象にしない） ----
def test_stop_is_excluded():
    lead = _lead(category="連絡停止", area="宇都宮市", record="前向き")
    r = winscore.score_lead(lead)
    assert r["score"] == 0
    assert r["grade"] == "除外"


# ---- 実績外エリアはエリア加点なし ----
def test_area_outside_no_bonus():
    inside = winscore.score_lead(_lead(category="不在", area="宇都宮市"))
    outside = winscore.score_lead(_lead(category="不在", area="大阪市"))
    assert inside["score"] > outside["score"]


# ---- しつこさペナルティ（何度も不在） ----
def test_many_follows_penalized():
    fresh = winscore.score_lead(_lead(category="不在", follow_count=0))
    stale = winscore.score_lead(_lead(category="不在", follow_count=4))
    assert stale["score"] < fresh["score"]


# ---- 本文/エリアどちらにも実績地名が無ければ加点0、本文にあれば拾う ----
def test_area_from_record_text():
    lead = _lead(category="不在", area="", record="ひたちなか市在住のお客様")
    r = winscore.score_lead(lead)
    assert any("エリア" in x["factor"] and x["points"] > 0 for x in r["reasons"])


# ---- 過去に話せた履歴があれば到達性加点 ----
def test_reachability_bonus_from_history():
    lead = _lead(category="不在")
    hist = [{"status": "disposition", "disposition": "検討中"}]
    with_hist = winscore.score_lead(lead, history=hist)
    without = winscore.score_lead(lead, history=[])
    assert with_hist["score"] > without["score"]


# ---- グレード帯が社内基準どおり ----
def test_grade_bands():
    assert winscore.grade_of(80) == "S"
    assert winscore.grade_of(79) == "A"
    assert winscore.grade_of(65) == "A"
    assert winscore.grade_of(64) == "B"
    assert winscore.grade_of(50) == "B"
    assert winscore.grade_of(49) == "C"
    assert winscore.grade_of(35) == "C"
    assert winscore.grade_of(34) == "D"


# ---- prioritize：確度が高い順、拒否は末尾/除外、_win 付与 ----
def test_prioritize_orders_desc_and_excludes_stop():
    leads = [
        _lead(name="低", category="要確認", area="大阪"),
        _lead(name="高", category="再調整希望", area="宇都宮市", record="前向き 予算"),
        _lead(name="拒否", category="連絡停止", area="宇都宮市"),
        _lead(name="中", category="不在", area="水戸市"),
    ]
    ranked = winscore.prioritize(leads)
    names = [x["name"] for x in ranked]
    # 拒否は対象外（末尾 or 除外）、先頭は「高」
    assert names[0] == "高"
    assert names.index("中") < names.index("低")
    for x in ranked:
        assert "_win" in x and "score" in x["_win"] and "grade" in x["_win"]


# ---- スコアは 0..100 にクランプ ----
def test_score_clamped():
    lead = _lead(category="再調整希望", area="宇都宮市西川田町雀宮",
                 record="前向き 興味 予算 検討 内見 ローン 審査 買いたい 見たい",
                 follow_count=0)
    r = winscore.score_lead(lead, history=[{"status": "disposition"}])
    assert 0 <= r["score"] <= 100


# ---- /tac/winscore ルート（番号は出さない） ----
def test_winscore_route(monkeypatch):
    import importlib
    try:
        server = importlib.import_module("tac.server")
        followup = importlib.import_module("tac.followup")
    except Exception:  # noqa: BLE001
        import pytest
        pytest.skip("flask 未導入")
    from tac.config import CONFIG
    monkeypatch.setattr(followup, "load", lambda category="": [
        _lead(name="高", category="再調整希望", area="宇都宮市", record="前向き 予算"),
        _lead(name="拒否", category="連絡停止", area="宇都宮市"),
    ])
    prev = CONFIG.outbound_token
    CONFIG.outbound_token = "tok-win-test"
    try:
        client = server.app.test_client()
        assert client.get("/tac/winscore").status_code == 401
        r = client.get("/tac/winscore", query_string={"token": "tok-win-test"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["count"] == 1              # 拒否は除外
        assert body["ranked"][0]["name"] == "高"
        assert "win" in body["ranked"][0]
        import json
        assert "+819000000000" not in json.dumps(body, ensure_ascii=False)  # 番号は出さない
    finally:
        CONFIG.outbound_token = prev
