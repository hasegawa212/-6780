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

import house_style
from juyojiko_schema import Gyosha, Juyojiko, Party, Torikiishi, TorihikiJoken
from keiyaku_schema import KeiyakuDaikin, Keiyakusho

DEFAULT_SELLER_B = house_style.SELLER_B_MASTER["shomei"]

# 三為特約（四者間取引の特約）の御社標準全文（タイトル＋本文）。
_SANME_TOKUYAKU = [house_style.SANME_TOKUYAKU_TITLE, *house_style.SANME_TOKUYAKU_BODY]


def _gyosha_from(deal: dict[str, Any], prefix: str) -> Gyosha | None:
    """deal の `<prefix>_*` キーから Gyosha を組む（無ければ None）。"""
    if not any(k.startswith(prefix) for k in deal):
        return None
    g = lambda k: deal.get(prefix + k)  # noqa: E731
    return Gyosha(
        menkyo_no=g("menkyo_no"),
        menkyo_date=g("menkyo_date"),
        shozai=g("shozai"),
        tel=g("tel"),
        shomei=g("shomei"),
        daihyo=g("daihyo"),
        is_kyokai_member=g("is_kyokai_member"),
        hosho_kyokai=g("hosho_kyokai"),
        hosho_kyokai_addr=g("hosho_kyokai_addr"),
        hosho_honbu=g("hosho_honbu"),
        hosho_honbu_addr=g("hosho_honbu_addr"),
        bensai_kyotaku=g("bensai_kyotaku"),
        bensai_kyotaku_addr=g("bensai_kyotaku_addr"),
    )


def _torikiishi_from(deal: dict[str, Any], prefix: str) -> Torikiishi | None:
    """deal の `<prefix>_*` キーから Torikiishi を組む（無ければ None）。"""
    if not any(k.startswith(prefix) for k in deal):
        return None
    t = lambda k: deal.get(prefix + k)  # noqa: E731
    return Torikiishi(
        toroku_no=t("toroku_no"),
        shimei=t("shimei"),
        jimusho=t("jimusho"),
        jimusho_shozai=t("jimusho_shozai"),
        tel=t("tel"),
    )


def _bc_gyosha(deal: dict[str, Any]) -> Gyosha | None:
    """売主側の宅建業者B（重説表紙の左欄）。bc_gyosha_* 由来。"""
    return _gyosha_from(deal, "bc_gyosha_")


def _bc_torikiishi(deal: dict[str, Any]) -> Torikiishi | None:
    """売主側業者の取引士（左欄）。bc_torikiishi_* 由来。"""
    return _torikiishi_from(deal, "bc_torikiishi_")


def _bc_baikai_gyosha(deal: dict[str, Any]) -> Gyosha | None:
    """媒介業者（重説表紙の右欄）。bc_baikai_gyosha_* 由来。"""
    return _gyosha_from(deal, "bc_baikai_gyosha_")


def _bc_baikai_torikiishi(deal: dict[str, Any]) -> Torikiishi | None:
    """媒介業者側の取引士（右欄）。bc_baikai_torikiishi_* 由来。"""
    return _torikiishi_from(deal, "bc_baikai_torikiishi_")


def _with_sanme_note(tokuyaku: list[str] | None) -> list[str]:
    """AB 引継ぎの特約に、御社標準の三為特約（四者間取引の特約）全文を付す。

    既に三為（所有権移転先指定／四者間／他人物売買）の記載があれば二重付与しない。
    """
    out = list(tokuyaku or [])
    if not any(("所有権移転先" in t or "四者間" in t or "他人物売買" in t or "中間省略" in t)
               for t in out):
        out.extend(_SANME_TOKUYAKU)
    return out


def _with_standard_yonin(yonin: list[str] | None) -> list[str]:
    """容認事項に御社標準セットを既定で付す（AB引継ぎと重複する項目は付与しない）。"""
    out = list(yonin or [])
    for std in house_style.STANDARD_YONIN_JIKO:
        key = std[:18]  # 先頭で重複判定（言い回し差を吸収）
        if not any(key in t for t in out):
            out.append(std)
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
        # 内訳（土地/建物/消費税）はBC価格に応じ案件マスタから設定（無ければ空）
        joken.tochi_kakaku = deal.get("bc_tochi_kakaku")
        joken.tatemono_kakaku = deal.get("bc_tatemono_kakaku")
        joken.shohizei = deal.get("bc_shohizei")
    if deal.get("bc_tetsuke") is not None:
        joken.tetsuke = deal["bc_tetsuke"]
    if deal.get("bc_seisan_kisanbi"):
        joken.seisan_kisanbi = deal["bc_seisan_kisanbi"]
    bc.joken = joken

    # 取引態様（BC 側）
    bc.torihiki_taiyo = deal.get("bc_torihiki_taiyo") or "売買 ・ 媒介"

    # 宅建業者・取引士（表紙）。案件マスタにあれば差し替え、無ければ空欄/クリア。
    #   左欄＝売主である宅建業者B（bc_gyosha_*）、右欄＝媒介業者（bc_baikai_gyosha_*）。
    bc.gyosha = _bc_gyosha(deal)
    bc.torikiishi = _bc_torikiishi(deal)
    bc.baikai_gyosha = _bc_baikai_gyosha(deal)
    bc.baikai_torikiishi = _bc_baikai_torikiishi(deal)

    # 特約: 三為（四者間取引の特約）の御社標準全文を引き継ぎ＋付与
    bc.tokuyaku = _with_sanme_note(ab.tokuyaku)
    # 容認事項: 御社標準セットを既定で付与（物件固有のAB引継ぎとマージ）
    bc.yonin_jiko = _with_standard_yonin(ab.yonin_jiko)
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
    # 表紙の追加日付（公租公課起算日・契約締結日・融資解除期日）は案件マスタで上書き可。
    # 起算日は重説と同じ bc_seisan_kisanbi を共用（両書類で同一値にするため）。
    if deal.get("bc_seisan_kisanbi"):
        bc.seisan_kisanbi = deal["bc_seisan_kisanbi"]
    if deal.get("bc_keiyaku_date"):
        bc.keiyaku_date = deal["bc_keiyaku_date"]
    if deal.get("bc_loan_kaijo_date"):
        bc.loan_kaijo_date = deal["bc_loan_kaijo_date"]

    # 違約金は御社標準＝売買代金の20%相当額（案件マスタ bc_iyakukin_wariai で上書き可）。
    d.iyakukin_wariai = deal.get(
        "bc_iyakukin_wariai", house_style.KEIYAKU_DEFAULTS["iyakukin_wariai"]
    )

    bc.gyosha = _bc_gyosha(deal)
    bc.torikiishi = _bc_torikiishi(deal)
    # 契約書の特約欄は御社定型「重要事項説明書に準拠する。以下余白」へ集約
    # （三為特約・容認事項の本文は重説側に記載）。案件マスタに個別特約があればそれを使う。
    bc.tokuyaku = deal.get("bc_keiyaku_tokuyaku") or [
        f"{house_style.KEIYAKU_TOKUYAKU_REF}{house_style.SECTION_END_MARK}"
    ]
    return bc
