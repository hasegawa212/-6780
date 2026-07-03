#!/usr/bin/env python3
"""自動読取(/extract)の疎通診断ツール。

BC自動生成アプリと同じ環境（.env を読んだ状態）で実行すると、
ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / CLAUDE_MODEL を確認し、
最小の Claude 呼び出しを1回試して **成功/失敗の正確な原因** を表示する。

  cd bc-pipeline && source .venv/bin/activate
  set -a; source .env; set +a
  python3 diag.py

読み取りが失敗する時、まずこれを実行して出力を共有すれば原因が特定できる。
（サーバ本体・APIには影響しない。標準ライブラリ＋anthropic のみ使用。）
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    key = os.environ.get("ANTHROPIC_API_KEY")
    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    model = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")

    print("=== 自動読取 診断 ===")
    masked = f"設定あり（{key[:6]}…{key[-4:]}）" if key and len(key) > 12 else \
        ("設定あり" if key else "未設定 ← これが原因")
    print(f"  ANTHROPIC_API_KEY : {masked}")
    print(f"  ANTHROPIC_BASE_URL: {base}")
    print(f"  CLAUDE_MODEL      : {model}")

    if not key:
        print("\n→ APIキーが環境にありません。以下を実行してから再度お試しください：")
        print("   set -a; source .env; set +a && python3 diag.py")
        return 1
    try:
        from anthropic import Anthropic
    except ImportError:
        print("\n→ anthropic 未導入。`.venv` を有効化して `pip install -r requirements.txt`。")
        return 1

    print("\n最小リクエストを送信中…（数秒）")
    try:
        client = Anthropic(timeout=60.0, max_retries=1)
        msg = client.messages.create(
            model=model, max_tokens=16,
            messages=[{"role": "user", "content": "「OK」とだけ返してください。"}],
        )
        txt = "".join(b.text for b in msg.content
                      if getattr(b, "type", None) == "text").strip()
        print(f"✅ 成功：応答 = {txt[:40]!r}")
        print("→ Claude 接続は正常です。個別PDFで読取が失敗する場合は、その PDF 固有の")
        print("   問題（極端に重い/暗号化/破損）です。アプリは自動でテキスト抽出・頁分割に")
        print("   フォールバックし、それでも駄目なら手入力で続行できます。")
        return 0
    except Exception as e:  # noqa: BLE001 診断目的で全例外を表示
        status = getattr(e, "status_code", None)
        name = type(e).__name__
        low = str(e).lower()
        print(f"❌ 失敗：{('HTTP ' + str(status) + ' ') if status else ''}{name}")
        print(f"   詳細：{str(e)[:400]}")
        print("   ── 推定原因 ──")
        if status == 401 or "authentication" in low or "x-api-key" in low:
            print("   APIキーが無効/期限切れ。有効な ANTHROPIC_API_KEY に差し替えてください。")
        elif status == 404 or "not_found" in low or "model" in low:
            print(f"   モデル '{model}' がこのキー/接続先で使えない可能性。")
            print("   CLAUDE_MODEL を見直すか、社内プロキシ利用時は base_url 側のモデル名を確認。")
        elif status in (403, 407) or "proxy" in low or "forbidden" in low:
            print("   プロキシ/接続許可の問題。api.anthropic.com への egress 許可を確認。")
        elif "connect" in low or "timeout" in low or "resolve" in low:
            print("   ネットワーク/DNS/タイムアウト。base_url と回線を確認。")
        else:
            print("   上記詳細を共有してください（原因を特定します）。")
        return 2


if __name__ == "__main__":
    sys.exit(main())
