"""AB→BC 変換.

AB 側（仕入れ）の重要事項説明書データを、BC 側（B→C 転売）の重説データへ
**決定論的に**変換する。「間違いないように」の肝。

変換ルール:
- 物件事実（不動産の表示・登記・法令制限・設備・管理費等）は **そのまま引き継ぐ**。
- 売主: A（元所有者）→ **B（株式会社Martial Arts。案件マスタで上書き可）**。
- 買主: B → **C（最終買主。案件マスタ buyer_C）**。
- 売買代金: AB 仕入価格 → **BC 転売価格（案件マスタ bc_baibai_daikin）**。
- 宅建業者・取引士: BC 側媒介の情報が案件マスタにあれば差し替え、無ければ空欄
  （AB 側＝A の仲介業者の情報は引き継がない）。
- 取引態様・手付・清算起算日等: 案件マスタにあれば反映、無ければ既定/空欄。
- 特約（三為・所有権移転先指定）: 引き継いだうえで BC 用の注記を付す。
"""

from __future__ import annotations

from typing import Any

from juyojiko_schema import Gyosha, Juyojiko, Party, Torikiishi, TorihikiJoken
from keiyaku_schema import KeiyakuDaikin, Keiyakusho

DEFAULT_SELLER_B = "株式会社Martial Arts"

_SANME_NOTE = (
    "【BC特約】本物件は売主が第三者のためにする契約により取得した物件であり、"
    "所有権は元所有者から買主へ直接移転する（中間省略・所有権移転先指定）。"
)


def _bc_gyosha(deal: dict[str, Any]) -> Gyosha | None:
    if not any(k.startswith("bc_gyosha_") for k in deal):
        return None
    return Gyosha(
        menkyo_no=deal.get("bc_gyosha_menkyo_no"),
        menkyo_date=deal.get("bc_gyosha_menkyo_date"),
        shozai=deal.get("bc_gyosha_shozai"),
        tel=deal.get("bc_gyosha_tel"),
        shomei=deal.get("bc_gyosha_shomei"),
        daihyo=deal.get("bc_gyosha_daihyo"),
    )


def _bc_torikiishi(deal: dict[str, Any]) -> Torikiishi | None:
    if not any(k.startswith("bc_torikiishi_") for k in deal):
        return None
    return Torikiishi(
        toroku_no=deal.get("bc_torikiishi_toroku_no"),
        shimei=deal.get("bc_torikiishi_shimei"),
        jimusho=deal.get("bc_torikiishi_jimusho"),
        jimusho_shozai=deal.get("bc_torikiishi_jimusho_shozai"),
        tel=deal.get("bc_torikiishi_tel"),
    )


def _with_sanme_note(tokuyaku: list[str] | None) -> list[str]:
    out = list(tokuyaku or [])
    if not any("所有権移転先" in t or "中間省略" in t for t in out):
        out.append(_SANME_NOTE)
    return out


def transform_ab_to_bc(ab: Juyojiko, deal: dict[str, Any] | None = None) -> Juyojiko:
    """AB 重説 + 案件マスタ → BC 重説 を返す（AB は変更しない）."""
    deal = deal or {}
    bc = ab.model_copy(deep=True)

    # 売主 B（Martial Arts）
    bc.urinushi = Party(
        name=deal.get("seller_B") or DEFAULT_SELLER_B,
        address=deal.get("seller_B_address"),
    )
    # 買主 C（最終買主）
    bc.kainushi = Party(
        name=deal.get("buyer_C"),
        address=deal.get("buyer_C_address"),
    )

    # 取引条件: 代金を BC 価格へ。手付・清算起算日等は案件マスタにあれば反映。
    joken = bc.joken or TorihikiJoken()
    if deal.get("bc_baibai_daikin") is not None:
        joken.baibai_daikin = deal["bc_baibai_daikin"]
        joken.shohizei = deal.get("bc_shohizei")  # 内訳は通常再計算（無ければ空）
    if deal.get("bc_tetsuke") is not None:
        joken.tetsuke = deal["bc_tetsuke"]
    if deal.get("bc_seisan_kisanbi"):
        joken.seisan_kisanbi = deal["bc_seisan_kisanbi"]
    bc.joken = joken

    # 取引態様（BC 側）
    bc.torihiki_taiyo = deal.get("bc_torihiki_taiyo") or "売買 ・ 媒介"

    # 宅建業者・取引士（BC 側媒介）。案件マスタにあれば差し替え、無ければ空欄。
    bc.gyosha = _bc_gyosha(deal)
    bc.torikiishi = _bc_torikiishi(deal)

    # 特約: 三為（所有権移転先指定）を引き継ぎ、BC 用注記を付す
    bc.tokuyaku = _with_sanme_note(ab.tokuyaku)
    return bc


def transform_keiyaku_ab_to_bc(
    ab: Keiyakusho, deal: dict[str, Any] | None = None
) -> Keiyakusho:
    """AB 売買契約書 + 案件マスタ → BC 売買契約書 を返す（AB は変更しない）.

    物件表示・約款はそのまま引き継ぎ、当事者（売主A→B・買主B→C）と
    代金内訳（売買代金・手付・残代金）を差し替える。
    """
    deal = deal or {}
    bc = ab.model_copy(deep=True)

    bc.urinushi = Party(
        name=deal.get("seller_B") or DEFAULT_SELLER_B,
        address=deal.get("seller_B_address"),
    )
    bc.kainushi = Party(
        name=deal.get("buyer_C"),
        address=deal.get("buyer_C_address"),
    )

    # 代金内訳: BC 価格へ。内訳（手付・残代金・支払日）は案件マスタにあれば反映。
    d = bc.daikin or KeiyakuDaikin()
    price_changed = deal.get("bc_baibai_daikin") is not None
    if price_changed:
        d.baibai_daikin = deal["bc_baibai_daikin"]
        d.shohizei = deal.get("bc_shohizei")
    if deal.get("bc_tetsuke") is not None:
        d.tetsuke = deal["bc_tetsuke"]
    if deal.get("bc_zankin") is not None:
        d.zankin = deal["bc_zankin"]
    elif price_changed and not d.uchikin1 and not d.uchikin2 \
            and d.baibai_daikin is not None and d.tetsuke is not None:
        # 価格が変わったら残代金(=売買代金-手付)を再計算し、古い AB 残代金を上書き
        d.zankin = d.baibai_daikin - d.tetsuke
    if deal.get("bc_zankin_date"):
        d.zankin_date = deal["bc_zankin_date"]
    bc.daikin = d

    if deal.get("bc_hikiwatashi_date"):
        bc.hikiwatashi_date = deal["bc_hikiwatashi_date"]
    if deal.get("bc_loan_shonin_date"):
        bc.loan_shonin_date = deal["bc_loan_shonin_date"]

    bc.gyosha = _bc_gyosha(deal)
    bc.torikiishi = _bc_torikiishi(deal)
    bc.tokuyaku = _with_sanme_note(ab.tokuyaku)
    return bc
