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
    # 音声通話だけ別モデルにして低遅延化（例: claude-haiku-4-5）。空なら CLAUDE_MODEL。
    voice_model: str = os.environ.get("TAC_VOICE_MODEL", "")
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

    # --- アウトバウンド発信（click-to-call ブリッジ） ---
    # 発信元に使う Twilio 番号（購入済みの自番号）。例: +16592103801
    caller_id: str = os.environ.get("TAC_CALLER_ID", "")
    # あなた（担当者）の電話番号。相手が出たら保留にして、この番号を鳴らし、
    # あなたが出た時点で通話が始まる。例: +818094662479
    agent_number: str = os.environ.get("TAC_AGENT_NUMBER", "")
    # 担当者名簿（複数担当者）。"名前:+81...,名前:+81..." のカンマ区切り。名前は省略可。
    # 未設定なら単一の TAC_AGENT_NUMBER にフォールバック。発信ごとに名前/番号で選べ、
    # 未指定ならラウンドロビンで自動振り分け（各担当者1件ずつ＝人数分の並行発信）。
    agents: str = os.environ.get("TAC_AGENTS", "")
    # 発信 API(/tac/call) の操作者トークン。公開URL(ngrok)から誰でも叩けてしまうと
    # 口座課金の発信を勝手に起こされるため、必須。未設定なら発信 API は無効化する。
    outbound_token: str = os.environ.get("TAC_OUTBOUND_TOKEN", "")
    # DNC（発信禁止リスト）の保存ファイル。断られた相手への再発信を仕組みで防ぐ。
    # 1行1番号（E.164推奨）。存在しなければ空リスト扱い。
    dnc_file: str = os.environ.get("TAC_DNC_FILE", "tac/dnc.txt")
    # 架電記録（Call Log）の保存ファイル（JSONL）。監査証跡・運用可視化用。
    # 電話番号を含むため gitignore。本番は永続ボリューム上のパスを推奨。
    calllog_file: str = os.environ.get("TAC_CALLLOG_FILE", "tac/calls.jsonl")
    # Webhook 冪等性ガードの処理済み記録（JSON）。Twilio の重複/遅延/順序逆転
    # コールバックで二重計上・二重 redirect しないよう、処理済み CallSid+イベントを
    # 短期記憶する。本番は永続ボリューム上のパスを推奨。
    idempotency_file: str = os.environ.get("TAC_IDEMPOTENCY_FILE", "tac/idempotency.json")
    # スマートリスト（優先順発信リスト）の保存ファイル（JSON）。
    queue_file: str = os.environ.get("TAC_QUEUE_FILE", "tac/queue.json")
    # 自動フォロー台帳（分類済みのお客様記録）の保存ファイル（JSON）。
    # 出典・最終更新・フォロー回数を保存。本番は永続ボリューム上のパスを推奨。
    follow_file: str = os.environ.get("TAC_FOLLOW_FILE", "tac/followup.json")
    # 自動フォローの合計回数の上限（1相手あたり）。0以下＝無制限。既定2（設計: 合計2回まで）。
    follow_cap: int = int(os.environ.get("TAC_FOLLOW_CAP", "2"))
    # 自動フォロー架電エンジンの状態（ON/一時停止）の保存ファイル（JSON）。
    autofollow_file: str = os.environ.get("TAC_AUTOFOLLOW_FILE", "tac/autofollow_state.json")
    # 自動フォロー架電エンジンの初期状態。既定 OFF（＝明示的に ON にするまで実発信しない）。
    # 安全装置: 同意済み・時間帯内・上限内のお客様にだけ、ON のとき発信する。
    autofollow_enabled: bool = _bool("TAC_AUTOFOLLOW_ENABLED", False)
    # 連続オート発信の1回あたりの上限件数（暴走防止）。0以下は安全のため既定にフォールバック。
    autofollow_batch_max: int = int(os.environ.get("TAC_AUTOFOLLOW_BATCH_MAX", "10"))
    # 連続オート発信の各発信の間隔（秒）。相手・回線への配慮。既定0（間隔なし）。
    autofollow_batch_pause_sec: float = float(os.environ.get("TAC_AUTOFOLLOW_BATCH_PAUSE_SEC", "0"))
    # 無人の常駐オート運転: 定期的に連続発信バッチを自動実行する間隔（秒）。既定300（5分）。
    autofollow_auto_interval_sec: int = int(os.environ.get("TAC_AUTOFOLLOW_AUTO_INTERVAL_SEC", "300"))
    # 常駐スケジューラ（バックグラウンド）を起動するか。既定 OFF（＝明示的に ON にするまで
    # 常駐しない）。ON でも実発信は runtime の「自動運転(auto)」トグルが ON の時だけ。
    autofollow_scheduler: bool = _bool("TAC_AUTOFOLLOW_SCHEDULER", False)
    # 成約/高スコア通知の Webhook URL。空＝無効（デフォルトOFF）。
    notify_webhook: str = os.environ.get("TAC_NOTIFY_WEBHOOK", "")
    # 発信時間帯ガード。常識外の時間（夜間・早朝）の発信を仕組みで止める（特定商
    # 取引法・迷惑防止への配慮）。既定 OFF（後方互換）。ON のとき、ローカル時
    # （call_hours_utc_offset 時間ずらした時刻）が [start, end) の範囲外なら発信を
    # ブロックする。日本は DST が無いため UTC オフセット（既定 +9=JST）で扱う。
    enforce_call_hours: bool = _bool("TAC_ENFORCE_CALL_HOURS", False)
    call_hours_start: int = int(os.environ.get("TAC_CALL_HOURS_START", "9"))
    call_hours_end: int = int(os.environ.get("TAC_CALL_HOURS_END", "21"))
    call_hours_utc_offset: int = int(os.environ.get("TAC_CALL_HOURS_UTC_OFFSET", "9"))
    # 発信の1日上限（レート制限）。掛けすぎ（迷惑・コスト）を仕組みで防ぐ。
    # 0 以下＝無効（無制限）。1 以上なら「当日の dialed 件数 < 上限」でのみ発信可。
    # 当日の境界は call_hours_utc_offset（既定 JST=UTC+9）を使う。
    daily_call_cap: int = int(os.environ.get("TAC_DAILY_CALL_CAP", "0"))
    # 着信 Webhook の Twilio 署名検証。ON にすると X-Twilio-Signature を検証し、
    # なりすましリクエストを 403 で弾く。既定 OFF（開発/後方互換）。本番は ON 推奨。
    verify_twilio_signature: bool = _bool("TAC_VERIFY_TWILIO_SIGNATURE", False)
    # ngrok 等の裏側だと Flask から見える URL が実URLと異なることがある。Twilio が
    # 署名した実際の公開 URL のベース（例 https://xxx.ngrok-free.dev）を明示できる。
    public_base_url: str = os.environ.get("TAC_PUBLIC_BASE_URL", "")

    # --- 通話録音と同意 ---
    # 録音する場合は ON。ON のとき、通話冒頭で必ず録音の同意告知を入れる（同意なき
    # 録音を避けるため）。既定 OFF。
    record_calls: bool = _bool("TAC_RECORD_CALLS", False)
    recording_consent_text: str = os.environ.get(
        "TAC_RECORDING_CONSENT_TEXT",
        "この通話は、サービス品質向上のため録音させていただきます。",
    )

    # --- 勧誘に先立つ名乗り（勧誘目的の明示） ---
    # ON のとき、発信で相手が出た直後（担当者につなぐ前）に会社名・担当者名・商品の種類・
    # 勧誘目的を自動で告げる。ON で項目が欠けていれば発信しない。既定 OFF（後方互換）。
    disclosure_enabled: bool = _bool("TAC_DISCLOSURE_ENABLED", False)
    company_name: str = os.environ.get("TAC_COMPANY_NAME", "")
    # 担当者名の既定値（発信ごとに agent_name で上書きできる）
    agent_name: str = os.environ.get("TAC_AGENT_NAME", "")
    solicitation_product: str = os.environ.get("TAC_SOLICITATION_PRODUCT", "")
    # ---- 生活意識調査モード「ライフパートナー」（金融リテラシー・保険の見直しの意識調査） ----
    # 既定 OFF。実施事業者・発信元番号・保険代理店・調査結果の利用目的がそろわないと発信しない（tac/survey.py）
    survey_enabled: bool = _bool("TAC_SURVEY_ENABLED", False)
    survey_company: str = os.environ.get("TAC_SURVEY_COMPANY", "")
    # 調査は不動産とは別の事業者が行うので、発信元番号も分ける（TAC_CALLER_ID は使わない）
    survey_caller_id: str = os.environ.get("TAC_SURVEY_CALLER_ID", "")
    # 保険の見直しの案内を行う、登録済みの保険代理店の名称（AI は商品の推奨・勧誘をしない）
    survey_insurance_agency: str = os.environ.get("TAC_SURVEY_INSURANCE_AGENCY", "")
    survey_purpose: str = os.environ.get(
        "TAC_SURVEY_PURPOSE", "金融教育の資料づくりと、ご希望の方への情報提供"
    )
    # 調査の記録（同意・回答の選択肢・撤回・引き継ぎ）。電話番号を含むので本番は /data に置く
    survey_file: str = os.environ.get("TAC_SURVEY_FILE", "tac/survey.json")
    # 調査の対象者（連絡許可の証跡つき）。不動産の顧客名簿とは別に、実施事業者が用意する
    survey_list_file: str = os.environ.get("TAC_SURVEY_LIST_FILE", "tac/survey_list.json")
    # 調査の質問の版（tac/survey_questions.py の SETS）。v2 = A〜E の 5 分野
    survey_question_set: str = os.environ.get("TAC_SURVEY_QUESTION_SET", "v2")
    # ライフパートナーの CRM（SQLite）。電話番号・同意・回答の選択肢を含むので本番は /data に置く
    lp_db_file: str = os.environ.get("TAC_LP_DB_FILE", "tac/lifepartner.db")
    # 調査の電話を同時に掛ける本数の上限（発信中・通話中の合計）
    survey_max_concurrent: int = int(os.environ.get("TAC_SURVEY_MAX_CONCURRENT", "1") or "1")
    # 調査の回答（選択肢）の保存期間（日）。過ぎたら lp_db.purge_expired で消す
    lp_retention_days: int = int(os.environ.get("TAC_LP_RETENTION_DAYS", "365") or "365")
    # 緊急停止スイッチの状態（再起動なしで全発信を止める）。本番は /data に置く
    kill_switch_file: str = os.environ.get("TAC_KILL_SWITCH_FILE", "tac/kill_switch.json")

    # --- 電話5問 → 仮ランク（sales-rank） ---
    # ON のとき、さくらに record_screening 道具を持たせ、相手が自分から話した内容から
    # 仮ランクを判定して記録する（相手には伝えない）。既定 OFF。
    screening_enabled: bool = _bool("TAC_SCREENING_ENABLED", False)
    screening_file: str = os.environ.get("TAC_SCREENING_FILE", "tac/screenings.jsonl")
    # sales-rank の場所（空ならリポジトリ直下の sales-rank/）
    sales_rank_dir: str = os.environ.get("TAC_SALES_RANK_DIR", "")

    # --- ConversationRelay（双方向ストリーミング音声・自然な割り込み） ---
    # 既定は Google 最上位の Chirp3-HD（超自然な日本語）。万一英語に
    # フォールバックする場合は TAC_RELAY_VOICE=ja-JP-Neural2-B に戻せる。
    relay_tts_provider: str = os.environ.get("TAC_RELAY_TTS_PROVIDER", "Google")
    relay_voice: str = os.environ.get("TAC_RELAY_VOICE", "ja-JP-Chirp3-HD-Aoede")

    # 御社の実情報（営業時間・料金・商品・FAQ 等）。設定するとシステムプロンプトへ
    # 注入し、的確に回答する。テキスト/Markdown ファイルのパス。
    business_info_file: str = os.environ.get("TAC_BUSINESS_INFO_FILE", "")
    relay_welcome: str = os.environ.get(
        "TAC_RELAY_WELCOME", "お電話ありがとうございます！AI音声案内担当、ライフパートナーです。今日はどうされましたか？"
    )

    # --- Memory / Knowledge (Supabase 上のベクトル検索を流用) ---
    supabase_url: str = os.environ.get("SUPABASE_URL", "")
    supabase_service_key: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    openai_key: str = os.environ.get("OPENAI_API_KEY", "")  # 埋め込み用

    # --- 通話録音の文字起こし＋AI要約 ---
    # 録音完了後に OpenAI で文字起こし→ Anthropic で要約する。両キー未設定なら安全にスキップ。
    transcribe_model: str = os.environ.get("TAC_TRANSCRIBE_MODEL", "whisper-1")

    # --- 留守電自動判定（AMD） ---
    # Twilio Answering Machine Detection。machine 判定時は会議に入れず不在記録に倒す。
    amd_enabled: bool = _bool("TAC_AMD_ENABLED", False)

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
