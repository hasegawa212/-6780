"""最強 Slack ボット (Socket Mode・Claude 統合).

Slack で @メンション または DM すると、Claude が応答する。
- 💬 文脈つきチャット（チャンネル/スレッドごとに記憶）
- 🌐 ウェブ検索・ページ取得（最新情報）
- 🏭 コード実行でグラフ・画像・Word/Excel/PDF 等を生成して Slack に添付
- 🛡 自動リトライで落ちにくい

必要な環境変数:
  ANTHROPIC_API_KEY   … Claude
  SLACK_BOT_TOKEN     … xoxb-…（chat:write 等）
  SLACK_APP_TOKEN     … xapp-…（Socket Mode・connections:write）
任意:
  CLAUDE_MODEL(既定 claude-opus-4-8) / CLAUDE_EFFORT(既定 medium) /
  CLAUDE_MAX_TOKENS(既定 8000) / SLACK_WEB_SEARCH(既定 1)
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from collections import defaultdict, deque

from anthropic import AsyncAnthropic
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")
EFFORT = os.environ.get("CLAUDE_EFFORT", "medium")
MAXTOK = int(os.environ.get("CLAUDE_MAX_TOKENS", "8000"))
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")
WEB_SEARCH = os.environ.get("SLACK_WEB_SEARCH", "1") not in ("0", "false", "False", "")
TURNS = int(os.environ.get("SLACK_HISTORY_TURNS", "10"))

SYS = os.environ.get(
    "SLACK_SYSTEM_PROMPT",
    "あなたは Slack 上で働く超優秀な業務アシスタントです。"
    "調査・文章作成・資料生成・分析を最後までやり切ります。\n"
    "【読みやすさ】Slack で読む前提。結論を最初に1文。要点は短い箇条書き。"
    "専門用語や記号の羅列は避け、普通の言葉で端的に。\n"
    "【ツール】最新情報は web_search / web_fetch で裏取りする。"
    "グラフ・図・Word(.docx)・Excel(.xlsx)・PDF・CSV 等のファイルは code_execution で"
    "実際にコードを書いて生成する（生成物は自動で添付される）。\n"
    "推測と事実を区別し、できないことは正直に伝える。誠実に、簡潔に。",
)

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("slack_bolt").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("slack-bot")

claude = AsyncAnthropic(api_key=KEY, max_retries=6, timeout=180.0)
app = AsyncApp(token=SLACK_BOT_TOKEN)

# チャンネル/スレッドごとの会話履歴
hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=TURNS * 2))
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
_MENTION = re.compile(r"<@[A-Z0-9]+>")


def _tools() -> list:
    t = []
    if WEB_SEARCH:
        t.append({"type": "web_search_20260209", "name": "web_search"})
        t.append({"type": "web_fetch_20260209", "name": "web_fetch"})
    t.append({"type": "code_execution_20260120", "name": "code_execution"})
    return t


def _collect_file_ids(obj, out: set) -> None:
    try:
        fid = getattr(obj, "file_id", None)
        if isinstance(fid, str):
            out.add(fid)
        sub = getattr(obj, "content", None)
        if isinstance(sub, list):
            for x in sub:
                _collect_file_ids(x, out)
        elif sub is not None and not isinstance(sub, (str, bytes)):
            _collect_file_ids(sub, out)
    except Exception:
        pass


def _split(t: str, n: int = 3500) -> list[str]:
    if len(t) <= n:
        return [t] if t else []
    out, cur = [], t
    while cur:
        if len(cur) <= n:
            out.append(cur)
            break
        cut = cur.rfind("\n", 0, n)
        cut = cut if cut > 0 else n
        out.append(cur[:cut])
        cur = cur[cut:].lstrip("\n")
    return out


async def _ask(key: str, content) -> tuple[str, set]:
    """Claude に投げて (本文, 生成file_id集合) を返す。"""
    h = hist[key]
    messages = list(h) + [{"role": "user", "content": content}]
    acc = ""
    final = None
    file_ids: set = set()
    for _ in range(6):
        async with claude.messages.stream(
            model=MODEL,
            max_tokens=MAXTOK,
            system=SYS,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            tools=_tools(),
            messages=messages,
        ) as stream:
            async for ev in stream:
                if (ev.type == "content_block_delta"
                        and getattr(ev.delta, "type", None) == "text_delta"):
                    acc += ev.delta.text
            final = await stream.get_final_message()
        messages.append({"role": "assistant", "content": final.content})
        for b in final.content:
            _collect_file_ids(b, file_ids)
        if getattr(final, "stop_reason", None) == "pause_turn":
            continue
        break
    text = acc.strip()
    if not text and final is not None:
        text = "".join(
            b.text for b in final.content if getattr(b, "type", None) == "text"
        ).strip()
    text = text or "(応答を生成できませんでした)"
    h.append({"role": "user", "content": content if isinstance(content, str) else "[添付]"})
    h.append({"role": "assistant", "content": text})
    return text, file_ids


async def _upload(client, channel: str, thread_ts, file_ids: set) -> None:
    for fid in file_ids:
        try:
            meta = await claude.beta.files.retrieve_metadata(fid)
            fname = getattr(meta, "filename", None) or f"{fid}.bin"
            binr = await claude.beta.files.download(fid)
            try:
                data = await binr.aread()
            except Exception:
                data = binr.read()
            await client.files_upload_v2(
                channel=channel, file=io.BytesIO(data), filename=fname,
                thread_ts=thread_ts, initial_comment=None,
            )
        except Exception:
            log.exception("ファイル添付に失敗: %s", fid)


async def _respond(client, channel: str, thread_ts, text: str) -> None:
    key = f"{channel}:{thread_ts}" if thread_ts else channel
    try:
        await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="🤔 …")
    except Exception:
        pass
    try:
        reply, file_ids = await _ask(key, text)
    except Exception:
        log.exception("応答生成に失敗")
        await client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                      text="⚠️ 応答生成中にエラーが発生しました。")
        return
    for chunk in _split(reply):
        await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=chunk)
    if file_ids:
        await _upload(client, channel, thread_ts, file_ids)


@app.event("app_mention")
async def on_mention(event, client):
    text = _MENTION.sub("", event.get("text", "")).strip()
    if not text:
        return
    thread = event.get("thread_ts") or event.get("ts")
    await _respond(client, event["channel"], thread, text)


@app.event("message")
async def on_message(event, client):
    # DM のみ自動応答（チャンネルは @メンションで）
    if event.get("bot_id") or event.get("subtype"):
        return
    if event.get("channel_type") != "im":
        return
    text = (event.get("text") or "").strip()
    if not text:
        return
    await _respond(client, event["channel"], event.get("thread_ts"), text)


async def main():
    if not (KEY and SLACK_BOT_TOKEN and SLACK_APP_TOKEN):
        raise SystemExit(
            "ANTHROPIC_API_KEY / SLACK_BOT_TOKEN / SLACK_APP_TOKEN を設定してください。"
        )
    me = await app.client.auth_test()
    log.info("起動: Slack bot @%s (team=%s) model=%s web_search=%s",
             me.get("user"), me.get("team"), MODEL, WEB_SEARCH)
    handler = AsyncSocketModeHandler(app, SLACK_APP_TOKEN)
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
