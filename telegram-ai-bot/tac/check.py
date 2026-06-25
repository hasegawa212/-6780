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
    args = ap.parse_args(argv)

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
