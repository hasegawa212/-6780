# TESTING.md

## 方針
- **TDD 必須**: RED → GREEN → REFACTOR → REGRESSION。
- 実発信・実ネットワークは**必ずモック**。本物の電話は絶対に鳴らさない。
- 判断ロジックは純粋関数に切り出してユニットテスト（`autofollow` / `realtime` / `followup`）。

## 実行
```bash
cd tac-6780/telegram-ai-bot
python -m pytest -q                 # 全テスト（2026-10-06: 346 test 関数）
python -m pytest tests/test_autofollow.py -q
python -m pytest tests/test_realtime.py -q
```
依存が足りない場合: `pip install flask pytest --break-system-packages`。

## 主なテスト
- `test_autofollow.py` / `test_autofollow_api.py` / `test_autofollow_scheduler.py`: エンジン・API・常駐。
- `test_realtime.py`: さくらの会話品質（g711_ulaw無変換・VADチューニング・日本語文字起こし・
  barge-in truncate・転送ツール・フォローアップ挨拶）。
- `test_amd_outbound.py` / `test_handoff_voice.py`: AMD・生転送。
- `test_followup.py`: 分類・ingest（overwrite 補完）。
- `test_dnc.py` / `test_consent.py` / `test_calling_hours.py` / `test_rate_limit.py`: 安全ゲート。

## これから追加する観点（IMPLEMENTATION_PLAN）
- Webhook idempotency: 重複 / 遅延 / 順序逆転（Duplicate / Delayed / Out-of-order）の再現と防御。
