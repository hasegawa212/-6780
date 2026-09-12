"""不動産情報ライブラリ タイルAPI（XKT/XPT系）で都市計画・地価情報を取得.

対応API:
  XKT002 — 用途地域（建ぺい率・容積率）
  XKT014 — 防火・準防火地域
  XKT025 — 液状化の発生傾向
  XPT002 — 地価公示・地価調査

認証: Ocp-Apim-Subscription-Key ヘッダーにAPIキーを設定。
      キーは ~/.openclaw/secrets/reinfolib_api_key または環境変数 REINFOLIB_API_KEY。
"""

from __future__ import annotations

import gzip
import json
import math
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_BASE = "https://www.reinfolib.mlit.go.jp/ex-api/external"
_UA = "bc-pipeline/1.0 (+juyojiko auto-fill)"
_TIMEOUT = 10.0

_api_key: str | None = None
_cache: dict[str, Any] = {}


def _get_api_key() -> str:
    global _api_key
    if _api_key:
        return _api_key
    key = os.environ.get("REINFOLIB_API_KEY", "").strip()
    if not key:
        p = Path.home() / ".openclaw/secrets/reinfolib_api_key"
        if p.exists():
            key = p.read_text().strip()
    if not key:
        raise RuntimeError("REINFOLIB_API_KEY が未設定です")
    _api_key = key
    return key


def _tile_xy(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2.0 ** z
    x = int((lon + 180.0) / 360.0 * n)
    r = math.radians(lat)
    y = int((1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * n)
    return x, y


def _fetch_tile(api: str, z: int, x: int, y: int) -> list[dict[str, Any]]:
    cache_key = f"reinfolib:{api}:{z}:{x}:{y}"
    if cache_key in _cache:
        return _cache[cache_key]

    url = (f"{_BASE}/{api}?response_format=geojson"
           f"&z={z}&x={x}&y={y}")
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Ocp-Apim-Subscription-Key": _get_api_key(),
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    })
    features: list[dict[str, Any]] = []
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read()
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
            data = json.loads(raw.decode("utf-8"))
            features = data.get("features") or []
    except Exception:
        pass
    _cache[cache_key] = features
    return features


def _point_in_polygon(lat: float, lon: float, coords: list) -> bool:
    """Ray-casting point-in-polygon (GeoJSON座標: [lon, lat])."""
    ring = coords[0] if coords and isinstance(coords[0][0], list) else coords
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _find_containing_feature(lat: float, lon: float, features: list[dict]) -> dict[str, Any] | None:
    for f in features:
        geom = f.get("geometry") or {}
        gtype = geom.get("type", "")
        coords = geom.get("coordinates") or []
        if gtype == "Polygon":
            if _point_in_polygon(lat, lon, coords):
                return f.get("properties") or {}
        elif gtype == "MultiPolygon":
            for poly in coords:
                if _point_in_polygon(lat, lon, poly):
                    return f.get("properties") or {}
    return None


def _find_nearest_point(lat: float, lon: float, features: list[dict],
                        max_km: float = 3.0) -> dict[str, Any] | None:
    best, best_d = None, float("inf")
    for f in features:
        geom = f.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        c = geom.get("coordinates") or []
        if len(c) < 2:
            continue
        dlat = lat - c[1]
        dlon = (lon - c[0]) * math.cos(math.radians(lat))
        d = math.sqrt(dlat**2 + dlon**2) * 111.32
        if d < best_d:
            best_d = d
            best = f.get("properties") or {}
    if best_d > max_km:
        return None
    return best


# ── 用途地域 ────────────────────────────────────────────────────
def yoto_chiki(lat: float, lon: float) -> dict[str, Any]:
    """用途地域・建ぺい率・容積率を取得（XKT002）。"""
    for z in (15, 14, 13):
        x, y = _tile_xy(lat, lon, z)
        features = _fetch_tile("XKT002", z, x, y)
        if not features:
            continue
        props = _find_containing_feature(lat, lon, features)
        if props:
            kenpei_raw = props.get("u_building_coverage_ratio_ja", "")
            yoseki_raw = props.get("u_floor_area_ratio_ja", "")
            kenpei = _parse_pct(kenpei_raw)
            yoseki = _parse_pct(yoseki_raw)
            return {
                "yoto": props.get("use_area_ja"),
                "kenpei": kenpei,
                "yoseki": yoseki,
                "kenpei_text": kenpei_raw,
                "yoseki_text": yoseki_raw,
                "youto_id": props.get("youto_id"),
                "city_name": props.get("city_name"),
                "prefecture": props.get("prefecture"),
                "estimated": False,
                "_source": "不動産情報ライブラリ XKT002",
            }
    return {"yoto": None, "kenpei": None, "yoseki": None, "estimated": True,
            "_source": "取得不可（当該地域にデータなし）"}


def _parse_pct(s: str) -> int | None:
    if not s:
        return None
    import re
    m = re.search(r"(\d+)", s.replace(",", ""))
    return int(m.group(1)) if m else None


# ── 防火・準防火地域 ─────────────────────────────────────────────
def bouka_chiiki(lat: float, lon: float) -> dict[str, Any]:
    """防火・準防火地域判定（XKT014）。"""
    for z in (15, 14, 13):
        x, y = _tile_xy(lat, lon, z)
        features = _fetch_tile("XKT014", z, x, y)
        if not features:
            continue
        props = _find_containing_feature(lat, lon, features)
        if props:
            return {
                "bouka": props.get("fire_prevention_districts_ja") or props.get("bouka_kubun"),
                "hit": True,
                "_source": "不動産情報ライブラリ XKT014",
                "_raw": props,
            }
    return {"bouka": None, "hit": False, "_source": "区域外またはデータなし"}


# ── 液状化の発生傾向 ─────────────────────────────────────────────
def ekijoka(lat: float, lon: float) -> dict[str, Any]:
    """液状化の発生傾向（XKT025）。"""
    for z in (14, 13, 12):
        x, y = _tile_xy(lat, lon, z)
        features = _fetch_tile("XKT025", z, x, y)
        if not features:
            continue
        props = _find_containing_feature(lat, lon, features)
        if props:
            note = (props.get("note")
                    or props.get("liquefaction_ja")
                    or props.get("liquefaction_tendency")
                    or "")
            topo = props.get("topographic_classification_name_ja", "")
            level = props.get("liquefaction_tendency_level")
            label = note or ("液状化の可能性あり" if level and level >= 2 else "")
            return {
                "ekijoka": label,
                "topo": topo,
                "level": level,
                "hit": True,
                "_source": "不動産情報ライブラリ XKT025",
            }
    return {"ekijoka": None, "hit": False, "_source": "区域外またはデータなし"}


# ── 地価公示・地価調査 ───────────────────────────────────────────
def chika_koji(lat: float, lon: float) -> dict[str, Any]:
    """最寄りの地価公示・地価調査ポイントを取得（XPT002）。"""
    for z in (13, 12, 11):
        x, y = _tile_xy(lat, lon, z)
        all_features: list[dict] = []
        # 中心タイル + 周囲8タイルを探索（地価ポイントは疎なので広く探す）
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                features = _fetch_tile("XPT002", z, x + dx, y + dy)
                all_features.extend(features)
        if not all_features:
            continue
        props = _find_nearest_point(lat, lon, all_features, max_km=5.0)
        if props:
            return {
                "chika_price": props.get("u_current_years_price_ja"),
                "chika_change": props.get("year_on_year_change_rate"),
                "chika_yoto": props.get("regulations_use_category_name_ja"),
                "chika_bouka": props.get("regulations_fireproof_name_ja"),
                "chika_kenpei": props.get("u_regulations_building_coverage_ratio_ja"),
                "chika_yoseki": props.get("u_regulations_floor_area_ratio_ja"),
                "gas": props.get("gas_supply_availability"),
                "water": props.get("water_supply_availability"),
                "sewer": props.get("sewer_supply_availability"),
                "lot_number": props.get("standard_lot_number_ja"),
                "location": props.get("location"),
                "_source": "不動産情報ライブラリ XPT002",
            }
    return {"chika_price": None, "_source": "近隣に地価公示ポイントなし"}


# ── 地価公示・鑑定評価書（REST API）────────────────────────────────
_PREF_CODES: dict[str, str] = {
    "北海道": "01", "青森県": "02", "岩手県": "03", "宮城県": "04",
    "秋田県": "05", "山形県": "06", "福島県": "07", "茨城県": "08",
    "栃木県": "09", "群馬県": "10", "埼玉県": "11", "千葉県": "12",
    "東京都": "13", "神奈川県": "14", "新潟県": "15", "富山県": "16",
    "石川県": "17", "福井県": "18", "山梨県": "19", "長野県": "20",
    "岐阜県": "21", "静岡県": "22", "愛知県": "23", "三重県": "24",
    "滋賀県": "25", "京都府": "26", "大阪府": "27", "兵庫県": "28",
    "奈良県": "29", "和歌山県": "30", "鳥取県": "31", "島根県": "32",
    "岡山県": "33", "広島県": "34", "山口県": "35", "徳島県": "36",
    "香川県": "37", "愛媛県": "38", "高知県": "39", "福岡県": "40",
    "佐賀県": "41", "長崎県": "42", "熊本県": "43", "大分県": "44",
    "宮崎県": "45", "鹿児島県": "46", "沖縄県": "47",
}


def _pref_code_from_address(address: str) -> str | None:
    for pref, code in _PREF_CODES.items():
        if pref in address:
            return code
    return None


def _city_code_from_muni(muni_cd: str | None) -> str | None:
    if not muni_cd or len(muni_cd) < 5:
        return None
    return muni_cd[:5]


def chika_koji_rest(address: str, pref_code: str | None = None,
                    city_name: str | None = None) -> dict[str, Any]:
    """地価公示・鑑定評価書データを取得（XCT001 REST API）。
    最寄りの地点を住所マッチで探す。"""
    if not pref_code:
        pref_code = _pref_code_from_address(address)
    if not pref_code:
        return {"chika_price": None, "_source": "都道府県コード不明"}

    import datetime
    year = datetime.date.today().year

    cache_key = f"xct001:{pref_code}:{year}"
    if cache_key not in _cache:
        all_items: list[dict] = []
        for div in ("00", "05"):
            url = (f"{_BASE}/XCT001?year={year}&area={pref_code}&division={div}")
            req = urllib.request.Request(url, headers={
                "User-Agent": _UA,
                "Ocp-Apim-Subscription-Key": _get_api_key(),
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            })
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read()
                    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                        raw = gzip.decompress(raw)
                    data = json.loads(raw.decode("utf-8"))
                    if isinstance(data, dict):
                        all_items.extend(data.get("data") or [])
            except Exception:
                pass
        _cache[cache_key] = all_items

    items = _cache.get(cache_key, [])
    if not items:
        return {"chika_price": None, "_source": "地価データ取得失敗"}

    # フィールド名（スペース区切り）
    F_LOC = "標準地 所在地 所在地番"
    F_PRICE = "1㎡当たりの価格"
    F_ROSENKA = "路線価 相続税路線価"
    F_WATER = "標準地 供給処理施設 水道"
    F_GAS = "標準地 供給処理施設 ガス"
    F_SEWER = "標準地 供給処理施設 下水道"
    F_YOTO = "標準地 法令上の規制等 用途地域"
    F_BOUKA = "標準地 法令上の規制等 防火"
    F_STATION = "標準地 交通施設の状況 交通施設"
    F_CITY_CODE = "標準地番号 市区町村コード 市区町村コード"
    F_REGION = "標準地番号 地域名"

    # 市区町村名でフィルタ → 最初にマッチしたものを使用
    best = None
    for item in items:
        region = item.get(F_REGION, "")
        loc = item.get(F_LOC, "")
        if city_name:
            if city_name.replace("市", "") in region or city_name in loc:
                best = item
                break
        else:
            best = item
            break

    if not best:
        # フィルタなしで最初の1件
        best = items[0] if items else None

    if best:
        price = best.get(F_PRICE)
        rosenka = best.get(F_ROSENKA)
        water = best.get(F_WATER)
        gas = best.get(F_GAS)
        sewer = best.get(F_SEWER)
        return {
            "chika_price": f"{int(price):,}円/㎡" if price else None,
            "rosenka": f"{int(rosenka):,}円/㎡" if rosenka else None,
            "chika_yoto": best.get(F_YOTO),
            "chika_bouka": best.get(F_BOUKA),
            "gas": gas == "1" if gas is not None else None,
            "water": water == "1" if water is not None else None,
            "sewer": sewer == "1" if sewer is not None else None,
            "location": best.get(F_LOC),
            "station": best.get(F_STATION),
            "_source": "不動産情報ライブラリ XCT001",
        }

    return {"chika_price": None, "_source": "地価データ取得失敗"}


# ── 参照リンク生成（APIなし項目）──────────────────────────────────
def reference_links(address: str, lat: float, lon: float) -> dict[str, Any]:
    """APIが存在しない項目の確認用リンクを生成する。"""
    encoded_addr = urllib.parse.quote(address)
    return {
        "maizo_bunkazai": {
            "label": "埋蔵文化財包蔵地",
            "status": "要確認",
            "url": f"https://heritagemap.nabunken.go.jp/?lat={lat}&lng={lon}&zoom=15",
            "note": "文化財総覧WebGIS（奈良文化財研究所）で確認",
        },
        "dojyo_osen": {
            "label": "土壌汚染",
            "status": "要確認",
            "url": "https://www.env.go.jp/water/dojo/wpcl.html",
            "note": "環境省の要措置区域等一覧で確認",
        },
        "rosenka_map": {
            "label": "路線価図",
            "status": "参考",
            "url": f"https://www.rosenka.nta.go.jp/",
            "note": "国税庁の路線価図で詳細確認",
        },
    }


# ── まとめ取得 ───────────────────────────────────────────────────
def lookup_all(lat: float, lon: float, address: str = "",
               budget: float = 20.0) -> dict[str, Any]:
    """全APIを一括取得。budget秒を超えた項目はスキップ。"""
    deadline = time.monotonic() + max(budget, 3.0)
    result: dict[str, Any] = {}

    result["yoto"] = yoto_chiki(lat, lon)

    if time.monotonic() < deadline:
        result["bouka"] = bouka_chiiki(lat, lon)
    else:
        result["bouka"] = {"bouka": None, "_timeout": True}

    if time.monotonic() < deadline:
        result["ekijoka"] = ekijoka(lat, lon)
    else:
        result["ekijoka"] = {"ekijoka": None, "_timeout": True}

    if time.monotonic() < deadline and address:
        city = result["yoto"].get("city_name")
        pref = result["yoto"].get("prefecture")
        pref_code = _pref_code_from_address(pref or address) if (pref or address) else None
        result["chika"] = chika_koji_rest(address, pref_code=pref_code, city_name=city)
    else:
        result["chika"] = {"chika_price": None, "_timeout": True}

    result["refs"] = reference_links(address or "", lat, lon)

    return result
