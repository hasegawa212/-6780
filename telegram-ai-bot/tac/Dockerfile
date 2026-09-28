# TAC Webhook サーバー本番イメージ（着信AI＋発信ブリッジ）。
#
# ビルドコンテキストは telegram-ai-bot（fly deploy を実行するディレクトリ）。
# fly.toml の dockerfile="Dockerfile" は fly.toml のある tac/ 基準で tac/Dockerfile
# を指すが、ビルドの context は実行ディレクトリ(telegram-ai-bot)なので COPY は tac/ 付き。
#   fly deploy -c tac/fly.toml -a <app>    ← telegram-ai-bot ディレクトリで実行
# 手元 Docker で試すなら telegram-ai-bot を context に:
#   docker build -f tac/Dockerfile -t tac .
#   docker run -p 8090:8090 --env-file tac/.env tac
#
# 注意: TACConnector は会話状態をプロセス内メモリに持つため worker は 1 に固定。
# 同時通話はスレッドで捌く（gthread）。ConversationRelay の WebSocket も gthread で動く。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8090

WORKDIR /app

# 依存を先に入れてレイヤキャッシュを効かせる（context=telegram-ai-bot なので tac/ 付き）。
# 併せて gunicorn が確実に入っているかをビルド時に検証する（未導入なら即失敗）。
COPY tac/requirements.txt /app/tac/requirements.txt
RUN pip install -r /app/tac/requirements.txt \
    && python -c "import gunicorn; print('gunicorn', gunicorn.__version__)"

# アプリ本体（tac パッケージ）
COPY tac /app/tac

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
