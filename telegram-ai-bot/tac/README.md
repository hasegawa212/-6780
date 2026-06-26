# TAC — Twilio Agent Connect 参照実装

Twilio のグローバル通信ファブリックと、お好みの会話型 AI エージェントをつなぐ
**インテリジェント・ミドルウェア**の参照実装です。Twilio が複雑な通信を担い、
あなたはビジネスロジックを完全にコントロールします。

このパッケージは Twilio の 3 つのソリューション設計図を、ひとつの動く Python
コードベースに落とし込んだものです。

| # | 設計図 | このコードでの実体 |
|---|--------|--------------------|
| 1 | **対話型エージェント**（マルチチャネル自律/半自律ワークフロー） | `connector.py`（ライフサイクル・ブリッジ・推論ループ・配信） |
| 2 | **人間エージェントへのシームレスなハンドオフ**（Flex / Studio） | `handoff.py` ＋ `tools.py` の `escalate_to_human` |
| 3 | **人間エージェント拡張**（Conversation Intelligence／言語演算子） | `operators.py` ＋ `intelligence.py` |

> 認証情報が一切なくても `import tac`・単体テスト・`demo.py` が動きます。
> `ANTHROPIC_API_KEY` があれば実際に Claude が応答・要約します。Twilio 認証情報と
> Studio Flow SID があれば実ハンドオフまで通ります。無い項目は安全に degrade します。

---

## アーキテクチャ（設計図の「仕組み」に対応）

```
 チャネル          Twilioプラットフォーム               顧客インフラ
┌─────────┐   ┌──────────────────────────────┐   ┌──────────────┐
│ Voice   │   │ Conversation Orchestrator     │   │  あなたのLLM   │
│ SMS     │──▶│  → 単一 Conversation 記録     │──▶│  + ビジネス     │
│ WhatsApp│   │ TAC Connector(ミドルウェア)    │◀──│   ロジック      │
│ Chat    │   │ Conversation Intelligence     │   └──────────────┘
└─────────┘   │ Memory / Knowledge            │
              │ Studio / Flex（ハンドオフ）     │
              └──────────────────────────────┘
```

1. **初期化/オーケストレーション** — `TACConnector.start()` がチャネルイベントを
   単一の `Conversation`（models.py）にまとめ、Memory プロファイルで顧客を識別。
2. **コンテキスト・エンリッチ（ブリッジ）** — LLM を呼ぶ前に `MemoryStore.enrich()`
   が traits（顧客情報）＋ observations（履歴）＋ Knowledge（社内ナレッジ意味検索）を
   標準化した文脈にまとめる。
3. **推論ループ** — 充実したプロンプトを Claude に送り、ツール（`escalate_to_human`,
   `schedule_callback` 等）の呼び出しを実行。
4. **実行と配信** — 応答を各チャネル形式（音声は TwiML）に変換して返す。同時に
   Conversation Intelligence がリアルタイム／事後で言語演算子を回し、人間エージェント
   支援シグナルと会話インサイトを生成、結果を Memory へ書き戻す。

---

## クイックスタート

```bash
cd telegram-ai-bot
python3.13 -m venv venv && source venv/bin/activate
pip install -r tac/requirements.txt
cp tac/.env.example tac/.env   # 値を設定（無くても demo/test は動く）

# 認証情報なしでライフサイクルを体験
python tac/demo.py

# 単体テスト（外部接続なし・11 ケース）
pytest tests/test_tac.py

# Webhook サーバー（音声＋メッセージング）
python -m tac.server          # http://localhost:8090
```

### 設定の確認（接続チェッカー）

実認証情報を `.env` に入れたら、疎通を確認できます（秘密はマスク表示）。

```bash
python -m tac.check          # 設定状況だけ表示（ネットワークなし）
python -m tac.check --live   # Twilio へ実問い合わせ: 認証・電話番号・Studio フローを検証
```

> 認証情報はコードに書かず `.env`（gitignore 済み）にのみ置きます。秘密が一度でも
> チャットやログ・コミットに平文で出たら、漏洩として **Auth Token / API Key を即ローテーション**してください。

### コードから使う

```python
from tac import TACConnector, Channel

conn = TACConnector()
conn.start("CA123", Channel.VOICE, customer_identity="+819012345678", goal="解約相談")
res = conn.handle("CA123", "プレミアム会員ですが解約したい。違約金は？")
print(res.text)              # AI 応答
print(res.assist)            # リアルタイム支援シグナル（感情・次の一手）
# 顧客が人間を希望すると LLM が escalate_to_human を呼び res.handed_off=True
signals = conn.close("CA123")  # 事後サマリー・スクリプト遵守
print(conn.intelligence.insights())  # 会話横断の集約インサイト
```

---

## コンポーネント

| ファイル | 役割 |
|----------|------|
| `models.py` | Orchestrator の記録モデル（Conversation / Communication / 役割付き Participant）。チャネル非依存の単一記録。 |
| `memory.py` | Conversation Memory（traits/observations）＋ Enterprise Knowledge（意味検索）。`enrich()` がブリッジ。 |
| `operators.py` | GenAI 言語演算子。標準4種（**Sentiment / Summary / Next Best Response / Script Adherence**）＋ `CustomOperator`。 |
| `intelligence.py` | ルールセットで演算子を **realtime / milestone / on_close** に発火。会話インサイトを集約。 |
| `tools.py` | ユニバーサルツール（Anthropic tool-use スキーマ）。`escalate_to_human`・`schedule_callback`。 |
| `handoff.py` | Flex / Studio へのシームレスなハンドオフ。AI 要約＋プロファイルをタスク属性へ。 |
| `connector.py` | 中核ミドルウェア。ライフサイクルと推論ループ。 |
| `server.py` | Flask Webhook（`/tac/voice`, `/tac/message`, `/tac/assist/<sid>`, `/tac/insights`）。 |

---

## 設計図2：人間エージェントへのハンドオフ手順

このコードは **コード側**（要約生成・コンテキストのパッケージ化・Studio フロー起動・
二重配信防止のためのステータス遷移）を担います。Twilio コンソール側の設定は次のとおり
（[Escalate to a human agent](https://www.twilio.com/docs/conversations/agent-connect/escalate-to-human-agent) 準拠）。

1. **Studio**：新規フローで *Twilio Agent Connect - Agent Handoff* テンプレートを選択し、
   `Send to Flex` ウィジェットのワークフロー／タスクチャネル／属性を調整して公開。
   その Flow SID を `TWILIO_STUDIO_HANDOFF_FLOW_SID` に設定。
2. **Conversations(Classic)**：対象アドレスでインバウンド自動作成を有効にし、
   バーチャルエージェントが先に応対できるよう既存の Flex 連携を切断。
3. **Conversation Orchestrator**：Flex の Conversations(Classic) サービス SID を登録し、
   音声のライフサイクルを *On hangup → Closed* に設定。
4. **Conversation Intelligence**：非アクティブ時と終了時に *Summary* を生成するルールを作成。
5. **Flex**：Beta のオプトインで「音声/メッセージング用 仮想エージェント概要」を有効化。

`escalate_to_human` ツールが呼ばれると、本実装は会話の **AI 要約 + 顧客 traits + ルーティング
属性** を組み立て（`HandoffPackage.task_attributes()`）、Studio Execution を起動して
タスク属性として渡します。`SendToFlex` 実行で会話は `handed-off` になり、`onMessageAdded`
webhook が外れて二重配信を防ぎます。顧客は同じ通話/チャットのまま、担当者は即座に AI 要約を
受け取ります。

### Studio フローをコードで生成・登録する（`studio_flow.py`）

上記 step 1 の Studio フローは、コンソール手作業の代わりに `tac/studio_flow.py` で
生成・公開できます。ハンドオフ（`handoff.py`）が積む属性キーと、フローの `SendToFlex`
タスク属性キーが整合するように作られています。

```bash
# 定義 JSON を出力（音声 / メッセージング）
python -m tac.studio_flow --channel voice --workflow WWxxxx --task-channel TCxxxx
python -m tac.studio_flow --channel messaging --workflow WWxxxx --task-channel TCxxxx

# Twilio REST で作成・公開し、Flow SID を得る（要 TWILIO 認証情報）
python -m tac.studio_flow --channel voice --workflow WWxxxx --task-channel TCxxxx --create
# 出力された FWxxxx を TWILIO_STUDIO_HANDOFF_FLOW_SID に設定
```

- **音声**: `Trigger → Set Variables → SendToFlex`
- **メッセージング**: `Trigger → HTTP(conversationSid) → HTTP(serviceSid) → ResumeConversation → SendToFlex`

認証情報が無い/`TAC_DRY_RUN=true` なら REST を呼ばず、送信予定のペイロードと定義を返します。
account 固有の SID（Flex Workflow / Task Channel）を含むため、本番では公式テンプレートの
利用も検討してください。本ジェネレータは即デプロイ可能な出発点を提供します。

---

## 設計図3：人間エージェント拡張のユースケース

同じ会話・実行モデルで、低摩擦の事後ユースケースからリアルタイム支援まで段階導入できます。

- **まとめエージェント支援** — `on_close()` の Summary でアフターコール作業を削減。担当者は
  AI ノートを出発点に編集・補足でき、記録は担当者が完全に管理。
- **リアルタイムエージェント支援** — `on_utterance()` の Sentiment / Next Best Response を
  `/tac/assist/<sid>` 経由でエージェントデスクトップへ。流れを止めず一貫した応対を支援。
- **リアルタイムワークフロー自動化** — Script Adherence のコンプライアンスリスクや感情急落を
  条件に下流処理（上長エスカレーション等）を起動。
- **コンタクトセンター QA** — `intelligence.insights()` がサマリー・感情スコア・遵守シグナルを
  会話横断で集約し、コーチング・分析・レポートへ。

---

## degrade の挙動（認証情報が無いとき）

| 未設定 | 挙動 |
|--------|------|
| `ANTHROPIC_API_KEY` | LLM 応答・演算子はプレースホルダ／既定値を返す（クラッシュしない） |
| Twilio 認証 / Flow SID | ハンドオフはドライラン：会話を `handed-off` にしタスク属性を返すが Studio は起動しない |
| `SUPABASE_URL` | Knowledge はローカル登録文書のキーワード検索にフォールバック |

`TAC_DRY_RUN=true` で外部呼び出しを明示的に抑止できます。

---

## テスト

```bash
pytest tests/test_tac.py     # 11/11 passed（外部接続不要）
```

models・memory・handoff のパッケージ化・intelligence の集約・tools・connector の
ライフラインを、LLM/Twilio 認証情報なしで検証します。

## 本番化（常駐運用）

開発用の Flask サーバーではなく gunicorn で常駐させます。

```bash
set -a; source tac/.env; set +a
./tac/run_prod.sh                                  # フォアグラウンド
# 常駐: nohup ./tac/run_prod.sh > /tmp/tac-prod.log 2>&1 &
```

> ⚠️ **ワーカーは 1 プロセス固定（`-w 1`）**。TACConnector は会話状態を
> プロセス内メモリに持つため、複数ワーカーだと状態が分裂します。同時通話は
> スレッド（`--threads`、既定 8）で捌きます。水平スケールするには会話状態を
> 共有ストア（Redis 等）へ外出しする改修が必要です。

### 公開URL（ngrok 常設 / 代替）
- ngrok 無料の URL は再起動で変わります。常設するには **ngrok の予約ドメイン**
  （`ngrok http 8090 --domain=your-name.ngrok-free.app`）や有料プランを使うか、
  クラウド（Fly.io / Render / Cloud Run 等）へデプロイして固定URLを得ます。
- URL を変えたら Twilio 番号の Voice Webhook を更新（`studio_flow` 同梱の curl 例、
  または `IncomingPhoneNumbers` API で `VoiceUrl` を再設定）。

### セキュリティ
- 開発中にチャット/ログへ出た認証情報（Twilio Auth Token / Anthropic / OpenAI /
  Telegram 等）は**漏洩扱い**。本番前に各コンソールで **Rotate（再生成）**し、
  新しい値だけを `.env`（gitignore 済み）に置く。
- `.env` や `service_account.json` などの秘密ファイルは**絶対にコミット/公開しない**。

## ConversationRelay（双方向ストリーミング音声・最も人間らしい）

`<Gather>` 方式（録音→認識→生成→再生の往復）に対し、ConversationRelay は
WebSocket で**話しながら同時に処理**でき、割り込み（barge-in）が自然です。

```bash
pip install flask-sock        # WebSocket に必要
# 番号の Voice Webhook を /tac/voice（Gather方式）→ /tac/voice-relay に変更
```

- `GET/POST /tac/voice-relay` … `<Connect><ConversationRelay url="wss://<host>/tac/relay" …>` を返す
- `WS /tac/relay` … `setup`/`prompt`/`interrupt` を処理。`prompt` の文字起こしを
  推論ループへ渡し、`text` トークンを返して TTS させる。ハンドオフ時は `end`＋
  `handoffData`（タスク属性）で TwiML へ戻し `<Enqueue>` で担当者へ。
- 声/挨拶は `TAC_RELAY_VOICE` / `TAC_RELAY_WELCOME` で調整。

> gunicorn で WS を扱うにはワーカークラスに注意（`flask-sock` は WSGI 上で動くが、
> 本番の同時通話数次第で `gevent`/`eventlet` ワーカー等の検討が必要）。
