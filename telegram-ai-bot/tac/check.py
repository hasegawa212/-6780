"""TAC 接続チェッカー（設定の健全性診断 + 任意のライブ疎通確認）。

  python -m tac.check          # 設定状況だけ表示（ネットワーク呼び出しなし）
  python -m tac.check --live   # Twilio へ実際に問い合わせて認証/番号/フローを検証

秘密の値は常にマスクして表示する（フルの Auth Token / Secret は出力しない）。
ライブ検証は環境変数から読んだ認証情報のみを使い、引数では受け取らない。
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from .config import CONFIG


def mask(secret: str, *, head: int = 4, tail: int = 4) -> str:
    """秘密を 'AC6a…bb98' のようにマスク。短すぎる場合は全伏字。"""
    if not secret:
        return "(未設定)"
    if len(secret) <= head + tail:
        return "•" * len(secret)
    return f"{secret[:head]}…{secret[-tail:]}"


def status_report() -> dict:
    """設定状況（秘密はマスク）。ネットワーク呼び出しなし。"""
    return {
        "llm": {
            "anthropic_key": mask(CONFIG.anthropic_key),
            "model": CONFIG.model,
            "configured": bool(CONFIG.anthropic_key),
        },
        "twilio": {
            "account_sid": mask(CONFIG.twilio_account_sid),
            "auth_token": mask(CONFIG.twilio_auth_token),
            "handoff_from": CONFIG.handoff_from or "(未設定)",
            "conversations_service_sid": mask(CONFIG.conversations_service_sid),
            "studio_handoff_flow_sid": mask(CONFIG.studio_handoff_flow_sid),
            "configured": CONFIG.has_twilio,
        },
        "memory": {
            "supabase_url": CONFIG.supabase_url or "(未設定)",
            "service_key": mask(CONFIG.supabase_service_key),
            "openai_key": mask(CONFIG.openai_key),
            "configured": CONFIG.has_memory,
        },
        "dry_run": CONFIG.dry_run,
    }


def preflight() -> dict:
    """本番 go-live 準備チェック（ネットワーク呼び出しなし）。

    実顧客に発信して安全かを一発判定する。creds・発信元番号(03)・担当者名簿・
    安全ゲートを点検し、critical が全て通れば go_live=True、推奨も全て通れば
    ready_for_production=True。秘密の実値（番号・トークン）は出さず、真偽と件数のみ。

    - critical : 欠けると「発信できない／事故る」もの
    - recommended : 本番コンプラ・安全運用で ON が望ましいもの
    """
    from . import agents

    roster = agents.roster()
    checks: list[dict] = []

    def add(key: str, label: str, level: str, ok: bool, detail: str) -> None:
        checks.append({"key": key, "label": label, "level": level,
                       "ok": bool(ok), "detail": detail})

    # --- CRITICAL ---
    add("twilio_creds", "Twilio認証(SID/AuthToken)", "critical",
        CONFIG.has_twilio, "設定済み" if CONFIG.has_twilio else "未設定")
    add("caller_id", "発信元番号(TAC_CALLER_ID)", "critical",
        bool(CONFIG.caller_id),
        "設定済み" if CONFIG.caller_id else "未設定（03番号の承認待ち？）")
    add("outbound_token", "API保護トークン(TAC_OUTBOUND_TOKEN)", "critical",
        bool(CONFIG.outbound_token),
        "設定済み" if CONFIG.outbound_token else "未設定（発信APIは無効＝誰も掛けられない）")
    add("agent_roster", "担当者名簿(TAC_AGENTS)", "critical",
        len(roster) > 0, f"{len(roster)}名" if roster else "空（取り次ぎ先なし）")

    # --- RECOMMENDED ---
    add("call_hours", "発信時間帯ガード(TAC_ENFORCE_CALL_HOURS)", "recommended",
        CONFIG.enforce_call_hours, "ON" if CONFIG.enforce_call_hours else "OFF")
    add("daily_cap", "1日発信上限(TAC_DAILY_CALL_CAP)", "recommended",
        CONFIG.daily_call_cap > 0,
        f"{CONFIG.daily_call_cap}件" if CONFIG.daily_call_cap > 0 else "0（無制限）")
    add("twilio_signature", "Webhook署名検証(TAC_VERIFY_TWILIO_SIGNATURE)", "recommended",
        CONFIG.verify_twilio_signature, "ON" if CONFIG.verify_twilio_signature else "OFF")
    add("disclosure", "冒頭ディスクロージャー(TAC_DISCLOSURE_ENABLED)", "recommended",
        CONFIG.disclosure_enabled, "ON" if CONFIG.disclosure_enabled else "OFF")

    critical_fail = sum(1 for c in checks if c["level"] == "critical" and not c["ok"])
    recommended_fail = sum(1 for c in checks if c["level"] == "recommended" and not c["ok"])
    return {
        "go_live": critical_fail == 0,
        "ready_for_production": critical_fail == 0 and recommended_fail == 0,
        "summary": {
            "critical_fail": critical_fail,
            "recommended_fail": recommended_fail,
            "total": len(checks),
        },
        "checks": checks,
    }


def _twilio_get(url: str) -> tuple[bool, dict | str]:
    auth = base64.b64encode(
        f"{CONFIG.twilio_account_sid}:{CONFIG.twilio_auth_token}".encode()
    ).decode()
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Basic {auth}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return True, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} {e.reason}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def live_check() -> dict:
    """Twilio へ実問い合わせ。認証・電話番号・Studio フローの有効性を確認。"""
    if not CONFIG.has_twilio:
        return {"ok": False, "error": "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN が未設定"}

    results: dict = {}

    # 1) 認証検証: アカウント取得
    ok, data = _twilio_get(
        f"https://api.twilio.com/2010-04-01/Accounts/{CONFIG.twilio_account_sid}.json"
    )
    results["auth"] = (
        {"ok": True, "friendly_name": data.get("friendly_name"), "status": data.get("status")}
        if ok else {"ok": False, "error": data}
    )

    # 2) 電話番号がアカウントに属するか
    if CONFIG.handoff_from:
        q = urllib.parse.quote(CONFIG.handoff_from)
        ok, data = _twilio_get(
            f"https://api.twilio.com/2010-04-01/Accounts/{CONFIG.twilio_account_sid}"
            f"/IncomingPhoneNumbers.json?PhoneNumber={q}"
        )
        if ok:
            nums = data.get("incoming_phone_numbers", [])
            results["phone_number"] = {"ok": bool(nums), "found": len(nums)}
        else:
            results["phone_number"] = {"ok": False, "error": data}

    # 3) Studio ハンドオフフローの存在確認
    if CONFIG.studio_handoff_flow_sid:
        ok, data = _twilio_get(
            f"https://studio.twilio.com/v2/Flows/{CONFIG.studio_handoff_flow_sid}"
        )
        results["studio_flow"] = (
            {"ok": True, "friendly_name": data.get("friendly_name"), "status": data.get("status")}
            if ok else {"ok": False, "error": data}
        )

    results["ok"] = all(v.get("ok") for v in results.values() if isinstance(v, dict))
    return results


def _main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="TAC の設定診断とライブ疎通確認")
    ap.add_argument("--live", action="store_true",
                    help="Twilio へ実際に問い合わせて検証する（環境変数の認証情報を使用）")
    ap.add_argument("--preflight", action="store_true",
                    help="本番 go-live 準備チェック（creds・発信元番号・名簿・安全ゲート）")
    args = ap.parse_args(argv)

    if args.preflight:
        rep = preflight()
        print("=== go-live プリフライト点検 ===")
        for c in rep["checks"]:
            mark = "✅" if c["ok"] else ("🔴" if c["level"] == "critical" else "🟡")
            print(f"{mark} [{c['level']:>11}] {c['label']}: {c['detail']}")
        print(f"\ngo_live(本番発信OK)={rep['go_live']} / "
              f"ready_for_production(完全体)={rep['ready_for_production']}")
        return 0 if rep["go_live"] else 1

    print("=== TAC 設定状況（秘密はマスク表示）===")
    print(json.dumps(status_report(), ensure_ascii=False, indent=2))

    if args.live:
        print("\n=== ライブ疎通確認（Twilio）===")
        print(json.dumps(live_check(), ensure_ascii=False, indent=2))
    else:
        print("\n（--live を付けると Twilio へ実際に問い合わせます）")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
