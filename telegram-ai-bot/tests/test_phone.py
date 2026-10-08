"""電話番号正規化 tac/phone.to_e164 の TDD。

「かける番号に自動で +81 を付ける」中核ロジックを固定する。
iPhone 入力（ハイフン/空白/全角）や国際表記を E.164(+81…) に直し、
読めないものは None を返す（fail closed）。
実行: cd telegram-ai-bot && python3 -m pytest tests/test_phone.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import phone  # noqa: E402


# ---- 国内表記 → +81（先頭0を落として81付与） ----
def test_mobile_hyphen():
    assert phone.to_e164("090-1234-5678") == "+819012345678"


def test_mobile_plain():
    assert phone.to_e164("09012345678") == "+819012345678"


def test_landline_tokyo():
    assert phone.to_e164("03-6914-4887") == "+81369144887"


def test_spaces():
    assert phone.to_e164("03 6899 5464") == "+81368995464"


def test_fullwidth_digits():
    # 全角で打っても半角化して +81 を付ける
    assert phone.to_e164("０９０１２３４５６７８") == "+819012345678"


# ---- 国際表記 ----
def test_already_plus81():
    assert phone.to_e164("+81 90 1234 5678") == "+819012345678"


def test_double_zero_intl_prefix():
    # 00 国際プレフィックス → + に置換
    assert phone.to_e164("008190-1234-5678") == "+819012345678"


def test_us_number_plus():
    assert phone.to_e164("+1 (212) 555-0147") == "+12125550147"


# ---- 不正系は None（発信させない） ----
def test_empty_is_none():
    assert phone.to_e164("") is None
    assert phone.to_e164(None) is None


def test_letters_is_none():
    assert phone.to_e164("内線123") is None
    assert phone.to_e164("090-ABCD-5678") is None


def test_no_leading_zero_or_plus_is_none():
    # 0/+/00 のいずれでも始まらない国内ざっくり入力は読めないので None
    assert phone.to_e164("9012345678") is None


def test_too_short_is_none():
    assert phone.to_e164("+123") is None


# ---- 既定の国番号を差し替えられる（将来の多国対応の保険） ----
def test_custom_country_code():
    assert phone.to_e164("0912345678", default_cc="82") == "+82912345678"
