"""Manus AI クライアント — 登記情報提供サービス + レインズ のブラウザ自動操作.

重説アプリ（BC Pipeline）から呼び出し、Manus AI に登記簿やレインズの
検索・取得を依頼する。結果は JSON で返す。

利用時間:
  登記情報提供サービス: 平日 8:30-21:00 / 土日祝 8:30-18:00
  レインズ: 24時間
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

MANUS_API = "https://api.manus.ai/v2"

_KEYS_FILE = Path(os.environ.get(
    "MANUS_KEYS_FILE",
    Path.home() / ".openclaw/workspace/notes/company-credentials.md",
))

_TOUKI_ID = os.environ.get("TOUKI_SERVICE_ID", "ABHO8172")
_TOUKI_PW = os.environ.get("TOUKI_SERVICE_PW", "MartialArts082")

_REINS_ID = os.environ.get("REINS_ID", "770000074370")
_REINS_PW = os.environ.get("REINS_PW", "uw4t3e")

_keys_cache: list[str] | None = None


def _load_keys() -> list[str]:
    global _keys_cache
    if _keys_cache is not None:
        return _keys_cache

    env_key = os.environ.get("MANUS_API_KEY", "")
    if env_key:
        _keys_cache = [k.strip() for k in env_key.split(",") if k.strip()]
        if _keys_cache:
            return _keys_cache

    keys: list[str] = []
    if _KEYS_FILE.exists():
        in_manus = False
        for line in _KEYS_FILE.read_text().splitlines():
            if "## Manus AI" in line:
                in_manus = True
                continue
            if in_manus and line.strip().startswith("## "):
                break
            if in_manus and line.strip().startswith(("1.", "2.", "3.", "4.")):
                parts = line.strip().split(". ", 1)
                if len(parts) == 2:
                    key = parts[1].split(" ")[0].strip()
                    if key.startswith("sk-"):
                        keys.append(key)
    _keys_cache = keys
    return keys


def _api_post(endpoint: str, data: dict, api_key: str) -> dict:
    url = f"{MANUS_API}/{endpoint}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "x-manus-api-key": api_key,
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": {"code": str(e.code), "message": e.read().decode() if e.fp else ""}}


def _api_get(endpoint: str, api_key: str) -> dict:
    url = f"{MANUS_API}/{endpoint}"
    req = urllib.request.Request(url, headers={"x-manus-api-key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": {"code": str(e.code), "message": e.read().decode() if e.fp else ""}}


def _select_key() -> str | None:
    keys = _load_keys()
    if not keys:
        return None
    for key in keys:
        try:
            r = _api_get("task.list", key)
            if r.get("ok"):
                return key
        except Exception:
            continue
    return keys[0]


def _wait(task_id: str, api_key: str, max_wait: int = 600) -> dict | None:
    start = time.time()
    while time.time() - start < max_wait:
        r = _api_get("task.list", api_key)
        if not r.get("ok"):
            time.sleep(10)
            continue
        for t in r.get("data", []):
            if t.get("id") == task_id:
                if t.get("status") in ("completed", "stopped", "failed"):
                    return t
                break
        time.sleep(15)
    return None


def _get_result(task_id: str, api_key: str) -> str:
    r = _api_get(f"task.listMessages?task_id={task_id}&limit=50&order=asc", api_key)
    msgs = []
    for m in r.get("data", r.get("messages", [])):
        if m.get("type") == "assistant_message":
            c = m.get("assistant_message", {}).get("content", "")
            if c:
                msgs.append(c)
    return msgs[-1] if msgs else ""


def fetch_touki(address: str, prop_type: str = "both", wait: bool = True) -> dict[str, Any]:
    """登記情報提供サービスからManusで登記情報を取得する."""
    api_key = _select_key()
    if not api_key:
        return {"ok": False, "error": "Manus APIキーが見つかりません"}

    type_label = {"land": "土地", "building": "建物"}.get(prop_type, "土地・建物")

    prompt = f"""登記情報提供サービス（https://www1.touki.or.jp/）にアクセスして、
以下の不動産の登記情報を取得してください。

【ログイン情報】
ID: {_TOUKI_ID}
パスワード: {_TOUKI_PW}

【検索対象】
住所: {address}
種別: {type_label}

【手順】
1. https://www1.touki.or.jp/ にアクセス
2. 上記IDとパスワードでログイン
3. 「不動産」を選択
4. 住所で検索（「{address}」）
5. {type_label}の登記情報を表示
6. 以下の情報を全て取得して報告:
   - 所在・地番
   - 地目（土地の場合）/ 種類（建物の場合）
   - 地積（土地の場合）/ 床面積（建物の場合）
   - 構造（建物の場合）
   - 所有者（甲区）: 氏名、住所、取得原因・日付
   - 抵当権・根抵当権（乙区）: 債権額、利息、債務者、抵当権者
   - 差押え・仮登記等があれば全て記載
   - 共同担保目録があれば記載
   - 不動産番号

【出力形式】
最後に以下のJSON形式で出力してください（テキスト報告の後に）:
```json
{{
  "shozai": "所在",
  "chiban": "地番",
  "chimoku": "地目",
  "chiseki": "地積（㎡）",
  "shurui": "種類（建物）",
  "kozo": "構造",
  "menseki": "床面積（㎡）",
  "chiku_date": "築年月",
  "shoyusha": "所有者氏名",
  "shoyusha_jusho": "所有者住所",
  "teitoken": [
    {{"saiken_gaku": "債権額", "risoku": "利息", "saimusha": "債務者", "teitokensha": "抵当権者"}}
  ],
  "sashiosae": "差押えの有無と内容",
  "fudosan_bango": "不動産番号"
}}
```

【重要】
- 人間のように自然な速度で操作すること
- エラーが出たら無理に繰り返さず報告すること
- 取得した情報は省略せず全て報告すること
"""

    r = _api_post("task.create", {"message": {"content": prompt}}, api_key)
    if not r.get("ok") and not r.get("task_id"):
        return {"ok": False, "error": f"タスク作成失敗: {json.dumps(r, ensure_ascii=False)}"}

    task_id = r.get("task_id", "")
    task_url = r.get("task_url", "")

    if not wait:
        return {"ok": True, "task_id": task_id, "task_url": task_url, "status": "running"}

    task = _wait(task_id, api_key)
    if not task:
        return {"ok": False, "task_id": task_id, "task_url": task_url, "error": "タイムアウト（10分）"}

    result_text = _get_result(task_id, api_key)
    parsed = _try_parse_json(result_text)

    return {
        "ok": True,
        "task_id": task_id,
        "task_url": task_url,
        "status": task.get("status"),
        "credit_usage": task.get("credit_usage", 0),
        "result_text": result_text,
        "parsed": parsed,
    }


def fetch_reins(address: str, wait: bool = True) -> dict[str, Any]:
    """レインズからManusで成約事例・売出情報を取得する."""
    api_key = _select_key()
    if not api_key:
        return {"ok": False, "error": "Manus APIキーが見つかりません"}

    prompt = f"""レインズ（REINS: Real Estate Information Network System）にアクセスして、
以下の物件周辺の成約事例・売出情報を検索してください。

【ログイン情報】
URL: https://system.reins.jp/
ログインID: {_REINS_ID}
パスワード: {_REINS_PW}

【検索対象】
住所: {address}

【手順】
1. https://system.reins.jp/ にアクセスしてログイン
2. 「成約事例検索」で上記住所の周辺（同一町名 or 同一市区町村）を検索
3. 種別: 中古戸建 / 土地
4. 成約事例を最大10件取得
5. 次に「売出中物件検索」で同エリアの売出中物件も検索

【取得する情報】
成約事例:
- 所在地、価格（万円）、土地面積、建物面積、築年数、成約時期
- 坪単価、㎡単価

売出中物件:
- 所在地、価格（万円）、土地面積、建物面積、築年数

【出力形式】
最後に以下のJSON形式で出力してください:
```json
{{
  "seiyaku": [
    {{
      "shozai": "所在地",
      "kakaku_man": 価格万円,
      "tochi_sqm": 土地面積,
      "tatemono_sqm": 建物面積,
      "chikunen": 築年数,
      "seiyaku_jiki": "成約時期",
      "tsubo_tanka_man": 坪単価万円
    }}
  ],
  "uridashi": [
    {{
      "shozai": "所在地",
      "kakaku_man": 価格万円,
      "tochi_sqm": 土地面積,
      "tatemono_sqm": 建物面積,
      "chikunen": 築年数
    }}
  ],
  "summary": "エリア相場の要約（1〜2文）"
}}
```

【重要】
- 人間のように自然な速度で操作すること（急ぎすぎない）
- エラーが出たら無理に繰り返さず報告すること
- 取得した情報は省略せず全て報告すること
"""

    r = _api_post("task.create", {"message": {"content": prompt}}, api_key)
    if not r.get("ok") and not r.get("task_id"):
        return {"ok": False, "error": f"タスク作成失敗: {json.dumps(r, ensure_ascii=False)}"}

    task_id = r.get("task_id", "")
    task_url = r.get("task_url", "")

    if not wait:
        return {"ok": True, "task_id": task_id, "task_url": task_url, "status": "running"}

    task = _wait(task_id, api_key, max_wait=600)
    if not task:
        return {"ok": False, "task_id": task_id, "task_url": task_url, "error": "タイムアウト（10分）"}

    result_text = _get_result(task_id, api_key)
    parsed = _try_parse_json(result_text)

    return {
        "ok": True,
        "task_id": task_id,
        "task_url": task_url,
        "status": task.get("status"),
        "credit_usage": task.get("credit_usage", 0),
        "result_text": result_text,
        "parsed": parsed,
    }


def fetch_corporate_touki(company_name: str, wait: bool = True) -> dict[str, Any]:
    """登記情報提供サービスから法人（会社）の登記簿謄本を取得する."""
    api_key = _select_key()
    if not api_key:
        return {"ok": False, "error": "Manus APIキーが見つかりません"}

    prompt = f"""登記情報提供サービス（https://www1.touki.or.jp/）にアクセスして、
以下の法人の登記情報（商業・法人登記）を取得してください。

【ログイン情報】
ID: {_TOUKI_ID}
パスワード: {_TOUKI_PW}

【検索対象】
法人名: {company_name}

【手順】
1. https://www1.touki.or.jp/ にアクセス
2. 上記IDとパスワードでログイン
3. 「商業・法人」を選択
4. 会社名で検索（「{company_name}」）
5. 該当する法人の登記情報を表示
6. 以下の情報を全て取得して報告:
   - 商号（会社名）
   - 本店所在地
   - 法人番号
   - 会社成立の年月日
   - 目的（事業内容）
   - 発行可能株式総数
   - 発行済株式の総数
   - 資本金の額
   - 役員に関する事項（取締役・代表取締役・監査役の氏名・住所・就任日）
   - 取締役会設置会社かどうか
   - 監査役設置会社かどうか
   - 登記記録に関する事項

【出力形式】
最後に以下のJSON形式で出力してください（テキスト報告の後に）:
```json
{{
  "shogo": "商号（会社名）",
  "honten": "本店所在地",
  "hojin_bango": "法人番号",
  "seiritsu_date": "会社成立年月日",
  "mokuteki": ["目的1", "目的2"],
  "hakko_kanou_kabushiki": "発行可能株式総数",
  "hakko_zumi_kabushiki": "発行済株式の総数",
  "shihonkin": "資本金の額",
  "yakuin": [
    {{"yakushoku": "代表取締役", "shimei": "氏名", "jusho": "住所", "shunin_date": "就任日"}}
  ],
  "torishimariyakukai": "設置/非設置",
  "kansayaku": "設置/非設置"
}}
```

【重要】
- 人間のように自然な速度で操作すること
- エラーが出たら無理に繰り返さず報告すること
- 取得した情報は省略せず全て報告すること
"""

    r = _api_post("task.create", {"message": {"content": prompt}}, api_key)
    if not r.get("ok") and not r.get("task_id"):
        return {"ok": False, "error": f"タスク作成失敗: {json.dumps(r, ensure_ascii=False)}"}

    task_id = r.get("task_id", "")
    task_url = r.get("task_url", "")

    if not wait:
        return {"ok": True, "task_id": task_id, "task_url": task_url, "status": "running"}

    task = _wait(task_id, api_key)
    if not task:
        return {"ok": False, "task_id": task_id, "task_url": task_url, "error": "タイムアウト（10分）"}

    result_text = _get_result(task_id, api_key)
    parsed = _try_parse_json(result_text)

    return {
        "ok": True,
        "task_id": task_id,
        "task_url": task_url,
        "status": task.get("status"),
        "credit_usage": task.get("credit_usage", 0),
        "result_text": result_text,
        "parsed": parsed,
    }


def check_task(task_id: str) -> dict[str, Any]:
    """進行中タスクのステータスを確認する."""
    api_key = _select_key()
    if not api_key:
        return {"ok": False, "error": "Manus APIキーが見つかりません"}

    r = _api_get("task.list", api_key)
    if not r.get("ok"):
        return {"ok": False, "error": "タスク一覧取得失敗"}

    for t in r.get("data", []):
        if t.get("id") == task_id:
            result_text = ""
            if t.get("status") in ("completed", "stopped"):
                result_text = _get_result(task_id, api_key)
            return {
                "ok": True,
                "task_id": task_id,
                "status": t.get("status"),
                "credit_usage": t.get("credit_usage", 0),
                "result_text": result_text,
                "parsed": _try_parse_json(result_text) if result_text else None,
            }

    return {"ok": False, "error": "タスクが見つかりません"}


def _try_parse_json(text: str) -> dict | None:
    """Manusの応答テキストからJSON部分を抽出する."""
    if not text:
        return None
    import re
    m = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{[^{}]*\"shozai\"[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{[^{}]*\"seiyaku\"[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None
