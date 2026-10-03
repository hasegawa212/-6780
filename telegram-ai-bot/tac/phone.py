"""電話番号の正規化（国内表記 → E.164）。

iPhone から 090-1234-5678 / 03 6899 5464 / 全角数字 のように打っても、Twilio が
受け付ける E.164（+819012345678）に直す。番号として成り立たないものは None。
"""

from __future__ import annotations

import re
import unicodedata

# E.164 は国番号込みで最大15桁。短すぎるもの（+81 だけ等）は弾く。
_MIN_DIGITS = 8
_MAX_DIGITS = 15


def to_e164(raw: str | None, default_cc: str = "81") -> str | None:
    """番号文字列を E.164 に正規化する。不正なら None。

    - "+..."  : 国番号付き。記号を除いてそのまま
    - "00..." : 国際プレフィックス。"+" に置き換え
    - "0..."  : 国内表記。先頭の 0 を落として default_cc（既定 81=日本）を付ける
    """
    if not raw:
        return None
    s = unicodedata.normalize("NFKC", raw).strip()  # 全角数字・全角記号を半角に
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits or re.search(r"[^\d\s\-()+.]", s):
        return None
    if plus:
        e164 = digits
    elif digits.startswith("00"):
        e164 = digits[2:]
    elif digits.startswith("0"):
        e164 = default_cc + digits[1:]
    else:
        return None
    if not (_MIN_DIGITS <= len(e164) <= _MAX_DIGITS):
        return None
    return "+" + e164
