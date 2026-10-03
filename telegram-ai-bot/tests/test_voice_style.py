"""さくらの話し方（声の人間らしさ）のテスト（TDD）。

電話応対が硬く短すぎないよう、温かく・自然で・少し詳しめ・カジュアル寄りの
話し言葉になっていること、第一声が親しみやすい挨拶であることを固定する。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac.config import CONFIG  # noqa: E402
from tac.connector import TACConnector  # noqa: E402
from tac.models import Channel, Conversation  # noqa: E402


def test_voice_prompt_is_warm_and_natural():
    conn = TACConnector()
    conv = Conversation(sid="V1", channel=Channel.VOICE)
    s = conn._system(conv, "")
    # 人間らしい自然な話し言葉で応対する指示
    assert "自然な話し言葉" in s
    # 相づちで受け止めてから答える（温かさ）
    assert "相づち" in s
    # 2〜3文まで許容（硬すぎる「基本1〜2文」ではない）
    assert "2〜3文" in s
    assert "基本1〜2文" not in s
    # もっとフランク：フレンドリーで肩の力を抜いた口調の指示が入る
    assert "フレンドリー" in s
    # ロボット（棒読み・定型）っぽさを避ける指示が入る
    assert "棒読み" in s


def test_greeting_is_warmer_and_inviting():
    # 第一声はさくらと名乗り、問いかけで自然に会話を誘う
    assert "さくら" in CONFIG.relay_welcome
    assert "？" in CONFIG.relay_welcome
