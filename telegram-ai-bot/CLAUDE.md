# CLAUDE.md — TAC 自動フォロー / さくら発信 開発の長期記憶

このファイルと `docs/` が AI の長期記憶。**毎回巨大プロンプトを貼らない**。
作業開始時はまず `docs/PROGRESS.md` → `docs/IMPLEMENTATION_PLAN.md` → 関連する `docs/*` を読む。

## 最上位原則（AGENTS.md 準拠）
1. **作り直さない** — 既存を活かす。動いてる機能を壊さない。
2. **先にバックアップ / TDD** — 変更前に。RED→GREEN→REFACTOR→REGRESSION を守る。
3. **実行確認なしに完成と言わない** — テスト green を証拠に報告。推測を事実にしない。

## これは何
不動産買取再販 Martial Arts の **AIテレアポ（自動フォロー）システム**。
バックれ/日程未定の見込み客へ、同意・DNC・時間帯・回数の安全ゲートを通過した相手だけに
自動連続発信し、応答したら会話AI「さくら」(ライフパートナー) が対応、必要なら担当者へ生転送する。

- 言語/基盤: Python / Flask (`tac/server.py`) + Twilio + OpenAI Realtime
- ブランチ: `feature/sakura-max`（main は旧コード注意）
- 本番: Fly app `tac-martial-arts`（手順 `tac/DEPLOY.md`）
- テスト: `pytest`（2026-10-06 時点 346 test 関数）

## 絶対に勝手にやらないこと（明示承認が必要）
- 実顧客への実発信 / Production Secret 変更 / 不可逆操作 / 有料処理の大量実行
- `autofollow_enabled` と「自動運転(auto)」は既定 OFF。ON にするのは人の明示指示のみ。

## 安全装置（既に実装済み・壊さない）
- **OFF by default**: `TAC_AUTOFOLLOW_ENABLED=False`、scheduler も OFF。
- **DNC 最優先**: `tac/dnc.py` / `tac/dnc.txt`。断られた相手に再発信しない。
- **同意**: `tac/consent.py`（拒否は発信しない）。
- **時間帯ガード**: `tac/calling_hours.py`（`TAC_ENFORCE_CALL_HOURS`、JST基準）。
- **回数上限**: 1相手 1日1回 + 合計 `follow_cap=2`。バッチ上限 `autofollow_batch_max=10`。
- **レート制限**: `tac/rate_limit.py`。
- **留守電スキップ(AMD)** + **担当不在フォールバック**: `tac/outbound.py` / `tac/realtime.py`。

## 開発の約束
- 実電話は使わず **Fake/モックで検証**（全テストが実発信をモック）。
- 変更後は必ず該当テストを実行。PII（電話番号/氏名）はコミットしない（`calls.jsonl` 等は gitignore）。
- 秘密情報はコード/リポジトリに置かず env（Fly secrets）のみ。
