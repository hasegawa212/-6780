"""登記事項証明書パーサ（touki_parser）の最小テスト（pytest 不要・素の assert）.

実行::
    cd bc-pipeline && python tests/test_touki_parser.py

テキストは登記情報提供サービスの「全部事項（箱文字）」書式を模した **架空データ**。
個人情報は含めない。実PDFの読み取り確認は別途、運用環境（APIキー有）で行う。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from touki_parser import looks_like_touki, parse_touki_text  # noqa: E402

# 建物の全部事項（架空）。床面積は 1階50.00 + 2階45.50 = 延床95.5㎡。
TATEMONO = """\
２０２０／０１／０１　１０：００　現在の情報です。
　　　┏━━━━━━━━━━━━━━━━━┯━━┯━━━━━━━━━┯━━━━━┯━━━━━━━━┓
　　　┃　表　　題　　部　　（主である建物の表示）│調製│令和２年１月１日　│不動産番号│１２３４５６７８９０１２３┃
　　　┠─────┬───────────┴──┴──────┴─────┴────────┨
　　　┃所在図番号│　　　　　　　　　　　　　　　　　　　　　　　　　　　　┃
　　　┠─────┼──────────────────────┬──────────┨
　　　┃所　　　在│港区芝公園四丁目　１番地２　　　　　　　　　│　　　　　　┃
　　　┠─────┼──────────────────────┼──────────┨
　　　┃家屋番号　│１番２　　　　　　　　　　　　　　　　　　　│　　　　　　┃
　　　┠─────┼──────┬───────────┼──────────┨
　　　┃①　種　類│②　構　造│③　床　面　積　㎡　│原因及びその日付〔登記の日付〕┃
　　　┠─────┼──────┼───────────┼──────────┨
　　　┃居宅　　　│木造スレート葺２階建│　　１階　　５０：００│令和２年３月１０日新築┃
　　　┃　　　　　│　　　　　　│　　２階　　４５：５０│〔令和２年３月２０日〕┃
　　　┗━━━━━┷━━━━━━┷━━━━━━━━━━━┷━━━━━━━━━━┛
　　　┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
　　　┃　権　　利　　部　（　甲　区　）　　（所　有　権　に　関　す　る　事　項）┃
　　　┠─────┬──────────┬─────────┬──────────┨
　　　┃順位番号　│登　記　の　目　的│受付年月日・受付番号│権利者その他の事項┃
　　　┠─────┼──────────┼─────────┼──────────┨
　　　┃１　　　　│所有権保存　　　　│令和２年４月１日　│所有者　港区芝公園四丁目１番２号┃
　　　┃　　　　　│　　　　　　　　　│第１２３４号　　　│　山　田　太　郎　　　┃
　　　┗━━━━━┷━━━━━━━━━━┷━━━━━━━━━┷━━━━━━━━━━┛
"""

# 土地の全部事項（架空）。地積 120.50㎡。
TOCHI = """\
２０２０／０１／０１　１０：００　現在の情報です。
　　　┏━━━━━━━━━━━━━━━━┯━━┯━━━━━━━┯━━━━━┯━━━━━━━━┓
　　　┃　表　　題　　部　　（土地の表示）│調製│令和２年１月１日│不動産番号│９８７６５４３２１０９８７┃
　　　┠─────┬──────────┴──┴─────┴─────┴────────┨
　　　┃所　　　在│港区芝公園四丁目　　　　　　　　　　　　　　│　　　　　　┃
　　　┠─────┼──────┬───────────┬──────────┨
　　　┃①　地　番│②　地　目│③　地　積　　　　㎡│原因及びその日付〔登記の日付〕┃
　　　┠─────┼──────┼───────────┼──────────┨
　　　┃１番２　　│宅地　　　│　　　　　１２０：５０│令和元年１２月１日地目変更┃
　　　┗━━━━━┷━━━━━━┷━━━━━━━━━━━┷━━━━━━━━━━┛
"""


def _check(cond, label):
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    print(f"  ok: {label}")


def test_tatemono():
    print("[建物]")
    _check(looks_like_touki(TATEMONO), "looks_like_touki=True")
    r = parse_touki_text(TATEMONO)
    f = r["fill"]
    _check(r["kind"] == "建物", f"kind==建物 (got {r['kind']})")
    _check(f.get("mp_shozai") == "港区芝公園四丁目1番地2", f"所在 (got {f.get('mp_shozai')})")
    _check(f.get("mp_kaoku") == "1番2", f"家屋番号 (got {f.get('mp_kaoku')})")
    _check(f.get("mp_shurui") == "居宅", f"種類 (got {f.get('mp_shurui')})")
    _check(f.get("mp_kozo") == "木造スレート葺2階建", f"構造 (got {f.get('mp_kozo')})")
    _check(f.get("mp_menseki") == "95.5㎡", f"延床面積 (got {f.get('mp_menseki')})")
    _check(f.get("mp_chiku") == "令和2年3月", f"築年月 (got {f.get('mp_chiku')})")
    _check(f.get("mp_touki") == "山田太郎", f"所有者 (got {f.get('mp_touki')})")


def test_tochi():
    print("[土地]")
    r = parse_touki_text(TOCHI)
    f = r["fill"]
    _check(r["kind"] == "土地", f"kind==土地 (got {r['kind']})")
    _check(f.get("mp_shozai") == "港区芝公園四丁目", f"所在 (got {f.get('mp_shozai')})")
    _check(f.get("mp_chimoku") == "宅地", f"地目 (got {f.get('mp_chimoku')})")
    _check(f.get("mp_chiseki") == "120.50㎡", f"地積 (got {f.get('mp_chiseki')})")


def test_not_touki():
    print("[非登記]")
    r = parse_touki_text("これはただのメモです。登記ではありません。")
    _check(not looks_like_touki("ただの文章"), "looks_like_touki=False")
    _check(r["fill"] == {}, "fill空")
    _check(r["notes"], "notesあり")


if __name__ == "__main__":
    test_tatemono()
    test_tochi()
    test_not_touki()
    print("\nAll touki_parser tests passed ✓")
