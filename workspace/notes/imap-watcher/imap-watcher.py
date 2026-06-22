#!/usr/bin/env python3
"""Lolipop IMAP watcher -> Slack Incoming Webhook.

Polls 4 mailboxes on imap.lolipop.jp, posts new messages to Slack,
persists last-seen UID per account so duplicates are not re-posted.

Run from launchd every 5 minutes. See setup.sh.

Environment:
    SLACK_WEBHOOK   required. Slack Incoming Webhook URL.
    LOLIPOP_IMAP_HOST   default imap.lolipop.jp
    LOLIPOP_IMAP_PORT   default 993
    STATE_DIR       default ~/.openclaw/imap-watcher/state
"""

import email
import imaplib
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path

IMAP_HOST = os.environ.get("LOLIPOP_IMAP_HOST", "imap.lolipop.jp")
IMAP_PORT = int(os.environ.get("LOLIPOP_IMAP_PORT", "993"))
SLACK_WEBHOOK = os.environ["SLACK_WEBHOOK"]
STATE_DIR = Path(
    os.environ.get("STATE_DIR", str(Path.home() / ".openclaw" / "imap-watcher" / "state"))
)
KEYCHAIN_SERVICE = "lolipop-imap"
MAX_BODY_CHARS = 600
JST = timezone(timedelta(hours=9))

ACCOUNTS = [
    "info@martialarts.co.jp",
    "sales@martialarts.co.jp",
    "h.hasegawa@martialarts.co.jp",
    "wordpress@martialarts.co.jp",
]


def get_password(account: str) -> str:
    out = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def load_last_uid(account: str) -> int:
    f = STATE_DIR / f"{account}.uid"
    return int(f.read_text().strip()) if f.exists() else 0


def save_last_uid(account: str, uid: int) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{account}.uid").write_text(str(uid))


def decode_mime(value) -> str:
    if not value:
        return ""
    pieces = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, bytes):
            try:
                pieces.append(chunk.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                pieces.append(chunk.decode("utf-8", errors="replace"))
        else:
            pieces.append(chunk)
    return "".join(pieces)


def extract_body(msg) -> str:
    candidate_parts = msg.walk() if msg.is_multipart() else [msg]
    for part in candidate_parts:
        if part.get_content_type() != "text/plain":
            continue
        if "attachment" in str(part.get("Content-Disposition") or ""):
            continue
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")
    return ""


def format_date(raw) -> str:
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).astimezone(JST).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return ""


def post_slack(account: str, msg) -> None:
    subject = decode_mime(msg["Subject"]) or "(no subject)"
    from_addr = decode_mime(msg["From"]) or "(unknown)"
    date_str = format_date(msg["Date"])
    body = re.sub(r"\s+", " ", extract_body(msg)).strip()[:MAX_BODY_CHARS]

    payload = {
        "text": f"[新着メール:Lolipop] {account} / {subject}",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f":envelope: *{account}* に新着メール"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*From:*\n{from_addr}"},
                    {"type": "mrkdwn", "text": f"*Time:*\n{date_str}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*件名:* {subject}\n```\n{body}\n```",
                },
            },
        ],
    }
    req = urllib.request.Request(
        SLACK_WEBHOOK,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Slack {resp.status}: {resp.read()!r}")


def watch_account(account: str) -> None:
    pw = get_password(account)
    last_uid = load_last_uid(account)
    new_last = last_uid

    with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as M:
        M.login(account, pw)
        M.select("INBOX", readonly=True)

        criteria = f"UID {last_uid + 1}:*" if last_uid else "ALL"
        typ, data = M.uid("search", None, criteria)
        if typ != "OK":
            print(f"[{account}] search failed: {typ}", file=sys.stderr)
            return
        raw_uids = data[0].split() if data and data[0] else []
        uids = [u for u in raw_uids if int(u) > last_uid]

        # First run: just bookmark the latest UID, do not flood Slack with history.
        if last_uid == 0:
            if uids:
                new_last = max(int(u) for u in uids)
                save_last_uid(account, new_last)
                print(f"[{account}] bootstrap: skipped {len(uids)}, last={new_last}")
            else:
                save_last_uid(account, 0)
                print(f"[{account}] bootstrap: empty mailbox")
            return

        for uid in uids:
            typ, msg_data = M.uid("fetch", uid, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            try:
                post_slack(account, msg)
            except Exception as e:
                print(f"[{account}] post failed uid={uid.decode()}: {e}", file=sys.stderr)
                continue
            new_last = max(new_last, int(uid))

    if new_last != last_uid:
        save_last_uid(account, new_last)
        print(f"[{account}] processed, last={new_last}")


def main() -> int:
    failures = 0
    for account in ACCOUNTS:
        try:
            watch_account(account)
        except Exception as e:
            print(f"[{account}] error: {e}", file=sys.stderr)
            failures += 1
    return 1 if failures == len(ACCOUNTS) else 0


if __name__ == "__main__":
    sys.exit(main())
