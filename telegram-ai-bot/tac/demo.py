"""TAC のエンドツーエンド・デモ（外部認証情報不要）。

  python tac/demo.py

ANTHROPIC_API_KEY があれば実際に Claude が応答・要約する。無ければ各機能が
安全に degrade（LLM 応答はプレースホルダ、ハンドオフはドライラン）するので、
ライフサイクルとハンドオフ、会話インサイトの流れだけを確認できる。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tac import Channel, TACConnector  # noqa: E402
from tac.config import CONFIG  # noqa: E402

CONFIG.dry_run = True


def main() -> None:
    conn = TACConnector()

    # 社内ナレッジと顧客プロファイルを用意（ブリッジで参照される）
    conn.memory._knowledge.add(
        "解約は次回更新日の3営業日前までに申請が必要です。",
        "プレミアム会員は解約時に違約金が免除されます。",
    )
    conn.memory.seed(
        "+819012345678",
        traits={"name": "山田太郎", "plan": "premium", "lang": "ja"},
        observations=["先月、請求金額について問い合わせ（解決済み）"],
    )

    print("=== 通話開始 (voice) ===")
    conv = conn.start("CA_demo", Channel.VOICE,
                      customer_identity="+819012345678", goal="解約の相談")
    print(f"顧客識別: {conv.customer_id} / 参加者: {[p.role.value for p in conv.participants]}")

    for utterance in [
        "プレミアム会員なんですけど、解約したいんです。違約金かかりますか？",
        "やっぱり納得いかない。担当の人と直接話させてください。",
    ]:
        print(f"\n顧客: {utterance}")
        res = conn.handle("CA_demo", utterance)
        print(f"AI  : {res.text or '(無言)'}")
        if res.tool_calls:
            for tc in res.tool_calls:
                print(f"  ↳ tool {tc['name']}({tc['input']}) -> {tc['output']}")
        if res.assist:
            sent = res.assist.get("sentiment", {})
            if sent:
                print(f"  ↳ 支援: 感情={sent.get('label')} ({sent.get('score')})")
        if res.handed_off:
            print("  ↳ ★ 人間エージェントへハンドオフ完了（会話は handed-off）")
            break

    print(f"\n会話ステータス: {conn.get('CA_demo').status.value}")

    print("\n=== 会話終了・事後インテリジェンス ===")
    signals = conn.close("CA_demo")
    summ = signals.get("summary", {})
    print(f"件名 : {summ.get('headline') or '(LLM未設定)'}")
    print(f"要約 : {summ.get('summary') or '(LLM未設定のため空)'}")

    print("\n=== 会話インサイト（横断集約） ===")
    print(conn.intelligence.insights())


if __name__ == "__main__":
    main()
