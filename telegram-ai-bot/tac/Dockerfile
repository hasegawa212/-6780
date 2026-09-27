# TAC Webhook サーバー本番イメージ（着信AI＋発信ブリッジ）。
#
# ビルドは telegram-ai-bot ディレクトリを context にする:
#   docker build -f tac/Dockerfile -t tac .
# 起動:
#   docker run -p 8090:8090 --env-file tac/.env tac
#
# 注意: TACConnector は会話状態をプロセス内メモリに持つため worker は 1 に固定。
# 同時通話はスレッドで捌く（gthread）。ConversationRelay の WebSocket も gthread で動く。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8090

WORKDIR /app

# 依存を先に入れてレイヤキャッシュを効かせる
COPY tac/requirements.txt /app/tac/requirements.txt
RUN pip install -r /app/tac/requirements.txt

# アプリ本体（tac パッケージ）
COPY tac /app/tac

EXPOSE 8090

# shell 形式で $PORT を展開。1 worker 固定・gthread で WS 対応。
CMD gunicorn \
    --workers 1 \
    --worker-class gthread \
    --threads "${TAC_GUNICORN_THREADS:-8}" \
    --timeout "${TAC_GUNICORN_TIMEOUT:-60}" \
    --bind "0.0.0.0:${PORT}" \
    --access-logfile - \
    tac.server:app
