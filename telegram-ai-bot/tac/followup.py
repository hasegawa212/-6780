"""自動フォロー（分類・台帳）。

設計図「自動フォロー｜情報連携・AI分類設計」/「訪問営業｜自動フォロー設計図」の
バックエンド中核。Slack・Drive・Sheets 等から読み込んだ「お客様の記録」を、
内容で分類（再調整希望／日程返答待ち／不在／要確認／連絡停止）して台帳に貯め、
出典（元の記録）と最終更新を保存する。発信対象は、既存の DNC（拒否）・発信時間帯・
1日上限に加えて「合計フォロー回数の上限」「重複登録しない」を守る。

既存の仕組みは壊さない（すべて追加）:
- 拒否          → 既存 dnc モジュールをそのまま使う
- 時間帯/1日上限 → 既存 calling_hours / rate_limit をそのまま使う
- 発信リスト     → 既存 queue にフォロー確定分だけを流し込む（promote）

重要: 元の記録テキストは「データ」として扱い、命令として実行しない（分類のみ）。
"""

from __future__ import annotations

import json
import os
import threading
import uuid

from . import dnc, phone, queue
from .config import CONFIG

_lock = threading.Lock()

# 分類カテゴリ（設計のタブ順）
CATEGORIES = ("再調整希望", "日程返答待ち", "不在", "要確認", "連絡停止")
# 自動発信の候補になるのはこの3つだけ。要確認は担当者へ、連絡停止は対象外。
CALLABLE = ("再調整希望", "日程返答待ち", "不在")

# カテゴリ別の既定スコア（キュー投入時の優先度）
_SCORE = {"再調整希望": 90, "日程返答待ち": 80, "不在": 70}

# 次の対応（設計の「次の対応」列）
_NEXT_ACTION = {
    "再調整希望": "候補日を確認",
    "日程返答待ち": "返答を確認",
    "不在": "再架電",
    "要確認": "担当者が最新状況を確認",
    "連絡停止": "自動発信対象外",
}

# 分類キーワード（優先度の高い順に判定する）
_STOP = ("拒否", "いらない", "要らない", "必要ない", "連絡しない", "連絡は希望しない",
         "今後の連絡を希望しない", "来るな", "来なくて", "もう来", "警察", "着信拒否",
         "断り", "お断り", "興味ない", "結構です", "やめと", "やめて")
_MISMATCH = ("別人", "番号相違", "番号違", "違う人", "人違い", "記録が古い", "内容が不一致",
             "不一致", "矛盾", "古い記録", "本人か不明", "本人不明")
_RESCHEDULE = ("リスケ", "再調整", "日程変更", "日程を変更", "別日", "組み直", "予定変更",
               "日程調整", "調整したい", "日取り")
_WAITING = ("返答待ち", "返事待ち", "日程返答", "日程待ち", "候補日", "確認中", "検討",
            "返信待ち", "回答待ち", "返答を待")
_ABSENT = ("不在", "留守", "出ない", "応答なし", "応答が", "折り返しなし", "でなかった",
           "出なかった", "繋がらない", "つながらない", "応答無し")


def classify(text: str, status_hint: str = "") -> tuple[str, str]:
    """記録テキスト（＋任意のステータス）を内容で分類する。

    戻り値 (category, basis)。basis は判定の根拠（マッチした語）。
    拒否（連絡停止）が最優先。曖昧なら要確認（勝手に発信しない安全側）。
    """
    t = f"{text or ''} {status_hint or ''}"

    def _hit(words: tuple[str, ...]) -> str:
        for w in words:
            if w in t:
                return w
        return ""

    w = _hit(_STOP)
    if w:
        return "連絡停止", w
    w = _hit(_MISMATCH)
    if w:
        return "要確認", w
    w = _hit(_RESCHEDULE)
    if w:
        return "再調整希望", w
    w = _hit(_WAITING)
    if w:
        return "日程返答待ち", w
    w = _hit(_ABSENT)
    if w:
        return "不在", w
    return "要確認", "根拠が曖昧"


def _dedup_key(item: dict, number_e164: str) -> str:
    src = item.get("source") or {}
    ref = src.get("ref")
    if ref:
        return f"ref:{ref}"
    return f"num:{number_e164 or item.get('number', '')}|rec:{(item.get('record') or '')[:40]}"


def ingest(records: list[dict]) -> dict:
    """記録のリストを分類して台帳へ取り込む。重複・拒否・番号相違を安全に処理する。

    戻り値: {"added": n, "skipped": m, "reasons": {...}}
    """
    added = 0
    skipped = 0
    reasons: dict[str, int] = {}

    def _bump(k: str) -> None:
        reasons[k] = reasons.get(k, 0) + 1

    with _lock:
        entries = _read()
        seen = {e.get("dedup_key") for e in entries}
        for item in records:
            raw = str(item.get("number") or "").strip()
            e164 = phone.to_e164(raw) or ""
            key = _dedup_key(item, e164)
            if key in seen:
                skipped += 1
                _bump("duplicate")
                continue

            record = str(item.get("record") or "")  # 元の記録（データ扱い）
            category, basis = classify(record, str(item.get("status") or ""))
            consent = ""
            eligible = True

            # 電話番号が取れない → 発信できないので要確認
            if not e164:
                category, basis = "要確認", "電話番号が不明"
                eligible = False
            # 拒否済み（DNC）は最優先で連絡停止・対象外（再び対象にしない）
            elif dnc.contains(e164):
                category, basis = "連絡停止", "発信禁止リスト登録済み"
                consent = "拒否"
                eligible = False

            if category in ("要確認", "連絡停止"):
                eligible = False

            entry = {
                "id": uuid.uuid4().hex[:8],
                "name": str(item.get("name") or ""),
                "number": e164 or raw,
                "number_raw": raw,
                "area": str(item.get("area") or ""),
                "assignee": str(item.get("assignee") or ""),
                "category": category,
                "basis": basis,
                "next_action": _NEXT_ACTION.get(category, ""),
                "record": record,
                "source": item.get("source") or {},
                "updated_at": str(item.get("updated_at") or ""),
                "follow_count": 0,
                "last_follow_at": "",
                "consent": consent,
                "eligible": eligible,
                "dedup_key": key,
            }
            entries.append(entry)
            seen.add(key)
            added += 1

        _write(entries)
    return {"added": added, "skipped": skipped, "reasons": reasons}


def load(category: str = "") -> list[dict]:
    """台帳を読み込む。category 指定でそのカテゴリだけ。"""
    entries = _read()
    if category:
        entries = [e for e in entries if e.get("category") == category]
    return entries


def counts() -> dict:
    """カテゴリ別の件数（設計のタブのバッジ用）。"""
    out = {c: 0 for c in CATEGORIES}
    for e in _read():
        c = e.get("category")
        if c in out:
            out[c] += 1
    return out


def can_follow(entry: dict, cap: int | None = None) -> bool:
    """この相手に自動フォロー発信してよいか（全ガード込み）。"""
    cap = CONFIG.follow_cap if cap is None else cap
    if entry.get("category") not in CALLABLE:
        return False
    if entry.get("consent") == "拒否":
        return False
    num = entry.get("number") or ""
    if not num.startswith("+"):
        return False
    if dnc.contains(num):
        return False
    if cap > 0 and int(entry.get("follow_count", 0)) >= cap:
        return False
    return True


def correct(entry_id: str, category: str) -> bool:
    """分類を手動修正する（担当者の確認後）。"""
    if category not in CATEGORIES:
        return False
    with _lock:
        entries = _read()
        for e in entries:
            if e.get("id") == entry_id:
                e["category"] = category
                e["next_action"] = _NEXT_ACTION.get(category, e.get("next_action", ""))
                e["eligible"] = category in CALLABLE and e.get("consent") != "拒否"
                _write(entries)
                return True
    return False


def record_follow(number: str) -> int:
    """フォロー発信を1回記録する（該当番号の follow_count を +1）。更新件数を返す。"""
    from datetime import datetime, timezone

    e164 = phone.to_e164(number) or dnc.normalize(number)
    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    with _lock:
        entries = _read()
        for e in entries:
            if e.get("number") == e164 or e.get("number_raw") == number:
                e["follow_count"] = int(e.get("follow_count", 0)) + 1
                e["last_follow_at"] = now
                updated += 1
        if updated:
            _write(entries)
    return updated


def promote(ids: list[str]) -> int:
    """「確認済みをフォロー予定へ」。発信可の相手だけを既存スマートリストへ流し込む。

    既にキューにある番号は重複追加しない。追加件数を返す。
    """
    want = set(ids)
    entries = [e for e in _read() if e.get("id") in want and can_follow(e)]
    if not entries:
        return 0
    existing = {q.get("number") for q in queue.load()}
    items = []
    for e in entries:
        if e["number"] in existing:
            continue
        items.append({
            "number": e["number"],
            "name": e.get("name", ""),
            "area": e.get("area", ""),
            "score": _SCORE.get(e["category"], 60),
            "note": f'{e["category"]}／{e.get("basis", "")}',
        })
        existing.add(e["number"])
    if items:
        queue.add_bulk(items)
    return len(items)


def _read() -> list[dict]:
    try:
        with open(CONFIG.follow_file, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write(entries: list[dict]) -> None:
    path = CONFIG.follow_file
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
