#!/usr/bin/env python3
"""OpenClaw / mail 監視 health check daemon.

毎 5 分 launchd が起動し、以下を確認:
    1. openclaw-gateway プロセス生存
    2. Telegram getMe (bot token 有効性)
    3. Anthropic API 疎通 (簡易 messages.count)
    4. セッションファイル数 (肥大化検知)
    5. 直近 30 分に「Unknown model / Unauthorized / SecretRef」等致命エラーが gateway.err.log に無いか

異常検知したら SLACK_WEBHOOK に「⚠️ OpenClaw ヘルス異常」を投稿する。
同じ症状は 1 時間に 1 回までしか通知しない (state ファイルで抑制)。

env:
    SLACK_WEBHOOK          必須
    TELEGRAM_BOT_TOKEN     必須
    ANTHROPIC_API_KEY      必須
    STATE_DIR              任意 (既定: ~/.openclaw/health)
    OPENCLAW_LOG_DIR       任意 (既定: ~/.openclaw/logs)
    SESSIONS_DIR           任意 (既定: ~/.openclaw/agents/main/sessions)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
STATE_DIR = Path(os.environ.get("STATE_DIR", str(Path.home() / ".openclaw" / "health")))
OPENCLAW_LOG_DIR = Path(os.environ.get("OPENCLAW_LOG_DIR", str(Path.home() / ".openclaw" / "logs")))
SESSIONS_DIR = Path(os.environ.get("SESSIONS_DIR", str(Path.home() / ".openclaw" / "agents" / "main" / "sessions")))
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

ALERT_COOLDOWN_SEC = 60 * 60
SESSION_FILE_WARN = 500
FATAL_LOG_PATTERNS = re.compile(
    r"(Unknown model|Unauthorized|SecretRefResolutionError|Gateway failed to start|surface_error reason=timeout)"
)
FATAL_LOG_WINDOW_MIN = 30


def alert_or_skip(key: str, message: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file = STATE_DIR / f"alert-{key}.ts"
    now = time.time()
    if state_file.exists():
        try:
            last = float(state_file.read_text().strip())
            if now - last < ALERT_COOLDOWN_SEC:
                print(f"[skip] {key} within cooldown", file=sys.stderr)
                return
        except ValueError:
            pass
    state_file.write_text(str(now))
    post_slack(message)


def post_slack(message: str) -> None:
    if not SLACK_WEBHOOK:
        print("SLACK_WEBHOOK unset; would have sent:", message, file=sys.stderr)
        return
    payload = {
        "text": message,
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": message}}],
    }
    req = urllib.request.Request(
        SLACK_WEBHOOK,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"slack post failed: {e}", file=sys.stderr)


def check_gateway_process() -> str | None:
    out = subprocess.run(
        ["pgrep", "-f", "openclaw-gateway"], capture_output=True, text=True
    )
    if not out.stdout.strip():
        return "openclaw-gateway プロセスが停止しています"
    return None


def check_telegram_getme() -> str | None:
    if not TELEGRAM_BOT_TOKEN:
        return "TELEGRAM_BOT_TOKEN 未設定"
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=10
        ) as r:
            body = json.loads(r.read())
            if not body.get("ok"):
                return f"Telegram getMe not ok: {body.get('description')}"
    except urllib.error.HTTPError as e:
        return f"Telegram getMe HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}"
    except Exception as e:
        return f"Telegram getMe 失敗: {e}"
    return None


def check_anthropic() -> str | None:
    if not ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY 未設定"
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        method="POST",
        data=json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 5,
            "messages": [{"role": "user", "content": "ping"}],
        }).encode("utf-8"),
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            return None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        if e.code == 400 and "haiku" in body.lower():
            return None
        return f"Anthropic API HTTP {e.code}: {body}"
    except Exception as e:
        return f"Anthropic API 失敗: {e}"


def check_sessions() -> str | None:
    if not SESSIONS_DIR.exists():
        return None
    count = sum(1 for _ in SESSIONS_DIR.iterdir())
    if count > SESSION_FILE_WARN:
        return f"session ファイル {count} 個 (>{SESSION_FILE_WARN}) — cleanup 推奨"
    return None


def check_recent_fatal_log() -> str | None:
    err_log = OPENCLAW_LOG_DIR / "gateway.err.log"
    if not err_log.exists():
        return None
    cutoff = datetime.now(JST) - timedelta(minutes=FATAL_LOG_WINDOW_MIN)
    try:
        lines = err_log.read_text(errors="replace").splitlines()[-500:]
    except Exception:
        return None
    hits: list[str] = []
    for line in lines:
        m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-]\d{2}:\d{2})", line)
        if not m:
            continue
        try:
            ts = datetime.fromisoformat(m.group(1))
        except ValueError:
            continue
        if ts < cutoff:
            continue
        if FATAL_LOG_PATTERNS.search(line):
            hits.append(line[:200])
    if hits:
        return f"直近 {FATAL_LOG_WINDOW_MIN} 分に致命エラー {len(hits)} 件:\n```\n" + "\n".join(hits[:3]) + "\n```"
    return None


def main() -> int:
    checks = [
        ("gateway_process", "🛑 gateway プロセス停止", check_gateway_process),
        ("telegram_token", "🛑 Telegram bot トークン無効", check_telegram_getme),
        ("anthropic_api", "🛑 Anthropic API 疎通失敗", check_anthropic),
        ("sessions_bloat", "⚠️ session ファイル過多", check_sessions),
        ("recent_fatal", "🛑 直近致命エラー", check_recent_fatal_log),
    ]
    failed = 0
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    for key, label, fn in checks:
        try:
            problem = fn()
        except Exception as e:
            problem = f"チェック自体が失敗: {e}"
        if problem:
            failed += 1
            alert_or_skip(key, f"⚠️ *OpenClaw ヘルス異常* [{stamp}]\n*{label}*\n> {problem}")
            print(f"[FAIL] {key}: {problem}", file=sys.stderr)
        else:
            print(f"[OK] {key}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
