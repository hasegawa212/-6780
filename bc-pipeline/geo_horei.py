"""住所 → 法令制限（推定）／ハザード（国土地理院タイル判定）.

重要な設計方針（重説は法的文書のため）:

1. **AB 書類から読み取れた実データを上書きしない。** ここで得るのは
   「空欄を埋める補助値」であり、原本の記載が常に優先される。
2. **通信失敗と『該当なし』を厳密に区別する。** タイルが 404 = その区域に
   データが無い（＝該当なし）だが、通信エラー時は None（＝要確認）を返す。
   ここを混同すると重説に虚偽の「該当なし」が載る。
3. **用途地域・建蔽率・容積率は公的APIから取得できない。** 国土地理院の
   逆ジオコーダが返すのは市区町村コードと町名のみ（実測で確認済み）。
   よって都道府県ベースの *推定値* を返し、必ず estimated=True を立てる。
   利用側は「要確認」として扱うこと。

外部API:
  - 住所→経緯度: https://msearch.gsi.go.jp/address-search/AddressSearch
  - 経緯度→住所: https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress
  - ハザード:     https://disaportaldata.gsi.go.jp/raster/<layer>/{z}/{x}/{y}.png
"""

from __future__ import annotations

import json
import math
import struct
import time
import urllib.parse
import urllib.request
import zlib
from typing import Any

_UA = "bc-pipeline/1.0 (+juyojiko auto-fill)"
_TIMEOUT = float(8.0)

# ハザードタイルの判定ズーム候補（高い順に試し、最初に取得できたものを使う）。
#
# 重要: レイヤーごとに公開されている最大ズームが違う（実測: 土砂災害系は z15 まで、
# z16 は 404）。ズームを固定すると「レイヤーの上限を超えただけの 404」を
# 「区域外」と誤判定し、重説に虚偽の『該当なし』を出力してしまう。
# そのため段階的に下げ、全ズームで 404 のときだけ「区域外」と確定させる。
_ZOOMS = (16, 15, 14)

# 実在を疎通確認済みのレイヤーのみ採用する（名前が誤っていると全タイル404となり
# 「該当なし」を誤って出力してしまうため、未確認のものは載せない）。
HAZARD_LAYERS: dict[str, str] = {
    "kozui": "01_flood_l2_shinsuishin_data",       # 洪水浸水想定（想定最大規模）
    "naisui": "02_naisui_data",                    # 内水（雨水出水）浸水想定区域
    "takashio": "03_hightide_l2_shinsuishin_data",  # 高潮浸水想定
    "tsunami": "04_tsunami_newlegend_data",         # 津波浸水想定
    "dosekiryu": "05_dosekiryukeikaikuiki",         # 土砂災害警戒区域（土石流）
    "kyukeisha": "05_kyukeishakeikaikuiki",         # 土砂災害警戒区域（急傾斜地）
    "jisuberi": "05_jisuberikeikaikuiki",           # 土砂災害警戒区域（地すべり）
}
UNAVAILABLE = ()

# RGBA色 → 浸水深テキスト（重説記載用。周辺ピクセルの多数決で判定）
FLOOD_DEPTH_LEGEND: list[tuple[tuple[int, int, int], str]] = [
    ((242, 133, 201), "0.5m未満"),
    ((255, 255, 179), "0.5〜3.0m"),
    ((255, 218, 65),  "3.0〜5.0m"),
    ((255, 145, 64),  "5.0〜10.0m"),
    ((220, 122, 220), "10.0〜20.0m"),
    ((219, 0, 170),   "20.0m以上"),
]
DOSHA_LEGEND: list[tuple[tuple[int, int, int], str]] = [
    ((255, 255, 0), "警戒区域"),
    ((255, 0, 0),   "特別警戒区域"),
]

# 都道府県別の既定（推定）。用途地域は公的APIで取れないため運用上の初期値。
_PREF_DEFAULT: dict[str, dict[str, Any]] = {
    "茨城県": {"yoto": "第一種低層住居専用地域", "kenpei": 50, "yoseki": 100},
    "栃木県": {"yoto": "第一種低層住居専用地域", "kenpei": 50, "yoseki": 100},
    "愛知県": {"yoto": "第一種住居地域", "kenpei": 60, "yoseki": 200},
}
_FALLBACK = {"yoto": None, "kenpei": None, "yoseki": None}

_cache: dict[str, Any] = {}


def _get(url: str, timeout: float = _TIMEOUT) -> bytes | None:
    """GET。404 は b"" （＝データ無し）、通信失敗は None（＝不明）で返す。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        if e.code == 404:
            return b""      # タイル無し＝その区域に指定なし
        return None         # それ以外のHTTPエラーは不明扱い
    except Exception:       # noqa: BLE001  タイムアウト・DNS等
        return None


# ── ジオコーディング ──────────────────────────────────────────
def geocode(address: str) -> dict[str, Any] | None:
    """住所 → {lat, lon, title}。失敗時 None。"""
    a = (address or "").strip()
    if not a:
        return None
    key = "geo:" + a
    if key in _cache:
        return _cache[key]
    url = ("https://msearch.gsi.go.jp/address-search/AddressSearch?q="
           + urllib.parse.quote(a))
    raw = _get(url)
    out = None
    if raw:
        try:
            arr = json.loads(raw.decode("utf-8"))
            if isinstance(arr, list) and arr:
                c = arr[0].get("geometry", {}).get("coordinates") or []
                if len(c) >= 2:
                    out = {
                        "lat": float(c[1]),
                        "lon": float(c[0]),
                        "title": (arr[0].get("properties") or {}).get("title") or a,
                    }
        except Exception:  # noqa: BLE001
            out = None
    _cache[key] = out
    return out


def reverse_geocode(lat: float, lon: float) -> dict[str, Any] | None:
    """経緯度 → {muniCd, name}。※用途地域等は返らない（GSI仕様）。"""
    url = ("https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress"
           f"?lat={lat}&lon={lon}")
    raw = _get(url)
    if not raw:
        return None
    try:
        res = (json.loads(raw.decode("utf-8")) or {}).get("results") or {}
        return {"muniCd": res.get("muniCd"), "name": res.get("lv01Nm")}
    except Exception:  # noqa: BLE001
        return None


# ── 最小PNGデコーダ（8bit RGBA / 非インターレース）─────────────
def _png_pixel(data: bytes, px: int, py: int) -> tuple[int, int, int, int] | None:
    """PNG バイト列から (px,py) の RGBA を取り出す。対応外形式は None。"""
    if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, w, h, ct, bd, idat = 8, 0, 0, 0, 0, b""
    while pos + 8 <= len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, bd, ct = struct.unpack(">IIBB", body[:10])
            interlace = body[12]
            if bd != 8 or ct != 6 or interlace != 0:
                return None          # 想定外の形式は判定しない（誤判定回避）
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        pos += 12 + ln
    if not idat or w == 0 or not (0 <= px < w and 0 <= py < h):
        return None
    try:
        raw = zlib.decompress(idat)
    except Exception:  # noqa: BLE001
        return None
    bpp, stride = 4, w * 4
    if len(raw) < (stride + 1) * (py + 1):
        return None
    # 目的行までスキャンライン復元（PNGフィルタは前行に依存するため順に解く）
    prev = bytearray(stride)
    cur = bytearray(stride)
    o = 0
    for y in range(py + 1):
        f = raw[o]
        o += 1
        line = raw[o:o + stride]
        o += stride
        cur = bytearray(line)
        if f == 1:
            for i in range(bpp, stride):
                cur[i] = (cur[i] + cur[i - bpp]) & 0xFF
        elif f == 2:
            for i in range(stride):
                cur[i] = (cur[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                a = cur[i - bpp] if i >= bpp else 0
                cur[i] = (cur[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = cur[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                cur[i] = (cur[i] + pr) & 0xFF
        prev = cur
    i = px * 4
    return (cur[i], cur[i + 1], cur[i + 2], cur[i + 3])


def _tile_and_pixel(lat: float, lon: float, z: int) -> tuple[int, int, int, int]:
    """経緯度 → (タイルX, タイルY, タイル内px, タイル内py)（Webメルカトル）。"""
    n = 2.0 ** z
    fx = (lon + 180.0) / 360.0 * n
    r = math.radians(lat)
    fy = (1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * n
    x, y = int(fx), int(fy)
    return x, y, min(int((fx - x) * 256), 255), min(int((fy - y) * 256), 255)


def _layer_hit(layer: str, lat: float, lon: float) -> dict[str, Any]:
    """1レイヤーの該当判定（ズームを下げながら実在タイルを探す）。

    returns: {"hit": True/False/None, "rgba": (...)|None, "zoom": int|None}
      True  = 区域内 / False = 区域外 / None = 判定不能（通信失敗・形式不明）
    """
    neterr = False
    for z in _ZOOMS:
        x, y, px, py = _tile_and_pixel(lat, lon, z)
        key = f"tile:{layer}:{z}:{x}:{y}"
        if key not in _cache:
            _cache[key] = _get(
                f"https://disaportaldata.gsi.go.jp/raster/{layer}/{z}/{x}/{y}.png")
        raw = _cache[key]
        if raw is None:
            neterr = True          # 通信失敗。下のズームも試すが確定はさせない
            continue
        if raw == b"":
            continue               # このズームにタイル無し → 下のズームへ
        rgba = _png_pixel(raw, px, py)
        if rgba is None:
            neterr = True
            continue
        return {"hit": bool(rgba[3] > 0), "rgba": rgba, "zoom": z}
    # 全ズームで取得できず: 通信失敗が絡むなら「不明」、純粋に404のみなら「区域外」
    return {"hit": None if neterr else False, "rgba": None, "zoom": None}


def _classify_depth(rgba: list[int] | None, legend: list) -> str | None:
    """RGBAからカラー凡例を参照して浸水深テキストを返す。"""
    if not rgba or rgba[3] < 30:
        return None
    r, g, b = rgba[0], rgba[1], rgba[2]
    best, best_d = None, 999.0
    for ref, label in legend:
        d = math.sqrt((r - ref[0])**2 + (g - ref[1])**2 + (b - ref[2])**2)
        if d < best_d:
            best_d = d
            best = label
    return best if best_d < 80 else None


def hazard(lat: float, lon: float, deadline: float | None = None) -> dict[str, Any]:
    """経緯度 → ハザード判定。値は True/False/None(=要確認)。

    deadline: 全体の締切（time.monotonic 基準の秒）。超過後のレイヤーは
    問い合わせず None（要確認）にする。外部API不調で書類生成が
    いつまでも返らないのを防ぐための安全弁。
    """
    out: dict[str, Any] = {"_source": "国土地理院 ハザードマップポータル"}
    for key, layer in HAZARD_LAYERS.items():
        if deadline is not None and time.monotonic() > deadline:
            out[key] = None
            out["_timeout"] = True
            continue
        r = _layer_hit(layer, lat, lon)
        out[key] = r["hit"]
        if r["zoom"]:
            out[key + "_zoom"] = r["zoom"]
        if r["rgba"]:
            out[key + "_rgba"] = list(r["rgba"])
    # 浸水深テキスト（重説記載用）
    for k in ("kozui", "naisui", "takashio", "tsunami"):
        rgba = out.get(k + "_rgba")
        depth = _classify_depth(rgba, FLOOD_DEPTH_LEGEND) if rgba else None
        if depth:
            out[k + "_depth"] = depth
    # 土砂分類テキスト
    for k in ("dosekiryu", "kyukeisha", "jisuberi"):
        rgba = out.get(k + "_rgba")
        cls = _classify_depth(rgba, DOSHA_LEGEND) if rgba else None
        if cls:
            out[k + "_class"] = cls
    # 土砂災害（土石流 or 急傾斜地 or 地すべり）のいずれかで警戒区域
    ds = [out.get("dosekiryu"), out.get("kyukeisha"), out.get("jisuberi")]
    out["dosha_keikai"] = True if any(v is True for v in ds) else (
        None if any(v is None for v in ds) else False)
    # 特別警戒区域（レッドゾーン）は色で判別（赤系＝特別警戒）
    out["dosha_tokubetsu"] = _is_red(out) if out["dosha_keikai"] else out["dosha_keikai"]
    for k in UNAVAILABLE:
        out[k] = None
    # ハザードマップURL（重説添付用）
    g = geocode(str(lat) + "," + str(lon))
    out["_map_url"] = f"https://disaportal.gsi.go.jp/hazardmap/maps/index.html?ll={lat},{lon}&z=14"
    return out


def _is_red(h: dict[str, Any]) -> bool | None:
    """土砂レイヤーの色から特別警戒区域(赤系)かを推定。判別不能は None。"""
    for k in ("dosekiryu", "kyukeisha", "jisuberi"):
        c = h.get(k + "_rgba")
        if not c:
            continue
        r, g, b = c[0], c[1], c[2]
        if r > 180 and g < 130 and b < 130:
            return True         # 赤系＝特別警戒区域
    return None                 # 黄系(警戒)か判別不能 → 断定しない


# ── 法令制限（推定）────────────────────────────────────────────
def horei_estimate(address: str, lat: float | None = None,
                   lon: float | None = None) -> dict[str, Any]:
    """住所（＋任意で経緯度）→ 用途地域・建蔽率・容積率の *推定値*。

    公的APIでは取得できないため、都道府県ベースの既定値を返す。
    必ず estimated=True が付く。利用側は「要確認」として扱うこと。
    """
    a = address or ""
    pref = next((p for p in _PREF_DEFAULT if p in a), None)
    base = dict(_PREF_DEFAULT.get(pref or "", _FALLBACK))
    muni = None
    if lat is not None and lon is not None:
        rg = reverse_geocode(lat, lon)
        if rg:
            muni = rg.get("name")
    return {
        **base,
        "estimated": True,
        "pref": pref,
        "muni": muni,
        "_note": ("用途地域・建蔽率・容積率は公的APIで取得できないため"
                  "都道府県既定値による推定です。必ず市区町村の都市計画課で確認してください。"),
    }


def lookup(address: str, budget: float = 12.0) -> dict[str, Any]:
    """住所 → {geo, horei, hazard}。UI/デバッグ用のまとめ取得。

    budget: 外部API全体に使ってよい秒数。超過分は「要確認(None)」で打ち切る。
    """
    deadline = time.monotonic() + max(float(budget), 1.0)
    g = geocode(address)
    if not g:
        return {
            "geo": None,
            "horei": horei_estimate(address),
            "hazard": {k: None for k in list(HAZARD_LAYERS) + list(UNAVAILABLE)},
            "warning": "住所から緯度経度を特定できませんでした（ハザードは要確認）。",
        }
    return {
        "geo": g,
        "horei": horei_estimate(address, g["lat"], g["lon"]),
        "hazard": hazard(g["lat"], g["lon"], deadline=deadline),
        "warning": "",
    }


def get_hazard_info(lat, lon):
    """国土地理院のハザードマップ情報を取得"""
    import requests
    result = {
        "kozui": None,  # 洪水
        "dosya": None,  # 土砂
        "tsunami": None,  # 津波
        "naisui": None,  # 内水
        "takashio": None,  # 高潮
    }
    
    try:
        # 洪水浸水想定
        z, x, y = _latlon_to_tile(lat, lon, 14)
        url = f"https://disaportaldata.gsi.go.jp/raster/01_flood_l2_shinsuishin_data/{z}/{x}/{y}.png"
        r = requests.head(url, timeout=5)
        result["kozui"] = r.status_code == 200
        
        # 土砂災害警戒区域
        url2 = f"https://disaportaldata.gsi.go.jp/raster/05_dosekiryukeikaikuiki/{z}/{x}/{y}.png"
        r2 = requests.head(url2, timeout=5)
        result["dosya"] = r2.status_code == 200
        
        # 津波浸水想定
        url3 = f"https://disaportaldata.gsi.go.jp/raster/04_tsunami_newlegend_data/{z}/{x}/{y}.png"
        r3 = requests.head(url3, timeout=5)
        result["tsunami"] = r3.status_code == 200
    except:
        pass
    
    return result

def _latlon_to_tile(lat, lon, zoom):
    """緯度経度からタイル座標を計算"""
    import math
    x = int((lon + 180) / 360 * (2 ** zoom))
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * (2 ** zoom))
    return zoom, x, y
