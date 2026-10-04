# ADR-0004: Telephony Provider の抽象化

- Status: Accepted
- Date: 2026-10-04

## Context
現行は Twilio REST を `outbound.py` から直接呼んでいる。業務ロジック（DNC・時間帯・上限）と
プロバイダ呼び出しが同じ関数にあり、テストでは monkeypatch で差し替えている。
候補になるプロバイダ：
- Twilio（Programmable Voice + ConversationRelay）: 既存アカウント・承認済み KYC・日本語 TTS の実績あり
- OpenAI Realtime API の SIP 接続: `realtime.call.incoming` Webhook → `/v1/realtime/calls/{call_id}/accept`、
  `/refer`（転送）、`/hangup`（終話）。音声どうしで直接やり取りする（speech-to-speech）ので低遅延
  （エンドポイントは実装時に公式ドキュメントで再確認する）
- その他 SIP トランク

## Decision
アプリケーション層に `TelephonyProvider` ポートを定義し、アダプタで実装する。

```ts
interface TelephonyProvider {
  readonly name: string;
  createCall(req: CreateCallRequest): Promise<ProviderCall>;   // idempotencyKey 必須
  endCall(providerCallId: string): Promise<void>;
  transferCall(providerCallId: string, target: TransferTarget): Promise<void>;
  getCall(providerCallId: string): Promise<ProviderCall>;
}
```

- `MockTelephonyProvider`: テスト・ローカル・デモの既定。実際の電話はかけず、呼び出しを記録する。
- `TwilioTelephonyProvider`: Phase 10。
- `OpenAISipTelephonyProvider`: Phase 11（Twilio の SIP トランク経由で OpenAI につなぐ構成も候補）。
- 音声のストリーミングは `VoiceSession` ポートとして分ける（通話制御と会話処理は寿命も障害の起き方も違うため）。

`APP_ENV=test` のときは、Mock 以外のアダプタを生成した時点で例外にする（テストから本番回線を呼ばない保証）。

## Consequences
- プロバイダの癖（ステータス名・Webhook 形式）はアダプタ内で正規化し、ドメインには出さない。
- 各アダプタは録画済みフィクスチャを使ったコントラクトテストを持つ。
