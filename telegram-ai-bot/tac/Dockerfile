# TAC Webhook サーバー本番イメージ（着信AI＋発信ブリッジ）。
#
# ビルドコンテキストはリポジトリ直下（fly deploy を実行するディレクトリ）。
# 判定ロジックの sales-rank/ を同じイメージに入れるため（判定を二重化しない）。
# イメージの中もリポジトリと同じ並び（/app/telegram-ai-bot/tac と /app/sales-rank）にする。
# 入れるファイルはリポジトリ直下の .dockerignore（許可リスト方式）で絞る。
#   fly deploy -c telegram-ai-bot/tac/fly.toml -a <app>    ← リポジトリ直下で実行
# 手元 Docker で試すならリポジトリ直下を context に:
#   docker build -f telegram-ai-bot/tac/Dockerfile -t tac .
#   docker run -p 8090:8090 --env-file telegram-ai-bot/tac/.env tac
#
# 注意: TACConnector は会話状態をプロセス内メモリに持つため worker は 1 に固定。
# 同時通話はスレッドで捌く（gthread）。ConversationRelay の WebSocket も gthread で動く。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8090

WORKDIR /app/telegram-ai-bot

# 依存を先に入れてレイヤキャッシュを効かせる（context=リポジトリ直下）。
# 併せて gunicorn が確実に入っているかをビルド時に検証する（未導入なら即失敗）。
COPY telegram-ai-bot/tac/requirements.txt /app/telegram-ai-bot/tac/requirements.txt
RUN pip install -r /app/telegram-ai-bot/tac/requirements.txt \
    && python -c "import gunicorn; print('gunicorn', gunicorn.__version__)"

# 判定ロジック（sales-rank の .py だけ。.dockerignore で絞る）とアプリ本体（tac パッケージ）
COPY sales-rank/ /app/sales-rank/
COPY telegram-ai-bot/tac /app/telegram-ai-bot/tac

EXPOSE 8090

# gunicorn をモジュール実行する（$PATH に依存せず確実に起動）。
# 1 worker 固定・gthread で WS 対応。shell 形式で $PORT を展開。
CMD python -m gunicorn.app.wsgiapp \
    --workers 1 \
    --worker-class gthread \
    --threads "${TAC_GUNICORN_THREADS:-8}" \
    --timeout "${TAC_GUNICORN_TIMEOUT:-60}" \
    --bind "0.0.0.0:${PORT}" \
    --access-logfile - \
    tac.server:app
