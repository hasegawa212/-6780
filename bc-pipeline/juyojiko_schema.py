"""重要事項説明書（35条書面）の構造化スキーマ.

宅建業法35条の重説を、AB→BC 変換と Excel 再現のために構造化したもの。
実物の重説（区分所有: フレクション長岡 / 土地建物: 戸建）の項目に準拠する。

設計方針:
- 物件事実（不動産の表示・登記・法令制限・設備・管理費等）は AB から BC へ
  **そのまま引き継ぐ**。当事者（売主A→B、買主B→C）と売買代金のみ差し替える。
- 取りこぼしを避けるため全モデルで extra="allow"（未知項目も保持）。
- チェックボックス選択肢は「選ばれた値の文字列」で持ち、Excel 側で ■/□ を描く。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow")


# ── 当事者 ─────────────────────────────────────────────────────
class Gyosha(_Base):
    """宅地建物取引業者（説明する業者）."""

    menkyo_no: str | None = None       # 免許証番号
    menkyo_date: str | None = None     # 免許年月日
    shozai: str | None = None          # 主たる事務所の所在地
    tel: str | None = None
    shomei: str | None = None          # 商号または名称
    daihyo: str | None = None          # 代表者氏名


class Torikiishi(_Base):
    """説明をする宅地建物取引士."""

    toroku_no: str | None = None       # 登録番号
    shimei: str | None = None          # 氏名
    jimusho: str | None = None         # 業務に従事する事務所名
    jimusho_shozai: str | None = None
    tel: str | None = None


class Party(_Base):
    """売主・買主の表示."""

    address: str | None = None
    name: str | None = None
    biko: str | None = None


# ── A 不動産の表示 ─────────────────────────────────────────────
class TochiHyoji(_Base):
    shozai: str | None = None          # 所在・地番
    chimoku: str | None = None         # 地目
    chiseki_toki: str | None = None    # 地積（登記簿）
    chiseki_jissoku: str | None = None # 実測面積


class TatemonoHyoji(_Base):
    shozai: str | None = None
    kaoku_bango: str | None = None     # 家屋番号
    meisho: str | None = None          # 建物の名称
    shurui: str | None = None          # 種類（居宅 等）
    kozo: str | None = None            # 構造
    yukamenseki: str | None = None     # 床面積
    chikujiki: str | None = None       # 建築時期


class ShikichikenTochi(_Base):
    shozai: str | None = None
    chiban: str | None = None
    chimoku: str | None = None
    chiseki: str | None = None
    shikichiken_shurui: str | None = None  # 敷地権の種類（所有権 等）
    wariai: str | None = None              # 敷地権の割合


class FudosanHyoji(_Base):
    """A 不動産の表示（戸建/区分 両対応）."""

    bukken_type: str | None = None     # 戸建 / 区分
    jukyo_hyoji: str | None = None     # 住居表示
    # 戸建（土地建物）
    tochi: TochiHyoji | None = None
    tatemono: TatemonoHyoji | None = None
    fuzoku_tatemono: str | None = None  # 附属建物の有無（有/無）
    fuzoku_tatemono_detail: str | None = None  # 附属建物の詳細（種類・構造等）
    # 区分所有
    ittou_shozai: str | None = None    # 一棟の建物の所在
    ittou_kozo: str | None = None      # 一棟の構造
    ittou_enshoumenseki: str | None = None
    senyuu: TatemonoHyoji | None = None        # 専有部分の表示
    shikichiken: list[ShikichikenTochi] = []   # 敷地権の目的である土地
    kiyaku_shikichi: str | None = None         # 規約敷地の有無


# ── Ⅰ-2 法令に基づく制限 ──────────────────────────────────────
class HoreiSeigen(_Base):
    """都市計画法・建築基準法等に基づく制限."""

    # 都市計画法
    toshikeikaku_kuiki: str | None = None  # 都市計画区域内/外
    kuiki_kubun: str | None = None         # 市街化区域/市街化調整区域/非線引
    # 建築基準法
    yoto: str | None = None                # 用途地域（正式名称）
    nijuni_jo: bool | None = None          # 建築基準法第22条区域
    boka: str | None = None                # 防火地域/準防火地域 等
    kodo_chiku: str | None = None          # 高度地区
    chiiki_chiku: list[str] = []           # その他の地域地区
    kenpei: int | None = None              # 指定建蔽率(%)
    kenpei_kanwa: str | None = None        # 角地・耐火等の緩和注記
    yoseki: int | None = None              # 指定容積率(%)
    yoseki_zenmen_doro: str | None = None  # 前面道路による制限の注記
    nisshido: str | None = None            # 日影規制
    doro: str | None = None                # 接面道路の概要（備考）
    doro_hoko: str | None = None           # 接面道路の方向（南/南西 等）
    doro_haba: str | None = None           # 接面道路の幅員
    doro_setsudo: str | None = None        # 接道の長さ
    shikichi_saitei: str | None = None     # 敷地面積の最低限度
    suigai_shozai: str | None = None       # 水害ハザードマップにおける所在地の説明
    other_horei: list[str] = []            # 都計法・建基法以外の法令


# ── Ⅰ-6 区分所有: 管理・費用 ──────────────────────────────────
class Setsubi(_Base):
    """飲用水・電気・ガス・排水の整備状況（種別）."""

    suidou: str | None = None    # 公営水道 / 私営水道 / 井戸
    gas: str | None = None       # 都市ガス / 個別プロパン / 集中プロパン
    osui: str | None = None      # 公共下水 / 個別浄化槽 / 集中浄化槽 / 汲取式
    zassui: str | None = None     # 公共下水 / 個別浄化槽 / 集中浄化槽 / 側溝等 / 浸透式
    denryoku: str | None = None  # 電力会社名（小売電気事業者）
    biko: str | None = None      # 設備に関する備考


class Saigai(_Base):
    """災害区域・調査の該当（Ⅰ-10〜15）。各 True=内/該当、False=外/非該当。"""

    zosei_bosai: bool | None = None       # 造成宅地防災区域（内=True）
    dosha_keikai: bool | None = None      # 土砂災害警戒区域
    dosha_tokubetsu: bool | None = None   # 土砂災害特別警戒区域
    tsunami_keikai: bool | None = None    # 津波災害警戒区域
    tsunami_tokubetsu: bool | None = None  # 津波災害特別警戒区域
    taishin_shindan: bool | None = None   # 耐震診断の有無（有=True）
    sekimen_kiroku: bool | None = None    # 石綿使用調査記録の有無（有=True）
    kozui: bool | None = None             # 水害ハザード 洪水（有=True）
    naisui: bool | None = None            # 水害ハザード 雨水出水(内水)（有=True）
    takashio: bool | None = None          # 水害ハザード 高潮（有=True）


class Kakunin(_Base):
    """建築確認・検査済証（Ⅰ-9）."""

    kenchiku_bango: str | None = None       # 建築確認 番号
    kenchiku_date: str | None = None        # 建築確認 交付年月日（元号年月日）
    kensa_bango: str | None = None          # 検査済証 番号
    kensa_date: str | None = None           # 検査済証 交付年月日


class Touki(_Base):
    """登記記録（Ⅰ-1。三為では登記名義人は元所有者のまま＝AB引継ぎ）."""

    tochi_shoyusha_jusho: str | None = None    # 土地 所有者 住所
    tochi_shoyusha_shimei: str | None = None   # 土地 所有者 氏名
    tochi_otsuku: str | None = None            # 土地 権利部（乙区）
    tatemono_shoyusha_jusho: str | None = None  # 建物 所有者 住所
    tatemono_shoyusha_shimei: str | None = None  # 建物 所有者 氏名
    tatemono_otsuku: str | None = None          # 建物 権利部（乙区）


class KanriHiyou(_Base):
    """区分所有建物の管理費・修繕積立金・管理組合等."""

    kanrihi_getsugaku: int | None = None       # 通常の管理費(月額)
    shuzen_getsugaku: int | None = None        # 修繕積立金(月額)
    shuzen_tsumitate: int | None = None        # すでに積み立てられている額
    kanrihi_taino: int | None = None           # 管理費滞納額
    shuzen_taino: int | None = None            # 修繕積立金滞納額
    kanri_kumiai: str | None = None            # 管理組合の名称
    kanri_keitai: str | None = None            # 管理形態（全部委託 等）
    kanri_itakusaki: str | None = None         # 管理委託先
    yoto_seigen: str | None = None             # 専有部分の用途制限
    pet_seigen: str | None = None              # ペット飼育制限


# ── 借地権（借地借家法）─────────────────────────────────────────
class Shakuchi(_Base):
    """借地権付き建物の場合の借地条件（借地借家法・重説借地権の標準項目）.

    AB→BC では底地（地主）・借地条件は物件事実として **そのまま引き継ぐ**。
    当事者（借地人 A→B→C）と売買代金のみ差し替わる。
    注意: 借地説明書シートへのセル差込は実データ照合済みの様式が無い限り
    行わない（cellmaps 側で未対応＝備考転記に留める）。
    """

    shakuchiken_shurui: str | None = None   # 借地権の種類（普通/一般定期/事業用定期/建物譲渡特約付）
    toki_umu: str | None = None             # 借地権の登記の有無（有/無）
    sonzoku_kikan: str | None = None        # 存続期間（始期〜満了日）
    keiyaku_shiki: str | None = None        # 契約の始期
    keiyaku_manryo: str | None = None       # 契約の満了日
    jidai_kingaku: int | None = None        # 地代（月額 or 年額）
    jidai_tani: str | None = None           # 地代の単位（月額/年額）
    jidai_shiharai: str | None = None       # 地代の支払方法・支払先
    jidai_kaitei: str | None = None         # 地代改定の特約
    koshin_ryo: str | None = None           # 更新料の有無・額
    joto_shodaku: str | None = None         # 借地権譲渡の地主承諾・承諾料
    kenchiku_seigen: str | None = None      # 建物の建築・増改築に関する制限
    teichi_shoyusha_jusho: str | None = None  # 底地（地主）の住所
    teichi_shoyusha_shimei: str | None = None  # 底地（地主）の氏名
    biko: str | None = None                 # 借地条件に関する備考


# ── Ⅱ 取引条件 ────────────────────────────────────────────────
class TorihikiJoken(_Base):
    baibai_daikin: int | None = None           # 売買代金
    shohizei: int | None = None                # うち消費税等相当額
    tetsuke: int | None = None                 # 手付金
    seisan_kisanbi: str | None = None          # 公租公課の清算起算日
    iyakukin_wariai: int | None = None         # 違約金（売買代金の%）
    tanpo_sekinin: str | None = None           # 担保責任/契約不適合の措置
    loan_tokuyaku: bool | None = None          # 融資利用の特約


# ── 重要事項説明書 全体 ───────────────────────────────────────
class Juyojiko(_Base):
    """重要事項説明書 1 通分の構造化データ."""

    bukken_type: str | None = None             # 戸建 / 区分
    torihiki_taiyo: str | None = None          # 取引態様（売買・媒介 等）
    gyosha: Gyosha | None = None               # 説明する宅建業者（売主側）
    torikiishi: Torikiishi | None = None       # 宅地建物取引士
    urinushi: Party | None = None              # B 売主の表示
    kainushi: Party | None = None              # 買主
    fudosan: FudosanHyoji | None = None        # A 不動産の表示
    touki_meigi: str | None = None             # 登記記録上の所有者
    senyuusha_uchi: str | None = None          # 第三者占有（賃借人）の有無/概要
    horei: HoreiSeigen | None = None           # Ⅰ-2 法令制限
    setsubi: str | None = None                 # Ⅰ-4 供給・排水（概要）
    setsubi_detail: Setsubi | None = None      # Ⅰ-4 供給・排水（種別）
    saigai: Saigai | None = None               # Ⅰ-10〜15 災害区域・調査
    kakunin: Kakunin | None = None             # Ⅰ-9 建築確認・検査済証
    touki: Touki | None = None                 # Ⅰ-1 登記記録の権利
    kanri: KanriHiyou | None = None            # Ⅰ-6 区分所有の管理
    shakuchi: Shakuchi | None = None           # 借地権付き建物の借地条件（借地借家法）
    joken: TorihikiJoken | None = None         # Ⅱ 取引条件
    yonin_jiko: list[str] = []                 # Ⅲ その他重要な事項（容認事項）
    tokuyaku: list[str] = []                   # Ⅴ 備考（特約事項）
    seisan_biko: str | None = None             # 公租公課の清算に関する備考
