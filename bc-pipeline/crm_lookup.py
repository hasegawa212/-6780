"""CRM連携のフォールバック実装（2026-09-19 再生成）。
元の crm_lookup.py が未コミットで消失していたため、アプリを起動させる安全な既定値版。
CRM自動補完は空を返す（BC生成の主機能は正常動作）。本来版が復元でき次第、差し替えること。
bc_service.py が使用: enrich_deal_master / search_customer / find_property / list_generated / reload_crm
"""
from typing import Any


def enrich_deal_master(deal_master: dict | None, crm_name: str = "", bukken_hint: str = "") -> dict:
    # CRM補完データ無し → 入力をそのまま返す（既存値を壊さない）
    return dict(deal_master or {})


def search_customer(name: str) -> dict[str, Any] | None:
    return None


def find_property(customer: Any, bukken: str = "") -> Any:
    return None


def list_generated(name: str = "") -> list:
    return []


def reload_crm() -> int:
    return 0
