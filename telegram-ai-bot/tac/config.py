"""TAC (Twilio Agent Connect) の設定。すべて環境変数から読み込む。

このパッケージは「設計図」をそのままコードに落とした参照実装です。Twilio の
実アカウントが無くても import・単体テストが通るよう、認証情報はすべて任意で、
未設定なら各機能が安全に degrade（要約はLLMのみ、ハンドオフはドライラン）します。
"""

from __future__ import annotations

import os


def _bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Config:
    """環境変数ベースの設定（属性アクセス）。"""

    # --- LLM (顧客インフラ側の推論) ---
    anthropic_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    model: str = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")
    # 通話は低遅延優先、要約/オペレーターは品質優先で別モデルにできる
    operator_model: str = os.environ.get(
        "TAC_OPERATOR_MODEL", os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")
    )
    effort: str = os.environ.get("CLAUDE_EFFORT", "low")
    max_tokens: int = int(os.environ.get("CLAUDE_MAX_TOKENS", "400"))
    # 最先端の回答用: サーバーサイド Web 検索（最新情報に対応）。
    # web_search_20260209 は Opus 4.8/4.7/4.6・Sonnet 4.6 で利用可。
    web_search: bool = _bool("TAC_WEB_SEARCH", False)
    web_search_type: str = os.environ.get("TAC_WEB_SEARCH_TYPE", "web_search_20260209")
    web_search_max_uses: int = int(os.environ.get("TAC_WEB_SEARCH_MAX_USES", "3"))

    # --- LLM プロバイダ切替（Claude / オープンソースモデル） ---
    # "anthropic"（既定）= Claude。"openai" = OpenAI 互換 API（Ollama/vLLM/LM Studio 等）
    llm_provider: str = os.environ.get("TAC_LLM_PROVIDER", "anthropic").strip().lower()
    openai_base_url: str = os.environ.get("TAC_OPENAI_BASE_URL", "http://localhost:11434/v1")
    # 既定は qwen2.5（多言語・日本語が強く、軽量〜中量で品質が高い）。
    # より軽くしたいなら llama3.1、より賢くしたいなら qwen2.5:14b 等に変更可。
    openai_model: str = os.environ.get("TAC_OPENAI_MODEL", "qwen2.5")
    openai_api_key: str = os.environ.get(
        "TAC_OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", "ollama")
    )

    # --- Twilio 認証 (Conversations / Studio / Flex) ---
    twilio_account_sid: str = os.environ.get("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.environ.get("TWILIO_AUTH_TOKEN", "")
    # Conversations(Classic) サービス SID (ISxxxx) — オーケストレーター連携用
    conversations_service_sid: str = os.environ.get("TWILIO_CONVERSATIONS_SERVICE_SID", "")
    # ハンドオフ先 Studio フロー (Agent Handoff テンプレート) SID (FWxxxx)
    studio_handoff_flow_sid: str = os.environ.get("TWILIO_STUDIO_HANDOFF_FLOW_SID", "")
    # SMS/チャットの Studio 実行を開始する際の発信元番号/送信者
    handoff_from: str = os.environ.get("TWILIO_HANDOFF_FROM", "")
    # Flex/TaskRouter ワークフロー SID (WWxxxx)。音声ハンドオフでライブ通話を
    # <Enqueue workflowSid> で直接このワークフローへ転送し、担当者へ橋渡しする。
    flex_workflow_sid: str = os.environ.get("TWILIO_FLEX_WORKFLOW_SID", "")

    # --- ConversationRelay（双方向ストリーミング音声・自然な割り込み） ---
    # 既定は voice 未指定で language=ja-JP の日本語デフォルト音声に任せる
    # （ttsProvider/voice を誤指定すると英語にフォールバックするため）。
    # 特定の声を使いたいときだけ両方を env で設定する。
    relay_tts_provider: str = os.environ.get("TAC_RELAY_TTS_PROVIDER", "")
    relay_voice: str = os.environ.get("TAC_RELAY_VOICE", "")
    relay_welcome: str = os.environ.get(
        "TAC_RELAY_WELCOME", "お電話ありがとうございます。さくらです。ご用件をうかがいます。"
    )

    # --- Memory / Knowledge (Supabase 上のベクトル検索を流用) ---
    supabase_url: str = os.environ.get("SUPABASE_URL", "")
    supabase_service_key: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    openai_key: str = os.environ.get("OPENAI_API_KEY", "")  # 埋め込み用

    # --- 挙動 ---
    # 認証情報が無い場合に外部呼び出しを実際には行わずログだけ出す
    dry_run: bool = _bool("TAC_DRY_RUN", False)
    # エージェントが守るべきスクリプト/ポリシー(Script Adherence オペレーター用)
    agent_script: str = os.environ.get("TAC_AGENT_SCRIPT", "")
    persona: str = os.environ.get(
        "AGENT_PERSONA",
        "あなたは礼儀正しく、的確で、共感的なカスタマーサポート担当です。",
    )

    @property
    def has_twilio(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token)

    @property
    def has_memory(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)


CONFIG = Config()
