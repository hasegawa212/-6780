# ADR-0005: 通話の回線状態と「話している主体」を分けて持つ

- Status: Accepted
- Date: 2026-10-04

## Context
依頼文の通話状態の例は `QUEUED / DIALING / RINGING / ANSWERED / AI_ACTIVE / HUMAN_ACTIVE / ENDING / COMPLETED / FAILED / CANCELLED` で、
回線の状態（つながったか）と会話の主体（AI か人か）が1つの列挙に混ざっている。
しかし両者は変わるきっかけが違う。回線の状態はプロバイダの Webhook（重複・順序の入れ替えがある）で変わり、
主体はオペレーターの Take Over や Safety 状態で変わる。1つにまとめると、
「AI_ACTIVE のときに RINGING の古い Webhook が届いたらどうするか」のような組み合わせが爆発する。

## Decision
3つに分けて持つ。
- **CallJob**（キュー）：`PENDING` など。依頼文の `QUEUED` はここ
- **Call**（回線）：`REQUESTED → DIALING → RINGING → IN_PROGRESS → ENDED | FAILED | NO_ANSWER | BUSY | CANCELED`
- **Conversation**（会話）：フェーズ（DISCLOSURE … / Safety）と `controller = AI | HUMAN`

対応表は VOICE.md「状態の対応」に置く。

## Alternatives
- 依頼文どおり1つの列挙にする：組み合わせの検証が増え、Webhook の順序の入れ替えで AI/HUMAN の情報を失う危険がある。却下。

## Consequences
- 画面の表示（「AI が話しています」「人が対応中」）は Call と Conversation を合わせて作る。
- 各状態機械を独立にプロパティベーステストできる。
