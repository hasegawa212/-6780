"""不動産売買契約書を Excel で再現するレンダラ.

表紙（当事者・物件表示・代金内訳・特約）＋ 約款（条文）を上から再現する。
物件表示の描画は重説レンダラ（juyojiko_excel.write_fudosan）を共用する。
"""

from __future__ import annotations

from juyojiko_excel import _Sheet, _yen, write_fudosan
from keiyaku_schema import DEFAULT_JOKAN_TITLES, Keiyakusho


def render(k: Keiyakusho) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "不動産売買契約書"
    s = _Sheet(ws)

    bukken = k.bukken_type or (k.fudosan.bukken_type if k.fudosan else None) or ""
    s.title("不 動 産 売 買 契 約 書")
    s.kv("物件種別", bukken)
    s.gap()

    # 当事者
    s.section("売主・買主")
    s.kv("売主（B）住所", k.urinushi.address if k.urinushi else None)
    s.kv("売主（B）氏名", k.urinushi.name if k.urinushi else None)
    s.kv("買主（C）住所", k.kainushi.address if k.kainushi else None)
    s.kv("買主（C）氏名", k.kainushi.name if k.kainushi else None)
    g = k.gyosha
    s.kv("宅地建物取引業者", g.shomei if g else None)
    t = k.torikiishi
    s.kv("宅地建物取引士", t.shimei if t else None)
    s.gap()

    # 物件表示（重説と共通）
    s.section("不動産の表示（第1条）")
    write_fudosan(s, k.fudosan, bukken)
    s.gap()

    # 代金内訳
    s.section("売買代金・手付金・支払い方法")
    d = k.daikin
    if d:
        s.kv("売買代金（第1項）", _yen(d.baibai_daikin))
        s.kv("うち消費税等相当額", _yen(d.shohizei))
        s.kv("手付金（第2項）", _yen(d.tetsuke))
        if d.uchikin1 is not None:
            s.kv("内金①（第3項）", f"{_yen(d.uchikin1)}　{d.uchikin1_date or ''}".strip())
        if d.uchikin2 is not None:
            s.kv("内金②", f"{_yen(d.uchikin2)}　{d.uchikin2_date or ''}".strip())
        s.kv("残代金（第5項）", f"{_yen(d.zankin)}　{d.zankin_date or ''}".strip())
    s.kv("引渡し日", k.hikiwatashi_date)
    s.gap()

    # ローン特約
    s.section("融資利用の特約（ローン特約）")
    s.kv("ローン特約", "有" if k.loan_tokuyaku else ("無" if k.loan_tokuyaku is False else ""))
    s.kv("融資利用予定額", _yen(k.loan_kingaku))
    s.kv("融資承認取得期日", k.loan_shonin_date)
    s.gap()

    # 特約事項
    s.section("特約事項")
    s.listblock("特約", k.tokuyaku)
    s.gap()

    # 約款（条文）
    s.section("約款（FRK標準書式）")
    if k.jokan:
        for j in k.jokan:
            head = f"{j.jo or ''} {j.midashi or ''}".strip()
            s.kv(head or "（条）", j.honbun)
    else:
        # 抽出が無い場合は標準条文の見出し骨子のみ（本文は別添約款による）
        for i, title in enumerate(DEFAULT_JOKAN_TITLES, 1):
            s.kv(f"第{i}条 {title}", "（本文は別添 FRK 標準約款による）")

    import io

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
