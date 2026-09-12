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
DEFAULT_SELLER_B_ADDR = house_style.SELLER_B_MASTER["shozai"]

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


def _default_seller_gyosha() -> Gyosha:
    """売主業者B（株式会社Martial Arts）の既定 Gyosha。

    案件マスタに bc_gyosha_* が無い場合の既定として、house_style の会社マスタを
    全書類・全シートへ自動入力する（要件: 会社情報を全シートに自動入力）。
    保証協会（不動産保証協会）の社員チェックも既定 True で立てる。
    """
    m = house_style.SELLER_B_MASTER
    return Gyosha(
        menkyo_no=m.get("menkyo_no"),
        shozai=m.get("shozai"),
        tel=m.get("tel"),
        shomei=m.get("shomei"),
        daihyo=m.get("daihyo"),
        is_kyokai_member=m.get("is_kyokai_member"),
        hosho_kyokai=m.get("hosho_kyokai"),
        hosho_honbu=m.get("hosho_honbu"),
        bensai_kyotaku=m.get("bensai_kyotaku"),
        bensai_kyotaku_addr=m.get("bensai_kyotaku_addr"),
    )


def _default_seller_torikiishi() -> Torikiishi | None:
    """既定の説明宅建士。退職者を除き、最初の現役取引士を返す。"""
    cand = house_style.SELLER_B_TORIKIISHI
    if not cand:
        return None
    from datetime import date
    today = date.today().isoformat()
    for t in cand:
        rd = t.get("retire_date")
        if rd and rd <= today:
            continue
        return Torikiishi(
            toroku_no=t.get("toroku_no"),
            shimei=t.get("shimei"),
            jimusho=t.get("jimusho"),
        )
    t = cand[-1]
    return Torikiishi(
        toroku_no=t.get("toroku_no"),
        shimei=t.get("shimei"),
        jimusho=t.get("jimusho"),
    )


def _with_sanme_note(tokuyaku: list[str] | None) -> list[str]:
    """AB 引継ぎの特約に、御社標準の三為特約（四者間取引の特約）全文を付す。

    既に三為（所有権移転先指定／四者間／他人物売買）の記載があれば二重付与しない。
    """
    out = list(tokuyaku or [])
    if not any(("所有権移転先" in t or "四者間" in t or "他人物売買" in t or "中間省略" in t)
               for t in out):
        out.extend(_SANME_TOKUYAKU)
    return out


def _strip_chukan_shoryaku(tokuyaku: list[str] | None) -> list[str]:
    """特約から中間省略（第三者のためにする特約）の記述を除去する。

    実務ルール「AB間で中間省略がある場合、BC間の特記事項からは中間省略の
    記述を削除する」に対応。※ 既定では呼ばれない（deal で明示指定時のみ）。
    法的に重大な削除のため opt-in とする。
    """
    kws = house_style.CHUKAN_KEYWORDS
    return [t for t in (tokuyaku or []) if not any(k in t for k in kws)]


# ── 実務ノウハウ：備考への自動注記・消費税・特約テンプレ挿入 ─────
_CHOSEI_NOTE = (
    "本物件は市街化調整区域内に存します。建築基準法その他関係法令に基づく建築規制"
    "（既存宅地・開発許可・都市計画法第43条許可等）は物件ごとに異なるため、"
    "建築の可否・要件を所管行政庁に個別にご確認ください。"
)
_KYOTEI_NOTE = (
    "本物件には建築協定が存します。建築協定の内容は詳細にわたるため、"
    "制限事項の全容は協定書原本により個別にご確認ください。"
)
_NAISUI_NOTE = (
    "洪水ハザードマップにおいて本物件が最大浸水想定の区域に該当する場合、"
    "内水（雨水出水）ハザードマップにより代替してリスクを確認するものとします。"
)


def _kuiki_is_chosei(bc: Juyojiko) -> bool:
    h = getattr(bc, "horei", None)
    return bool(h and "調整" in str(getattr(h, "kuiki_kubun", "") or ""))


def _has_kenchiku_kyotei(bc: Juyojiko) -> bool:
    h = getattr(bc, "horei", None)
    if not h:
        return False
    blob = " ".join([str(x) for x in (getattr(h, "other_horei", None) or [])])
    blob += " " + str(getattr(h, "chiiki_chiku", "") or "")
    return "建築協定" in blob


def _flood_is_max(bc: Juyojiko) -> bool:
    s = getattr(bc, "saigai", None)
    return bool(s and getattr(s, "kozui", None) is True)


def apply_knowhow(bc: Juyojiko, deal: dict[str, Any]) -> list[str]:
    """物件データと案件フラグに応じて備考(tokuyaku)へ注記・特約を追加する。

    - 情報系の注記（調整区域・建築協定・内水代替）は該当時に自動付与。
    - 特約テンプレ（中間省略・抵当権除去）は deal フラグ指定時のみ挿入。
    戻り値: 追加した項目のラベル一覧（warnings 生成用）。
    """
    added: list[str] = []
    tok = list(bc.tokuyaku or [])

    def _add(text: str, label: str) -> None:
        if text and text not in tok:
            tok.append(text)
            added.append(label)

    # 特約テンプレ（選択挿入）
    if deal.get("bc_add_tokuyaku_chukan"):
        _add(house_style.TOKUYAKU_CHUKAN_SHORYAKU_TITLE + "\n"
             + house_style.TOKUYAKU_CHUKAN_SHORYAKU, "特約:中間省略")
    if deal.get("bc_add_tokuyaku_teitoken"):
        _add(house_style.TOKUYAKU_TEITOKEN_JOKYO_TITLE + "\n"
             + house_style.TOKUYAKU_TEITOKEN_JOKYO, "特約:抵当権除去")
    if deal.get("bc_add_tokuyaku_loan"):
        _add(house_style.TOKUYAKU_LOAN_TITLE + "\n"
             + house_style.TOKUYAKU_LOAN, "特約:ローン")
    if deal.get("bc_add_tokuyaku_setsubi"):
        _add(house_style.TOKUYAKU_SETSUBI_TITLE + "\n"
             + house_style.TOKUYAKU_SETSUBI, "特約:設備引渡し")
    if deal.get("bc_add_tokuyaku_kizu"):
        _add(house_style.TOKUYAKU_KIZU_MENSEKI_TITLE + "\n"
             + house_style.TOKUYAKU_KIZU_MENSEKI, "特約:瑕疵担保免責")

    # データ依存の自動注記（既定ON。deal で個別に抑止可）
    if deal.get("note_chosei") is not False and _kuiki_is_chosei(bc):
        _add(_CHOSEI_NOTE, "注記:市街化調整区域")
    if deal.get("note_kenchiku_kyotei") is not False and _has_kenchiku_kyotei(bc):
        _add(_KYOTEI_NOTE, "注記:建築協定")
    if deal.get("note_naisui") is not False and _flood_is_max(bc):
        _add(_NAISUI_NOTE, "注記:内水代替")

    bc.tokuyaku = tok
    return added


def apply_shohizei(bc: Juyojiko) -> bool:
    """建物価格が判っていて消費税が空欄なら、建物価格×10%で自動算出する。

    ※ 土地は非課税。原本に消費税の記載があれば上書きしない。
    戻り値: 自動算出したら True。
    """
    j = getattr(bc, "joken", None)
    if not j:
        return False
    tate = getattr(j, "tatemono_kakaku", None)
    if not tate:
        return False
    if getattr(j, "shohizei", None):
        return False        # 原本優先（上書きしない）
    try:
        j.shohizei = int(round(int(tate) * 0.10))
        return True
    except (TypeError, ValueError):
        return False


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
        address=deal.get("seller_B_address") or DEFAULT_SELLER_B_ADDR,
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
    # 御社標準の取引条件を既定付与（BC＝対個人）。契約書側と一致させ、重説Ⅱ取引条件の
    # 違約金・担保措置が空欄にならないようにする（案件マスタ指定があればそちら優先）。
    if deal.get("bc_iyakukin_wariai") is not None:
        joken.iyakukin_wariai = deal["bc_iyakukin_wariai"]
    elif joken.iyakukin_wariai is None:
        joken.iyakukin_wariai = house_style.KEIYAKU_DEFAULTS["iyakukin_wariai"]
    if joken.tanpo_sekinin is None:
        joken.tanpo_sekinin = deal.get("bc_tanpo_sochi") or house_style.TANPO_SOCHI_DEFAULT
    bc.joken = joken

    # 取引態様（BC 側）
    bc.torihiki_taiyo = deal.get("bc_torihiki_taiyo") or "売買 ・ 媒介"

    # 宅建業者・取引士（表紙）。案件マスタにあれば差し替え、無ければ空欄/クリア。
    #   左欄＝売主である宅建業者B（bc_gyosha_*）、右欄＝媒介業者（bc_baikai_gyosha_*）。
    #   売主業者B（御社）は未指定でも既定マスタ（MA）を全シートへ自動入力する。
    #   媒介（右欄）は未指定なら空欄クリア（旧案件の残渣を消す運用）。
    bc.gyosha = _bc_gyosha(deal) or _default_seller_gyosha()
    bc.torikiishi = _bc_torikiishi(deal) or _default_seller_torikiishi()
    bc.baikai_gyosha = _bc_baikai_gyosha(deal)
    bc.baikai_torikiishi = _bc_baikai_torikiishi(deal)

    # 特約: 三為（四者間取引の特約）の御社標準全文を引き継ぎ＋付与
    #   deal に bc_omit_chukan_shoryaku=True があるときは、実務ルールに従い
    #   AB から引き継いだ中間省略の記述を削除する（四者間特約は付与しない）。
    if deal.get("bc_omit_chukan_shoryaku"):
        bc.tokuyaku = _strip_chukan_shoryaku(ab.tokuyaku)
    else:
        bc.tokuyaku = _with_sanme_note(ab.tokuyaku)
    # 容認事項: 御社標準セットを既定で付与（物件固有のAB引継ぎとマージ）
    bc.yonin_jiko = _with_standard_yonin(ab.yonin_jiko)

    # 実務ノウハウ: 備考への注記（調整区域・建築協定・内水）／特約テンプレ挿入
    apply_knowhow(bc, deal)
    # 消費税（建物価格×10%）の自動算出（空欄時のみ）
    apply_shohizei(bc)
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
        address=deal.get("seller_B_address") or DEFAULT_SELLER_B_ADDR,
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

    # 売主業者B（御社）は未指定でも既定マスタ（MA）を自動入力する。
    bc.gyosha = _bc_gyosha(deal) or _default_seller_gyosha()
    bc.torikiishi = _bc_torikiishi(deal) or _default_seller_torikiishi()
    # 契約書の特約欄は御社定型「重要事項説明書に準拠する。以下余白」へ集約
    # （三為特約・容認事項の本文は重説側に記載）。案件マスタに個別特約があればそれを使う。
    bc.tokuyaku = deal.get("bc_keiyaku_tokuyaku") or [
        f"{house_style.KEIYAKU_TOKUYAKU_REF}{house_style.SECTION_END_MARK}"
    ]
    return bc


def juyojiko_to_keiyakusho(bc_j: Juyojiko, deal: dict[str, Any] | None = None) -> Keiyakusho:
    """BC重説から BC売買契約書データを自動生成する。"""
    deal = deal or {}
    j = bc_j.joken or TorihikiJoken()

    baibai = deal.get("bc_baibai_daikin") or j.baibai_daikin
    tochi = deal.get("bc_tochi_kakaku") or j.tochi_kakaku
    tatemono = deal.get("bc_tatemono_kakaku") or j.tatemono_kakaku
    shohizei = deal.get("bc_shohizei") or j.shohizei
    tetsuke = deal.get("bc_tetsuke") or j.tetsuke
    zankin = deal.get("bc_zankin") or j.zankin
    if zankin is None and baibai is not None and tetsuke is not None:
        zankin = baibai - tetsuke
    loan_kingaku = deal.get("bc_loan_kingaku") or j.loan_kingaku

    kainushi_name = deal.get("bc_kainushi")
    kainushi_addr = deal.get("bc_kainushi_addr")
    kainushi = bc_j.kainushi
    if kainushi_name:
        kainushi = Party(name=kainushi_name, address=kainushi_addr)

    daikin = KeiyakuDaikin(
        baibai_daikin=baibai,
        tochi_kakaku=tochi,
        tatemono_kakaku=tatemono,
        shohizei=shohizei,
        tetsuke=tetsuke,
        zankin=zankin,
        zankin_date=deal.get("bc_zankin_date") or j.zankin_date,
        iyakukin_wariai=j.iyakukin_wariai or house_style.KEIYAKU_DEFAULTS["iyakukin_wariai"],
    )

    bc_k = Keiyakusho(
        bukken_type=bc_j.bukken_type,
        urinushi=bc_j.urinushi,
        kainushi=kainushi,
        gyosha=bc_j.gyosha,
        torikiishi=bc_j.torikiishi,
        fudosan=bc_j.fudosan,
        daikin=daikin,
        hikiwatashi_date=deal.get("bc_hikiwatashi_date") or j.hikiwatashi_date,
        seisan_kisanbi=deal.get("bc_seisan_kisanbi") or j.seisan_kisanbi,
        keiyaku_date=deal.get("bc_keiyaku_date"),
        loan_tokuyaku=j.loan_tokuyaku,
        loan_kingaku=loan_kingaku,
        loan_shonin_date=deal.get("bc_loan_shonin_date") or j.loan_shonin_date,
        loan_kaijo_date=deal.get("bc_loan_kaijo_date") or j.loan_kaijo_date or j.loan_shonin_date,
        tokuyaku=deal.get("bc_keiyaku_tokuyaku") or [
            f"{house_style.KEIYAKU_TOKUYAKU_REF}\n{house_style.SECTION_END_MARK}"
        ],
    )
    return bc_k


# ===== 販売価格自動計算（2026-07-21追加） =====
def calc_bc_price(ab_price, reform=3000000, target_margin=5000000):
    """AB間仕入価格からBC間販売価格を自動計算
    販売価格 = (仕入 + リフォーム + 利幅) / 0.93 (諸経費7%込み)
    """
    if not ab_price or ab_price <= 0:
        return None
    return int((ab_price + reform + target_margin) / 0.93)

def calc_margin(bc_price, ab_price, reform=3000000):
    """利幅を計算"""
    if not bc_price or not ab_price:
        return None
    expenses = int(bc_price * 0.07)
    return bc_price - ab_price - reform - expenses

