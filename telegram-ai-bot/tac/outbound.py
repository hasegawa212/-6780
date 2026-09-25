"""アウトバウンド発信（click-to-call ブリッジ）。

使い方の流れ:
  1. あなたが 1 件、相手の番号を指定して発信をトリガー（POST /tac/call）
  2. 相手に発信 → 出たら Conference に入り「保留（ホールド音）」で待つ
  3. 同時にあなたの電話（TAC_AGENT_NUMBER）が鳴る
  4. あなたが出た瞬間に Conference が開始 → 相手の保留が解け、会話が始まる

Twilio の Conference を使う:
  - 相手側 : startConferenceOnEnter=false → 先に出ても会議は始まらず保留音のまま待つ
  - あなた : startConferenceOnEnter=true, endConferenceOnExit=true
            → あなたが入ると会議開始（相手と接続）、あなたが切ると通話終了

※ 意図的に「1 件ずつ手動発信（click-to-call）」だけを提供する。リストの一斉自動
  発信（オートダイヤラー）や、断った相手への再架電は実装しない。
  法令順守（米国 TCPA / 日本 特定商取引法の勧誘目的明示義務・再勧誘の禁止）は
  運用側の責任。冒頭で正直に名乗り、拒否されたら再発信しないこと。

依存なし（標準ライブラリの urllib のみ）。Twilio REST の Calls API を叩く。
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .config import CONFIG

_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"


def _conf_twiml(room: str, *, starter: bool) -> str:
    """Conference に参加する TwiML。starter=あなた側（会議を開始する）。"""
    if starter:
        # あなた（担当者）: 参加で会議開始、退出で通話終了
        conf = (
            f'<Conference startConferenceOnEnter="true" '
            f'endConferenceOnExit="true" beep="false">{room}</Conference>'
        )
    else:
        # 相手: 会議が始まるまで保留音で待機（先に出ても始まらない）
        conf = (
            f'<Conference startConferenceOnEnter="false" '
            f'endConferenceOnExit="false" beep="false">{room}</Conference>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Dial>{conf}</Dial></Response>"
    )


def _hangup_call(sid: str | None) -> None:
    """進行中/呼び出し中の通話を終了する（保留のまま放置しないため）。"""
    account = CONFIG.twilio_account_sid
    token = CONFIG.twilio_auth_token
    if not (account and token and sid):
        return
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{account}/Calls/{sid}.json"
    )
    data = urllib.parse.urlencode({"Status": "completed"}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    auth = base64.b64encode(f"{account}:{token}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except Exception:  # noqa: BLE001 - 後始末なので失敗しても握り潰す
        pass


def _create_call(*, to: str, twiml: str) -> dict:
    """Twilio Calls API で 1 本発信する（TwiML インラインで指定）。"""
    sid = CONFIG.twilio_account_sid
    token = CONFIG.twilio_auth_token
    if not (sid and token):
        return {"ok": False, "error": "TWILIO_ACCOUNT_SID/AUTH_TOKEN 未設定"}
    if not CONFIG.caller_id:
        return {"ok": False, "error": "TAC_CALLER_ID（発信元 Twilio 番号）未設定"}

    data = urllib.parse.urlencode(
        {"To": to, "From": CONFIG.caller_id, "Twiml": twiml}
    ).encode()
    req = urllib.request.Request(_API.format(sid=sid), data=data, method="POST")
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
        return {"ok": True, "sid": body.get("sid"), "to": to, "status": body.get("status")}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        return {"ok": False, "error": f"HTTP {e.code}: {detail}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def bridge_call(to: str, *, agent: str | None = None) -> dict:
    """1 件だけ発信して担当者につなぐ（相手は接続まで保留）。

    to    : かける相手の番号（E.164, 例 +81... / +1...）
    agent : 担当者（あなた）の番号。省略時は CONFIG.agent_number。
    戻り値: 両レッグの発信結果と会議名。
    """
    agent = agent or CONFIG.agent_number
    if not to:
        return {"ok": False, "error": "発信先 to が空です"}
    if not agent:
        return {"ok": False, "error": "担当者番号（TAC_AGENT_NUMBER か agent 引数）未設定"}

    room = f"tac-{uuid.uuid4().hex[:12]}"
    # 相手を先に発信（出たら保留音で待機）
    target_leg = _create_call(to=to, twiml=_conf_twiml(room, starter=False))
    if not target_leg.get("ok"):
        return {"ok": False, "stage": "target", "room": room, **target_leg}
    # あなたを発信（出た瞬間に会議開始＝相手と接続）
    agent_leg = _create_call(to=agent, twiml=_conf_twiml(room, starter=True))
    if not agent_leg.get("ok"):
        # 担当者レッグが失敗した場合、相手を保留のまま（課金継続・会議開始不能）に
        # しないよう、必ずターゲットのレッグを終了する。
        _hangup_call(target_leg.get("sid"))
        return {"ok": False, "stage": "agent", "room": room,
                "target_leg": target_leg, "target_hung_up": True, **agent_leg}
    return {"ok": True, "room": room, "target_leg": target_leg, "agent_leg": agent_leg}


if __name__ == "__main__":
    # 使い方: python -m tac.outbound +81901234567 [担当者番号]
    import sys

    if len(sys.argv) < 2:
        print("使い方: python -m tac.outbound <相手番号> [担当者番号]")
        print("例:     python -m tac.outbound +81901234567")
        raise SystemExit(1)
    _to = sys.argv[1]
    _agent = sys.argv[2] if len(sys.argv) > 2 else None
    print(json.dumps(bridge_call(_to, agent=_agent), ensure_ascii=False, indent=2))
