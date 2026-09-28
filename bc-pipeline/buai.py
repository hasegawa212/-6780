"""歩合計算（インセンティブ配分）v5.7.7.

粗利から歩率で歩合プールを算出し、役割別に配分する。

計算手順:
  1. キャップ前利益 = min(粗利, 500万)          … 1件あたりの利益上限
  2. 歩合プール    = キャップ前利益 × 歩率        … 商品種別ごとの歩率
     ただしプールは 110万 を上限とする（歩合プール上限）
  3. 配分          = プール × 各役割の配分率

注意:
- 金額は円の整数。配分は端数を最後の役割で吸収し、合計＝プールを保証する。
- 粗利・商品種別は案件から与える。役割は空でもよい（空なら配分ゼロ）。
"""

from __future__ import annotations

from typing import Any

# 商品種別ごとの歩率
BUAI_RATE: dict[str, float] = {
    "不動産": 0.22,
    "FGH": 0.22,
    "リフォーム": 0.18,
    "私募債": 0.05,
}

CAP_PROFIT = 5_000_000     # キャップ前利益の上限（500万）
POOL_CAP = 1_100_000       # 歩合プールの上限（110万）

# 役割別の配分率（合計 = 1.00）
ALLOCATION: list[tuple[str, float]] = [
    ("アポ", 0.25),
    ("同行①", 0.18),
    ("同行②", 0.30),
    ("業務", 0.21),
    ("業務部席", 0.025),
    ("営業部席", 0.015),
    ("営業課席", 0.015),
    ("マスター", 0.005),
]


def _rate_for(product: str | None) -> float:
    return BUAI_RATE.get(str(product or "").strip(), BUAI_RATE["不動産"])


def calc_buai(gross_profit: int, product: str = "不動産",
              members: dict[str, str] | None = None) -> dict[str, Any]:
    """粗利 → 歩合プールと役割別配分。

    gross_profit: 粗利（円）
    product:      '不動産' / 'FGH' / 'リフォーム' / '私募債'
    members:      {役割: 担当者名} 任意。名前を配分に添えるだけ。
    """
    gp = max(int(gross_profit or 0), 0)
    rate = _rate_for(product)
    capped = min(gp, CAP_PROFIT)
    pool_raw = int(round(capped * rate))
    pool = min(pool_raw, POOL_CAP)
    pool_capped = pool_raw > POOL_CAP

    members = members or {}
    alloc: list[dict[str, Any]] = []
    acc = 0
    for i, (role, share) in enumerate(ALLOCATION):
        if i < len(ALLOCATION) - 1:
            amt = int(round(pool * share))
            acc += amt
        else:
            amt = pool - acc          # 端数を最終役割で吸収
        alloc.append({
            "role": role, "share": share, "amount": amt,
            "member": members.get(role, ""),
        })

    return {
        "gross_profit": gp,
        "product": product,
        "rate": rate,
        "capped_profit": capped,          # min(粗利, 500万)
        "pool_before_cap": pool_raw,
        "pool": pool,                     # 110万上限適用後
        "pool_capped": pool_capped,       # 110万で頭打ちしたか
        "allocation": alloc,
        "allocation_total": sum(a["amount"] for a in alloc),
        "version": "5.7.7",
    }
