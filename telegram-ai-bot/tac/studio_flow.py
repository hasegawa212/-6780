"""ハンドオフ先 Studio フロー（Agent Handoff テンプレート相当）の生成と登録。

設計図2の「スタジオフローを設定」を、コンソール手作業ではなくコードで再現する。
`handoff.py` の `_trigger_studio()` が起動する Studio Flow の定義（JSON）を生成し、
Twilio REST API で作成/更新・公開できるようにする。生成される Flow SID を
環境変数 `TWILIO_STUDIO_HANDOFF_FLOW_SID` に設定すれば、ハンドオフが実フローへ流れる。

2 種類のフローを生成する（ガイドのウィジェット構成に対応）:

  音声 (voice):
    Trigger → Set Variables（ハンドオフ属性をタスク属性へ）→ SendToFlex

  メッセージング (messaging, SMS/チャット):
    Trigger
      → HTTP Request: conversationSid 取得（Participants の channelId）
      → HTTP Request: serviceSid 取得（conversationsV1Bridge）
      → ResumeConversation（既存会話を実行へ添付・onMessageAdded webhook 付与）
      → SendToFlex（Interactions API 経由でタスク属性へ）

`escalate_to_human` が渡す属性キー（HandoffPackage.task_attributes）と、ここで
SendToFlex に積むタスク属性キーを一致させてある。Studio Execution を
`Parameters`(JSON) 付きで起動すると、各キーは `{{flow.data.<key>}}` で参照できる。

注: Flow 定義は account 固有の SID（Flex Workflow / Task Channel）を含むため、
本番では公式テンプレートの利用も検討すること。本ジェネレータは即デプロイ可能な
出発点を与え、属性マッピングを TAC のハンドオフと整合させることを目的とする。
"""

from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request

from .config import CONFIG

# escalate_to_human → SendToFlex タスク属性のマッピング（flow.data の各キーを参照）
_TASK_ATTRIBUTES = {
    "conversationSid": "{{flow.data.conversationSid}}",
    "channelType": "{{flow.data.channel}}",
    "handoffReason": "{{flow.data.handoffReason}}",
    "virtualAgentSummary": "{{flow.data.virtualAgentSummary}}",
    "headline": "{{flow.data.headline}}",
    "intent": "{{flow.data.intent}}",
    "customerId": "{{flow.data.customerId}}",
    "priority": "{{flow.data.priority}}",
    "department": "{{flow.data.department}}",
    # Flex の仮想エージェント概要表示が参照するキー
    "conversations": {"conversation_summary": "{{flow.data.virtualAgentSummary}}"},
}


def _offset(x: int, y: int) -> dict:
    return {"x": x, "y": y}


def _send_to_flex(workflow_sid: str, task_channel_sid: str, *, name: str = "SendToFlex",
                  y: int = 500) -> dict:
    return {
        "name": name,
        "type": "send-to-flex",
        "properties": {
            "offset": _offset(50, y),
            "workflow": workflow_sid,
            "channel": task_channel_sid,
            "attributes": json.dumps(_TASK_ATTRIBUTES, ensure_ascii=False),
            "waitUrl": "",
            "waitUrlMethod": "POST",
        },
        "transitions": [
            {"event": "callComplete"},
            {"event": "failedToEnqueue"},
            {"event": "callFailure"},
        ],
    }


def build_voice_flow(flex_workflow_sid: str, flex_task_channel_sid: str) -> dict:
    """音声: Set Variables → SendToFlex。"""
    return {
        "description": "TAC Agent Handoff (voice)",
        "flags": {"allow_concurrent_calls": True},
        "initial_state": "Trigger",
        "states": [
            {
                "name": "Trigger",
                "type": "trigger",
                "properties": {"offset": _offset(0, 0)},
                "transitions": [
                    {"event": "incomingMessage"},
                    {"event": "incomingCall", "next": "set_attributes"},
                    {"event": "incomingConversationMessage"},
                    {"event": "incomingRequest", "next": "set_attributes"},
                    {"event": "incomingParent"},
                ],
            },
            {
                "name": "set_attributes",
                "type": "set-variables",
                "properties": {
                    "offset": _offset(50, 250),
                    # ハンドオフで受け取った属性を後段で参照できるよう確定させる
                    "variables": [
                        {"key": k, "value": f"{{{{flow.data.{k}}}}}"}
                        for k in ("conversationSid", "virtualAgentSummary", "headline",
                                  "intent", "handoffReason", "priority", "department")
                    ],
                },
                "transitions": [{"event": "next", "next": "SendToFlex"}],
            },
            _send_to_flex(flex_workflow_sid, flex_task_channel_sid),
        ],
    }


def build_messaging_flow(flex_workflow_sid: str, flex_task_channel_sid: str) -> dict:
    """SMS/チャット: HTTP×2 → ResumeConversation → SendToFlex。"""
    base = "https://conversations.twilio.com"
    return {
        "description": "TAC Agent Handoff (messaging)",
        "flags": {"allow_concurrent_calls": True},
        "initial_state": "Trigger",
        "states": [
            {
                "name": "Trigger",
                "type": "trigger",
                "properties": {"offset": _offset(0, 0)},
                "transitions": [
                    {"event": "incomingMessage", "next": "fetch_conversationSid"},
                    {"event": "incomingCall"},
                    {"event": "incomingConversationMessage", "next": "fetch_conversationSid"},
                    {"event": "incomingRequest", "next": "fetch_conversationSid"},
                    {"event": "incomingParent"},
                ],
            },
            {
                "name": "fetch_conversationSid",
                "type": "make-http-request",
                "properties": {
                    "offset": _offset(50, 200),
                    "method": "GET",
                    "content_type": "application/x-www-form-urlencoded;charset=utf-8",
                    # customer participant の channelId から v1 conversationSid を取得
                    "url": f"{base}/v2/Conversations/{{{{flow.data.conversationSid}}}}/Participants",
                },
                "transitions": [
                    {"event": "success", "next": "fetch_serviceSid"},
                    {"event": "failed"},
                ],
            },
            {
                "name": "fetch_serviceSid",
                "type": "make-http-request",
                "properties": {
                    "offset": _offset(50, 350),
                    "method": "GET",
                    "content_type": "application/x-www-form-urlencoded;charset=utf-8",
                    # conversationsV1Bridge から serviceSid を取得
                    "url": f"{base}/v2/Conversations/{{{{flow.data.conversationSid}}}}",
                },
                "transitions": [
                    {"event": "success", "next": "ResumeConversation"},
                    {"event": "failed"},
                ],
            },
            {
                "name": "ResumeConversation",
                "type": "send-message-to-conversation-resume",
                "properties": {
                    "offset": _offset(50, 480),
                    # 取得した SID で既存会話を実行へ添付し onMessageAdded webhook を付与
                    "conversation_sid": "{{widgets.fetch_conversationSid.parsed.channelId}}",
                    "service_sid": "{{widgets.fetch_serviceSid.parsed.conversationsV1Bridge.serviceSid}}",
                },
                "transitions": [
                    {"event": "success", "next": "SendToFlex"},
                    {"event": "failed"},
                ],
            },
            _send_to_flex(flex_workflow_sid, flex_task_channel_sid, y=640),
        ],
    }


def build_flow_definition(channel: str, flex_workflow_sid: str,
                          flex_task_channel_sid: str) -> dict:
    """channel('voice'|'messaging') に応じた Studio フロー定義を返す。"""
    if channel == "voice":
        return build_voice_flow(flex_workflow_sid, flex_task_channel_sid)
    if channel in ("messaging", "sms", "chat"):
        return build_messaging_flow(flex_workflow_sid, flex_task_channel_sid)
    raise ValueError(f"unknown channel: {channel}（'voice' か 'messaging'）")


# --- Twilio REST: フロー作成/更新・公開 ---
def create_or_update_flow(friendly_name: str, definition: dict, *,
                          status: str = "published", flow_sid: str = "") -> dict:
    """Studio Flow を作成（または flow_sid 指定で更新）して公開する。

    認証情報が無い/ドライランなら REST を呼ばず、送信予定のペイロードを返す。
    成功時は {'sid': 'FWxxxx', ...} を返す。
    """
    payload = {
        "FriendlyName": friendly_name,
        "Status": status,
        "Definition": json.dumps(definition, ensure_ascii=False),
    }
    if CONFIG.dry_run or not CONFIG.has_twilio:
        return {"dry_run": True, "would_post": {**payload, "Definition": "<json>"},
                "definition": definition}

    base = "https://studio.twilio.com/v2/Flows"
    url = f"{base}/{flow_sid}" if flow_sid else base
    data = urllib.parse.urlencode(payload).encode()
    auth = base64.b64encode(
        f"{CONFIG.twilio_account_sid}:{CONFIG.twilio_auth_token}".encode()
    ).decode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="TAC Agent Handoff の Studio フローを生成/登録")
    ap.add_argument("--channel", choices=["voice", "messaging"], default="voice")
    ap.add_argument("--workflow", default="WWxxxxxxxx", help="Flex Workflow SID")
    ap.add_argument("--task-channel", default="TCxxxxxxxx", help="Flex Task Channel SID")
    ap.add_argument("--name", default="TAC Agent Handoff")
    ap.add_argument("--create", action="store_true", help="REST でフローを作成/公開する")
    ap.add_argument("--flow-sid", default="", help="更新する既存 Flow SID（任意）")
    args = ap.parse_args(argv)

    definition = build_flow_definition(args.channel, args.workflow, args.task_channel)
    if not args.create:
        print(json.dumps(definition, ensure_ascii=False, indent=2))
        return 0
    result = create_or_update_flow(args.name, definition, flow_sid=args.flow_sid)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sid = result.get("sid")
    if sid:
        print(f"\n→ TWILIO_STUDIO_HANDOFF_FLOW_SID={sid} を設定してください。")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
