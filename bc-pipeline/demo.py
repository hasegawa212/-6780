#!/usr/bin/env python3
"""BC自動生成パイプラインのオフライン エンドツーエンド デモ.

ライブのAI抽出（/extract, 要 ANTHROPIC_API_KEY）を使わずに、抽出後の
構造化データ（AB重説・AB契約書）から BC一式（重要事項説明書・売買契約書）
を生成する全工程を実演する。

実運用では本デモの「サンプルAB」を /extract の出力（実PDFからの抽出結果）に
差し替えるだけでフル自動になる。

使い方:
    python demo.py                       # 標準デモ（所有権物件）
    python demo.py --shakuchi            # 借地権物件のデモ（備考に借地条件）
    python demo.py --variant 36-1 --template 本番WB.xlsx  # 本番WBへ差込
    python demo.py --out ./demo_out      # 出力先ディレクトリ

個人情報は一切含めない（物件属性とダミー社名のみ）。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import juyojiko_excel
import keiyaku_excel
from bc_transform import transform_ab_to_bc, transform_keiyaku_ab_to_bc
from juyojiko_schema import (
    FudosanHyoji,
    HoreiSeigen,
    Juyojiko,
    KanriHiyou,
    Party,
    Shakuchi,
    TatemonoHyoji,
    TochiHyoji,
    TorihikiJoken,
)
from keiyaku_schema import KeiyakuDaikin, Keiyakusho


def sample_ab_juyojiko(shakuchi: bool = False) -> Juyojiko:
    """サンプルAB重説（戸建・非個人情報）。/extract の出力に相当。"""
    j = Juyojiko(
        bukken_type="戸建",
        torihiki_taiyo="売買 ・ 媒介",
        urinushi=Party(name="売主A（元所有者）"),
        kainushi=Party(name="株式会社Martial Arts"),
        fudosan=FudosanHyoji(
            bukken_type="戸建",
            jukyo_hyoji="茨城県ひたちなか市〇〇1-2-3",
            tochi=TochiHyoji(shozai="ひたちなか市大字勝田字寺漏 3317番11",
                             chimoku="宅地", chiseki_toki="213.96㎡"),
            tatemono=TatemonoHyoji(shurui="居宅",
                                   kozo="木造スレート葺2階建", yukamenseki="122.97㎡"),
        ),
        horei=HoreiSeigen(
            toshikeikaku_kuiki="都市計画区域内", kuiki_kubun="市街化区域",
            yoto="第1種住居地域", nijuni_jo=True, kenpei=60, yoseki=200,
        ),
        kanri=KanriHiyou(),
        joken=TorihikiJoken(baibai_daikin=16_900_000),
        yonin_jiko=["本物件は現況有姿売買とする。"],
        tokuyaku=["別添「設備表」において有とした設備を含む。"],
    )
    if shakuchi:
        j.fudosan.jukyo_hyoji = "茨城県ひたちなか市〇〇1-2-3（借地）"
        j.shakuchi = Shakuchi(
            shakuchiken_shurui="普通借地権",
            toki_umu="無",
            sonzoku_kikan="令和3年4月1日〜令和23年3月31日",
            jidai_kingaku=30_000, jidai_tani="月額",
            jidai_shiharai="毎月末日までに地主指定口座へ振込",
            koshin_ryo="更新時に借地権価格の5%相当",
            joto_shodaku="譲渡には地主の書面承諾を要する（承諾料 別途）",
            teichi_shoyusha_shimei="地主（底地所有者）",
        )
    return j


def sample_ab_keiyaku() -> Keiyakusho:
    """サンプルAB売買契約書（戸建・非個人情報）。"""
    return Keiyakusho(
        bukken_type="戸建",
        urinushi=Party(name="売主A（元所有者）"),
        kainushi=Party(name="株式会社Martial Arts"),
        fudosan=FudosanHyoji(
            bukken_type="戸建",
            tochi=TochiHyoji(shozai="ひたちなか市大字勝田字寺漏 3317番11",
                             chimoku="宅地", chiseki_toki="213.96㎡"),
            tatemono=TatemonoHyoji(shurui="居宅", yukamenseki="122.97㎡"),
        ),
        daikin=KeiyakuDaikin(baibai_daikin=16_900_000, tetsuke=1_900_000,
                             zankin=15_000_000, zankin_date="2025年4月10日"),
        tokuyaku=["別添「設備表」において有とした設備を含む。"],
    )


def sample_deal() -> dict:
    """案件マスタ（BC側）。Google Sheets「案件マスタ」相当。"""
    return {
        "buyer_C": "東洋建設ホーム株式会社",
        "buyer_C_address": "東京都〇〇区〇〇1-1-1",
        "bc_baibai_daikin": 27_800_000,
        "bc_tetsuke": 1_000_000,
        "bc_zankin": 26_800_000,
        "bc_zankin_date": "2025年5月20日",
    }


def _yen(v: int | None) -> str:
    return f"{v:,}円" if v is not None else "（空欄）"


def _live_extract(pdf_path: str, doc_type: str) -> dict:
    """実PDFから /extract（Claude）で構造化データを得る（要 ANTHROPIC_API_KEY）。

    鍵が無い場合は HTTPException(400) を投げる。呼び出し側で握って案内する。
    """
    import base64

    from bc_service import ExtractReq, extract  # 既存の抽出ロジックを再利用
    b64 = base64.b64encode(Path(pdf_path).read_bytes()).decode()
    resp = extract(ExtractReq(doc_type=doc_type, file_base64=b64, mime="application/pdf"))
    # /extract は失敗しても例外を投げず {extracted:{}, warning:...} を返す設計。
    # デモは実データ抽出が空なら「サンプルへ退避」させたいので、ここで例外に変換する。
    if not resp.extracted:
        raise RuntimeError(resp.warning or "実PDFからの抽出に失敗しました。")
    return resp.extracted


def main() -> int:
    ap = argparse.ArgumentParser(description="BC自動生成パイプライン オフラインデモ")
    ap.add_argument("--out", default="demo_out", help="出力ディレクトリ")
    ap.add_argument("--shakuchi", action="store_true", help="借地権物件のデモ")
    ap.add_argument("--variant", choices=["36-1", "37-1", "38-1"],
                    help="本番WBへの差込バリアント（--template と併用）")
    ap.add_argument("--template", help="本番ワークブック(.xlsx)。指定時はセル差込も実演")
    ap.add_argument("--live", metavar="重説PDF",
                    help="実PDFをClaudeで抽出（要 ANTHROPIC_API_KEY）。"
                         "サンプルABの代わりに実データで一気通し")
    ap.add_argument("--live-keiyaku", metavar="契約書PDF",
                    help="実契約書PDF（--live と併用）")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # ① 抽出済みデータ（実運用では /extract の戻り値）
    deal = sample_deal()
    if args.live:
        try:
            ab_j = Juyojiko(**_live_extract(args.live, "juyojiko"))
            ab_k = (Keiyakusho(**_live_extract(args.live_keiyaku, "keiyaku"))
                    if args.live_keiyaku else sample_ab_keiyaku())
            print(f"=== ライブ抽出成功: {args.live} ===")
        except Exception as e:  # HTTPException(no key) 等
            detail = getattr(e, "detail", str(e))
            print(f"[ライブ抽出スキップ] {detail}")
            print("  → ANTHROPIC_API_KEY を設定すれば実PDFから一気通しになります。")
            print("  → 今回はサンプルABで続行します。\n")
            ab_j = sample_ab_juyojiko(shakuchi=args.shakuchi)
            ab_k = sample_ab_keiyaku()
    else:
        ab_j = sample_ab_juyojiko(shakuchi=args.shakuchi)
        ab_k = sample_ab_keiyaku()

    # ② AB→BC 変換（当事者A→B→C・代金差替。物件事実は引継ぎ）
    bc_j = transform_ab_to_bc(ab_j, deal)
    bc_k = transform_keiyaku_ab_to_bc(ab_k, deal)

    print("=== AB→BC 変換サマリ ===")
    print(f"  売主 : {ab_j.urinushi.name}  →  {bc_j.urinushi.name}")
    print(f"  買主 : {ab_j.kainushi.name}  →  {bc_j.kainushi.name}")
    print(f"  代金 : {_yen(ab_j.joken.baibai_daikin)}  →  {_yen(bc_j.joken.baibai_daikin)}")
    print(f"  物件 : {bc_j.fudosan.jukyo_hyoji}（AB のまま引継ぎ）")
    if args.shakuchi:
        print(f"  借地 : {bc_j.shakuchi.shakuchiken_shurui} / "
              f"地代{bc_j.shakuchi.jidai_tani}{_yen(bc_j.shakuchi.jidai_kingaku)} → 備考へ転記")
    print(f"  特約 : 三為（中間省略）注記を自動付与（計{len(bc_j.tokuyaku)}件）")

    # ③ 標準様式の Excel 生成（テンプレ不要・自己完結）
    j_path = out / "BC重要事項説明書.xlsx"
    k_path = out / "BC不動産売買契約書.xlsx"
    j_path.write_bytes(juyojiko_excel.render(bc_j))
    k_path.write_bytes(keiyaku_excel.render(bc_k))
    print("\n=== 標準様式 Excel 出力 ===")
    print(f"  {j_path}")
    print(f"  {k_path}")

    # ④ 本番WBへのセル差込（任意。--template 指定時のみ）
    if args.template:
        import cellmaps
        import wb_fill
        tpl = Path(args.template).read_bytes()
        variant = args.variant or "36-1"
        sv, sc = cellmaps.build_juyojiko(variant, bc_j)
        filled, n = wb_fill.fill_workbook(tpl, sv, sc)
        f_path = out / f"BC重説_本番WB差込_{variant}.xlsx"
        f_path.write_bytes(filled)
        print("\n=== 本番WB差込 ===")
        print(f"  {f_path}（{n}セル差込, variant={variant}）")
    else:
        print("\n（--template 未指定のため本番WB差込はスキップ）")

    print("\n実運用ではサンプルABを /extract（実PDF抽出, 要APIキー）に差し替えるだけでフル自動。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
