"""録音同意（recording consent）。

録音する設定（CONFIG.record_calls）のとき、通話冒頭で必ず録音の同意告知を入れる。
同意なき録音を避けるための最小ヘルパー。録音しない設定なら何も足さない。
"""

from __future__ import annotations

from .config import CONFIG


def notice() -> str:
    """録音ONなら同意告知文、OFFなら空文字。"""
    return CONFIG.recording_consent_text if CONFIG.record_calls else ""


def prefix(base: str) -> str:
    """base の前に録音告知を付ける（録音ON時のみ）。"""
    n = notice()
    if not n:
        return base
    return f"{n} {base}".strip() if base else n
