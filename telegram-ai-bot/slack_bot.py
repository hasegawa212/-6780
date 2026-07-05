"""最強 Slack ボット (Socket Mode・Claude 統合).

Slack で @メンション または DM すると、Claude が応答する。
- 🧠 Telegram ボット(mega_bot)と「同じ脳」：同じデータ(~/.telegram-mega-bot)・
     同じ 52 ツール（顧客台帳・記憶・知識・名簿・日報・予定・経費・ToDo・
     メール・Slack送受信・リマインダー 等）を共有する。
- 💬 スレッド内は履歴無制限（スレッド全体を毎回読み込む・再起動でも残る）
- 🌐 ウェブ検索・ページ取得（最新情報）
- 🏭 コード実行でグラフ・Word/Excel/PDF 等を生成して Slack に添付
- 🛡 自動リトライで落ちにくい

必要な環境変数:
  ANTHROPIC_API_KEY   … Claude
  SLACK_BOT_TOKEN     … xoxb-…（chat:write 等）
  SLACK_APP_TOKEN     … xapp-…（Socket Mode・connections:write）
任意:
  CLAUDE_MODEL(既定 claude-opus-4-8) / CLAUDE_EFFORT(既定 medium) /
  CLAUDE_MAX_TOKENS(既定 8000) / SLACK_WEB_SEARCH(既定 1) /
  SLACK_BRAIN_CHAT_ID(Telegram と同じ台帳を共有する場合に、その Telegram チャットIDを設定) /
  SLACK_ADMIN_USER_IDS(送信/電話/PC作業など要認可ツールを許す Slack ユーザーID。
                       未設定なら全員に許可＝社内ワークスペース前提)
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

# Telegram ボットを「脳」として取り込む（同じデータ・同じ 52 ツールを共有）。
# 取り込めない環境（telegram 未導入など）では従来の単体モードに自動フォールバック。
try:
    import mega_bot as brain  # noqa: E402
    BRAIN = True
except Exception as _e:  # pragma: no cover - 環境依存
    brain = None
    BRAIN = False
    logging.getLogger("slack-bot").warning("mega_bot を取り込めず単体モードで起動: %s", _e)

KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")
EFFORT = os.environ.get("CLAUDE_EFFORT", "medium")
MAXTOK = int(os.environ.get("CLAUDE_MAX_TOKENS", "8000"))
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")
WEB_SEARCH = os.environ.get("SLACK_WEB_SEARCH", "1") not in ("0", "false", "False", "")
TURNS = int(os.environ.get("SLACK_HISTORY_TURNS", "10"))
# スレッド内は履歴を打ち切らず、そのスレッド全体を毎回読み込んで文脈にする。
# → 往復数の上限なし & ボット再起動でも消えない（Slack 側が記録を保持）。
THREAD_MEMORY = os.environ.get("SLACK_THREAD_MEMORY", "1") not in ("0", "false", "False", "")
MAX_THREAD_MSGS = int(os.environ.get("SLACK_MAX_THREAD_MSGS", "600"))  # 暴走防止の安全上限
# 共有する「脳」のキー。Telegram と同じ台帳を読むには、その Telegram チャットIDを設定する。
# 未設定(0)なら Slack 独自の台帳になる（Telegram とは別データ）。
BRAIN_CHAT_ID = int(os.environ.get("SLACK_BRAIN_CHAT_ID", "0"))
# 要認可ツール（send_slack / make_call / send_email / run_claude_code 等）を使える Slack ユーザー。
ADMIN_USER_IDS = {x.strip() for x in os.environ.get("SLACK_ADMIN_USER_IDS", "").split(",") if x.strip()}

# Slack 表示用の追記（mega_bot の system に足す）。
SLACK_ADDENDUM = (
    "【Slack 表示】結論を最初に1文。要点は短い箇条書き。記号の羅列を避け普通の言葉で端的に。"
    "図表・Word(.docx)・Excel(.xlsx)・PDF・CSV は code_execution で実際に作る（自動添付される）。"
)

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

# チャンネル/スレッドごとの会話履歴（スレッド読込が使えない時のフォールバック）
hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=TURNS * 2))
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
_MENTION = re.compile(r"<@[A-Z0-9]+>")
BOT_USER_ID = ""  # 起動時に auth_test で確定（自分の発言を assistant 扱いにするため）


def _tools_for(authorized: bool) -> list:
    """共有脳があれば mega_bot の 52 ツールを、無ければ最小構成を返す。"""
    if BRAIN:
        return brain._tools_for_chat(authorized)
    t = []
    if WEB_SEARCH:
        t.append({"type": "web_search_20260209", "name": "web_search"})
        t.append({"type": "web_fetch_20260209", "name": "web_fetch"})
    t.append({"type": "code_execution_20260120", "name": "code_execution"})
    return t


def _system_for():
    """共有脳があれば記憶・知識・顧客を注入した system を、無ければ既定文字列を返す。"""
    if BRAIN:
        return brain._system_param(BRAIN_CHAT_ID, SLACK_ADDENDUM)
    return SYS


async def _exec_tool(name: str, inp: dict) -> str:
    """クライアントツールを共有脳で実行（Telegram と同じデータに読み書き）。"""
    if BRAIN:
        try:
            return await brain._exec_client_tool(BRAIN_CHAT_ID, name, inp or {})
        except Exception as e:
            log.exception("ツール実行に失敗: %s", name)
            return f"ツール『{name}』の実行に失敗しました: {e}"
    return f"未対応のツール: {name}"


def _streamer(**kw):
    """MCP 設定時は mega_bot の beta ストリームを使う。"""
    if BRAIN:
        return brain._stream(**kw)
    return claude.messages.stream(**kw)


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


async def _run(messages: list, authorized: bool) -> tuple[str, set]:
    """組み立て済み messages を投げ、ツール（52種）も実行して (本文, file_id集合) を返す。"""
    msgs = list(messages)
    tools = _tools_for(authorized)
    system = _system_for()
    acc = ""
    final = None
    file_ids: set = set()
    for _ in range(12):  # ツール/検索の継続ループ
        async with _streamer(
            model=MODEL,
            max_tokens=MAXTOK,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            tools=tools,
            messages=msgs,
        ) as stream:
            async for ev in stream:
                if (ev.type == "content_block_delta"
                        and getattr(ev.delta, "type", None) == "text_delta"):
                    acc += ev.delta.text
            final = await stream.get_final_message()
        msgs.append({"role": "assistant", "content": final.content})
        for b in final.content:
            _collect_file_ids(b, file_ids)
        sr = getattr(final, "stop_reason", None)
        if sr == "pause_turn":  # サーバーツール（検索/コード実行）の継続
            continue
        if sr == "tool_use":  # クライアントツール（52種）を実行して結果を返す
            results = []
            for b in final.content:
                if getattr(b, "type", None) == "tool_use":
                    out = await _exec_tool(b.name, b.input)
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": out or "(空)"})
            if results:
                msgs.append({"role": "user", "content": results})
                continue
        break
    text = acc.strip()
    if not text and final is not None:
        text = "".join(
            b.text for b in final.content if getattr(b, "type", None) == "text"
        ).strip()
    return text or "(応答を生成できませんでした)", file_ids


async def _ask(key: str, content, authorized: bool) -> tuple[str, set]:
    """メモリ履歴（deque）を使う従来パス。DM や履歴取得不可時のフォールバック。"""
    h = hist[key]
    text, file_ids = await _run(list(h) + [{"role": "user", "content": content}], authorized)
    h.append({"role": "user", "content": content if isinstance(content, str) else "[添付]"})
    h.append({"role": "assistant", "content": text})
    return text, file_ids


def _normalize(msgs: list) -> list:
    """Claude 用に整える：先頭の assistant を落とし、連続する同roleは結合する。"""
    while msgs and msgs[0]["role"] == "assistant":
        msgs.pop(0)
    out: list = []
    for m in msgs:
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"] += "\n" + m["content"]
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


async def _fetch_thread(client, channel: str, thread_ts: str) -> list:
    """スレッド全体を Slack から読み、Claude 用 messages に変換する（無限記憶）。"""
    raw: list = []
    cursor = None
    while True:
        resp = await client.conversations_replies(
            channel=channel, ts=thread_ts, limit=200, cursor=cursor,
        )
        raw.extend(resp.get("messages", []))
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor or len(raw) >= MAX_THREAD_MSGS * 2:
            break
    out: list = []
    for m in raw:
        if m.get("subtype"):  # 参加/退出などのシステムメッセージは除外
            continue
        txt = _MENTION.sub("", m.get("text") or "").strip()
        if not txt or txt == "🤔 …":  # 空・考え中プレースホルダは除外
            continue
        role = "assistant" if (BOT_USER_ID and m.get("user") == BOT_USER_ID) else "user"
        out.append({"role": role, "content": txt})
    if len(out) > MAX_THREAD_MSGS:  # 安全上限（直近のみ）
        out = out[-MAX_THREAD_MSGS:]
    return _normalize(out)


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


def _authorized(user_id: str) -> bool:
    """要認可ツールを許可するか。ADMIN 未設定なら社内前提で全員許可。"""
    if not ADMIN_USER_IDS:
        return True
    return user_id in ADMIN_USER_IDS


async def _respond(client, channel: str, thread_ts, text: str, user_id: str = "") -> None:
    authorized = _authorized(user_id)
    # スレッド内なら Slack からスレッド全体を読み込んで文脈にする（無限記憶・再起動耐性）。
    history = None
    if THREAD_MEMORY and thread_ts:
        try:
            history = await _fetch_thread(client, channel, thread_ts)
        except Exception:
            log.exception("スレッド履歴の取得に失敗 → メモリ履歴で継続（要 history スコープ）")
            history = None
    try:
        await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="🤔 …")
    except Exception:
        pass
    try:
        if history is not None:
            if not history or history[-1]["role"] != "user":
                history.append({"role": "user", "content": text})
            reply, file_ids = await _run(history, authorized)
        else:
            key = f"{channel}:{thread_ts}" if thread_ts else channel
            reply, file_ids = await _ask(key, text, authorized)
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
    await _respond(client, event["channel"], thread, text, event.get("user", ""))


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
    await _respond(client, event["channel"], event.get("thread_ts"), text, event.get("user", ""))


async def main():
    if not (KEY and SLACK_BOT_TOKEN and SLACK_APP_TOKEN):
        raise SystemExit(
            "ANTHROPIC_API_KEY / SLACK_BOT_TOKEN / SLACK_APP_TOKEN を設定してください。"
        )
    global BOT_USER_ID
    me = await app.client.auth_test()
    BOT_USER_ID = me.get("user_id", "")
    ntools = len(_tools_for(True))
    log.info("起動: Slack bot @%s (team=%s) model=%s brain=%s tools=%d thread_memory=%s",
             me.get("user"), me.get("team"), MODEL, BRAIN, ntools, THREAD_MEMORY)
    handler = AsyncSocketModeHandler(app, SLACK_APP_TOKEN)
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
