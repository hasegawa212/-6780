"""不動産売買契約書（FRK標準書式）の構造化スキーマ.

契約書 = 表紙（物件表示・代金内訳・当事者・特約）＋ 約款（標準条文）。
重説と同様、AB→BC では物件表示・約款はそのまま引き継ぎ、当事者と代金のみ
差し替える。約款本文は標準書式（著作物）のため、抽出できたものを引き継ぐ。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from juyojiko_schema import FudosanHyoji, Gyosha, Party, Torikiishi


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow")


class KeiyakuDaikin(_Base):
    """売買代金・手付・内金・残代金とその支払期日."""

    baibai_daikin: int | None = None     # 売買代金（第1項）
    shohizei: int | None = None          # うち消費税等相当額
    tetsuke: int | None = None           # 手付金（第2項）
    uchikin1: int | None = None          # 内金①（第3項）
    uchikin1_date: str | None = None
    uchikin2: int | None = None          # 内金②
    uchikin2_date: str | None = None
    zankin: int | None = None            # 残代金（第5項）
    zankin_date: str | None = None       # 残代金支払期日


class Jokan(_Base):
    """約款の1条."""

    jo: str | None = None       # 例 "第1条"
    midashi: str | None = None  # 見出し
    honbun: str | None = None   # 本文（抽出できた場合のみ）


# FRK 標準書式の条文見出し（本文は別添約款による）。約款が抽出できない場合の骨子。
DEFAULT_JOKAN_TITLES: list[str] = [
    "売買の目的物および売買代金",
    "手付金",
    "売買対象面積および測量",
    "公租公課等の分担",
    "所有権の移転と引渡し",
    "所有権移転登記等",
    "抵当権等の抹消",
    "引渡し前の滅失・毀損（危険負担）",
    "物件状況等報告書・設備表",
    "契約不適合責任",
    "手付解除",
    "契約違反による解除・違約金",
    "反社会的勢力の排除",
    "融資利用の特約（ローン特約）",
    "印紙代等の負担",
    "管轄裁判所",
    "特約",
]


class Keiyakusho(_Base):
    """不動産売買契約書 1 通分の構造化データ."""

    bukken_type: str | None = None              # 戸建 / 区分
    urinushi: Party | None = None               # 売主
    kainushi: Party | None = None               # 買主
    gyosha: Gyosha | None = None                # 宅地建物取引業者
    torikiishi: Torikiishi | None = None        # 宅地建物取引士
    fudosan: FudosanHyoji | None = None         # 不動産の表示（重説と共通）
    daikin: KeiyakuDaikin | None = None         # 代金内訳
    hikiwatashi_date: str | None = None         # 引渡し日
    loan_tokuyaku: bool | None = None           # ローン特約の有無
    loan_kingaku: int | None = None             # 融資利用予定額
    loan_shonin_date: str | None = None         # 融資承認取得期日
    tokuyaku: list[str] = []                    # 特約事項
    jokan: list[Jokan] = []                     # 約款（標準条文）
