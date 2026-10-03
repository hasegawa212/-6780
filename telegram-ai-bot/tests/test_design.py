"""デザイン刷新（Design Refresh）のテスト（TDD）。

Martial Arts炎ブランド、ダークモード、アクセシビリティ、PWA質感。
新しいUI要素（キュー、チャート、折り返し、メモ）の存在確認。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import mobile_app  # noqa: E402


def test_dark_mode_css_variables():
    page = mobile_app.render()
    assert "prefers-color-scheme: dark" in page
    assert "--bg:" in page
    assert "--card:" in page


def test_aria_labels_present():
    page = mobile_app.render()
    assert 'aria-label="発信"' in page or 'aria-label="電話番号"' in page
    assert 'role="tabpanel"' in page
    assert 'role="tablist"' in page
    assert 'role="tab"' in page
    assert 'aria-selected' in page


def test_accent_color_brand():
    page = mobile_app.render()
    assert "#c2410c" in page


def test_large_tap_targets():
    page = mobile_app.render()
    assert "min-height: 48px" in page or "min-height:48px" in page


def test_no_auto_dial():
    page = mobile_app.render()
    assert "setInterval" not in page


def test_queue_section_exists():
    page = mobile_app.render()
    assert "queue-list" in page
    assert "queue-search" in page
    assert "/tac/calls/queue" in page


def test_chart_section_exists():
    page = mobile_app.render()
    assert "agent-chart" in page
    assert "/tac/calls/stats" in page
    assert "bar-fill" in page


def test_callback_section_exists():
    page = mobile_app.render()
    assert "cb-list" in page
    assert "/tac/calls/callbacks" in page
    assert 'type="datetime-local"' in page


def test_note_input_exists():
    page = mobile_app.render()
    assert "dispo-note" in page
    assert "/tac/calls/note" in page


def test_xss_prevention_textcontent():
    """XSS対策: textContent のみ使用、innerHTML に外部値を入れない。"""
    page = mobile_app.render()
    assert "textContent" in page
    assert ".innerHTML" not in page


def test_gradient_buttons():
    page = mobile_app.render()
    assert "linear-gradient" in page


def test_status_bar_dark():
    page = mobile_app.render()
    assert "black-translucent" in page


def test_nav_icons():
    page = mobile_app.render()
    assert "nav-icon" in page
