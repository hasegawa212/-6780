"""自動フォローの発信順＝成約確度エンジン(winscore)で駆動することの TDD。

select_next / run_batch が「カテゴリだけ」でなく当社の勝ち筋スコア（エリア・
前向き・鮮度…）順に次の1件を選ぶ＝連続発信が“勝てる客”から掛かることを固定する。
既存の優先度（高カテゴリ優先・拒否除外）も壊さない。

実行: cd telegram-ai-bot && python3 -m pytest tests/test_autofollow_winscore.py -q
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import autofollow as af  # noqa: E402

NOON_JST = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)  # JST 13:00（発信可能帯）


def _entry(**over):
    e = {
        "id": "e", "number": "+819011110000", "name": "客",
        "category": "不在", "consent": "同意",
        "area": "", "record": "", "follow_count": 0, "last_follow_at": "",
    }
    e.update(over)
    return e


# 同カテゴリなら「当社実決済エリア」の客が先に掛かる
def test_area_breaks_tie_within_same_category():
    osaka = _entry(id="osaka", number="+819000000001", category="不在", area="大阪市")
    utsunomiya = _entry(id="utsu", number="+819000000002", category="不在", area="宇都宮市")
    chosen, dec = af.select_next([osaka, utsunomiya], now=NOON_JST, enabled=True)
    assert chosen["id"] == "utsu"
    assert dec.place is True


# 同カテゴリ・同エリアなら「前向きワード」の客が先
def test_positive_signal_breaks_tie():
    plain = _entry(id="plain", number="+819000000001", category="不在", area="水戸市")
    hot = _entry(id="hot", number="+819000000002", category="不在", area="水戸市",
                 record="予算OKで前向き、内見したい")
    chosen, _ = af.select_next([plain, hot], now=NOON_JST, enabled=True)
    assert chosen["id"] == "hot"


# 既存の優先度（高カテゴリ優先）は維持（エリア無しなら 再調整希望 > 不在）
def test_high_category_still_preferred_without_area():
    low = _entry(id="low", number="+819000000001", category="不在")
    high = _entry(id="high", number="+819000000002", category="再調整希望")
    chosen, _ = af.select_next([low, high], now=NOON_JST, enabled=True)
    assert chosen["id"] == "high"


# 拒否は選ばれない（除外は維持）
def test_stop_never_selected():
    stop = _entry(id="stop", number="+819000000001", category="不在",
                  area="宇都宮市", consent="拒否")
    ok = _entry(id="ok", number="+819000000002", category="不在", area="大阪市")
    chosen, _ = af.select_next([stop, ok], now=NOON_JST, enabled=True)
    assert chosen["id"] == "ok"


# run_batch も確度順（勝ち筋の高い順）に掛ける
def test_run_batch_dials_in_winscore_order():
    dialed: list[str] = []
    entries = [
        _entry(id="c", number="+819000000001", category="不在", area="大阪市"),
        _entry(id="a", number="+819000000002", category="再調整希望", area="宇都宮市",
               record="前向き 予算"),
        _entry(id="b", number="+819000000003", category="不在", area="水戸市"),
    ]
    res = af.run_batch(
        now=NOON_JST, entries=entries, enabled=True,
        placer=lambda e: dialed.append(e["id"]) or {"ok": True},
        max_calls=3,
    )
    assert res["placed"] == 3
    assert dialed[0] == "a"            # 最も勝ち筋が高い
    assert dialed.index("b") < dialed.index("c")  # 実績エリア水戸 > 実績外大阪
