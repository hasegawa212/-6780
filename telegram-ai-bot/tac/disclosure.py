"""勧誘に先立つ名乗り（勧誘目的の明示）。

電話で勧誘するときは、勧誘に先立って「事業者名・担当者名・商品の種類・勧誘目的」を
告げる。不動産の売買は特商法の適用除外だが宅建業法施行規則16条の12が名乗りを求め、
リフォーム・太陽光などは特商法§16（電話勧誘販売）の対象になる。両方を満たすよう
4 点すべてを告げる（どの規定がどの商材に当たるかは要専門家確認）。

自動アナウンスは担当者の言い忘れ防止であり、担当者本人の口頭での名乗りは続ける。
flask 非依存の純関数だけを置く。
"""

from __future__ import annotations

from .branding import AI_ROLE


def text(company: str, agent_name: str, product: str) -> str:
    """相手が出た直後に流す名乗りの文。"""
    return (
        f"こちらは{company.strip()}の{agent_name.strip()}です。"
        f"{product.strip()}のご案内のため、ご契約の勧誘を目的としてお電話いたしました。"
    )


def missing(company: str, agent_name: str, product: str) -> list[str]:
    """名乗りに足りない項目名。空なら告げられる。"""
    items = (("会社名", company), ("担当者名", agent_name), ("商品の種類", product))
    return [label for label, value in items if not (value or "").strip()]


def ai_text(company: str, product: str) -> str:
    """AI が掛ける電話の冒頭で、AI に接続する前にサーバーが流す名乗り（固定文）。

    事業者名・AI の自動音声であること・商品の種類・勧誘目的を告げる。
    LLM の自由文に任せない（言い忘れ・言い換えで名乗りが欠けないように）。
    """
    return (
        f"こちらは{company.strip()}の、{AI_ROLE}です。AIによる自動音声でご案内しています。"
        f"{product.strip()}のご案内のため、ご契約の勧誘を目的としてお電話いたしました。"
    )

