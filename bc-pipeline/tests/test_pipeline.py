"""BC重説パイプラインの最小テスト（pytest 不要・素の assert）.

実行::
    cd bc-pipeline && python tests/test_pipeline.py

AB 側データは実物の重要事項説明書（フレクション長岡 503 号室・区分所有）の
物件属性に基づく（個人情報は含めない）。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openpyxl import load_workbook  # noqa: E402

import juyojiko_excel  # noqa: E402
from bc_schema import normalize_yoto, resolve_bukken  # noqa: E402
from bc_transform import transform_ab_to_bc  # noqa: E402
from juyojiko_schema import (  # noqa: E402
    FudosanHyoji,
    HoreiSeigen,
    Juyojiko,
    KanriHiyou,
    Party,
    TatemonoHyoji,
    TorihikiJoken,
)

# 実物 AB 重説（区分）の物件属性（page3-12）
AB = Juyojiko(
    bukken_type="区分",
    torihiki_taiyo="売買 ・ 媒介",
    urinushi=Party(name="有限会社ネットプラン", address="岐阜県多治見市太平町一丁目47番地の2"),
    kainushi=Party(name="株式会社Martial Arts"),
    fudosan=FudosanHyoji(
        bukken_type="区分",
        jukyo_hyoji="新潟県長岡市曲新町551-1",
        ittou_shozai="長岡市曲新町字横田 551番地1",
        ittou_kozo="鉄筋コンクリート造陸屋根5階建",
        senyuu=TatemonoHyoji(kaoku_bango="曲新町 551番1の503", meisho="503号",
                             shurui="居宅", yukamenseki="14.95㎡"),
    ),
    horei=HoreiSeigen(
        toshikeikaku_kuiki="都市計画区域内",
        kuiki_kubun="市街化区域",
        yoto="第1種住居地域",
        nijuni_jo=True,
        kenpei=60,
        yoseki=200,
    ),
    kanri=KanriHiyou(kanrihi_getsugaku=5000, shuzen_getsugaku=3000,
                     kanri_kumiai="フレクション長岡管理組合", kanri_keitai="全部委託管理",
                     kanri_itakusaki="日本ハウズイング株式会社"),
    joken=TorihikiJoken(baibai_daikin=1_500_000),
    tokuyaku=["本物件は賃借権の負担付きで売買。買主は貸主の地位を承継する。"],
)

# 案件マスタ（BC 側）
DEAL = {
    "buyer_C": "東洋建設ホーム株式会社",
    "buyer_C_address": "東京都〇〇区〇〇1-1-1",
    "bc_baibai_daikin": 27_800_000,
    "bc_tetsuke": 1_000_000,
}


def _flat(xlsx: bytes) -> list[object]:
    ws = load_workbook(io.BytesIO(xlsx)).active
    return [c.value for row in ws.iter_rows() for c in row if c.value is not None]


def test_resolve_bukken() -> None:
    assert resolve_bukken("マンション") == "区分"
    assert resolve_bukken("土地建物") == "戸建"


def test_normalize_yoto() -> None:
    assert normalize_yoto("第1種住居") == "第1種住居地域"
    assert normalize_yoto("指定なし") == "用途地域の指定なし"


def test_transform_swaps_parties_and_price() -> None:
    bc = transform_ab_to_bc(AB, DEAL)
    # 売主 A→B、買主 B→C
    assert bc.urinushi.name == "株式会社Martial Arts"
    assert bc.kainushi.name == "東洋建設ホーム株式会社"
    # 代金は BC 価格
    assert bc.joken.baibai_daikin == 27_800_000
    assert bc.joken.tetsuke == 1_000_000
    # AB は不変（元データを壊さない）
    assert AB.urinushi.name == "有限会社ネットプラン"
    assert AB.joken.baibai_daikin == 1_500_000


def test_transform_carries_property_facts() -> None:
    bc = transform_ab_to_bc(AB, DEAL)
    # 物件事実はそのまま引き継ぐ
    assert bc.horei.kuiki_kubun == "市街化区域"
    assert bc.horei.yoto == "第1種住居地域"
    assert bc.horei.kenpei == 60 and bc.horei.yoseki == 200
    assert bc.kanri.kanrihi_getsugaku == 5000
    assert bc.fudosan.ittou_shozai == "長岡市曲新町字横田 551番地1"
    # 三為注記が付く
    assert any("所有権移転先" in t or "中間省略" in t for t in bc.tokuyaku)


def test_render_bc_excel() -> None:
    bc = transform_ab_to_bc(AB, DEAL)
    flat = _flat(juyojiko_excel.render(bc))
    assert "重 要 事 項 説 明 書" in flat
    assert "株式会社Martial Arts" in flat       # 売主B
    assert "東洋建設ホーム株式会社" in flat       # 買主C
    assert "27,800,000 円" in flat               # BC代金
    assert "新潟県長岡市曲新町551-1" in flat       # 物件
    # 用途地域チェックが正式名称に ■
    assert any(isinstance(v, str) and "■ 第1種住居地域" in v for v in flat)
    # 区域区分チェック
    assert any(isinstance(v, str) and "■ 市街化区域" in v for v in flat)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
