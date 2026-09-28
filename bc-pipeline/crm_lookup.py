"""CRM連携。顧客データ(PII)は公開リポジトリに含めず、実行環境の外部ファイルから読む。
探索順: 環境変数 BC_CRM_FILE → /etc/secrets/crm_customers.json → 同階層 crm_customers.json。
データ形式: {"customers":[...]} または [...]。顧客は name / properties を持つ想定。
ファイルが無ければ空を返す（アプリは正常起動・CRM補完は無効のまま）。
bc_service.py が使用: search_customer / find_property / list_generated / reload_crm / enrich_deal_master
"""
import os, json, re, threading
from pathlib import Path

_LOCK = threading.Lock()
_CACHE: dict = {"customers": None, "path": None}
_PATHS = [
    os.environ.get("BC_CRM_FILE", ""),
    "/etc/secrets/crm_customers.json",
    str(Path(__file__).parent / "crm_customers.json"),
    str(Path(__file__).parent / "data" / "crm_customers.json"),
]


def _norm(s) -> str:
    return re.sub(r'[\s　様]', '', str(s or '')).lower()


def _load():
    for p in _PATHS:
        if p and os.path.exists(p):
            try:
                d = json.loads(Path(p).read_text(encoding='utf-8'))
                cust = d.get("customers") if isinstance(d, dict) else d
                if isinstance(cust, list):
                    return cust, p
            except Exception:
                continue
    return [], None


def _customers():
    with _LOCK:
        if _CACHE["customers"] is None:
            _CACHE["customers"], _CACHE["path"] = _load()
        return _CACHE["customers"]


def reload_crm() -> int:
    with _LOCK:
        _CACHE["customers"], _CACHE["path"] = _load()
        return len(_CACHE["customers"])


def search_customer(name: str):
    if not name:
        return None
    key = _norm(name)
    best = None
    for c in _customers():
        nm = c.get("name") or c.get("氏名") or c.get("customer") or ""
        n = _norm(nm)
        if not n:
            continue
        if n == key:
            props = c.get("properties") or c.get("物件") or []
            return {**c, "name": nm, "properties": props}
        if best is None and key and key in n:
            props = c.get("properties") or c.get("物件") or []
            best = {**c, "name": nm, "properties": props}
    return best


def find_property(customer, bukken: str = ""):
    if not customer:
        return None
    props = customer.get("properties") or []
    if not props:
        return None
    if bukken:
        b = _norm(bukken)
        for p in props:
            label = _norm(p.get("name") or p.get("物件名") or p.get("property") or p.get("address") or p.get("所在") or p)
            if b and b in label:
                return p
    return props[0]


def list_generated(name: str = "") -> list:
    if name:
        c = search_customer(name)
        if c:
            return c.get("generated") or c.get("documents") or []
    return []


def enrich_deal_master(deal_master, crm_name: str = "", bukken_hint: str = "") -> dict:
    # 安全側：法的書類に誤った値を入れないため、確実な項目のみ・空欄だけ補完。
    dm = dict(deal_master or {})
    c = search_customer(crm_name) if crm_name else None
    if not c:
        return dm
    if c.get("name") and not dm.get("customer_name"):
        dm["customer_name"] = c["name"]
    return dm
