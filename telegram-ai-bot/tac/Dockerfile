# TAC Webhook サーバー本番イメージ（着信AI＋発信ブリッジ）。
#
# ビルドコンテキストは tac/ ディレクトリ（fly.toml と同じ場所）。
# Fly.io は fly.toml のあるディレクトリを基準にビルドするため、それに合わせている。
#   fly deploy -c tac/fly.toml
# 手元 Docker で試すなら tac/ を context に:
#   docker build -f tac/Dockerfile -t tac tac
#   docker run -p 8090:8090 --env-file tac/.env tac
#
# 注意: TACConnector は会話状態をプロセス内メモリに持つため worker は 1 に固定。
# 同時通話はスレッドで捌く（gthread）。ConversationRelay の WebSocket も gthread で動く。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8090

WORKDIR /app

# 依存を先に入れてレイヤキャッシュを効かせる（context=tac/ なので requirements.txt は直下）。
# 併せて gunicorn が確実に入っているかをビルド時に検証する（未導入なら即失敗）。
COPY requirements.txt /app/tac/requirements.txt
RUN pip install -r /app/tac/requirements.txt \
    && python -c "import gunicorn; print('gunicorn', gunicorn.__version__)"

# アプリ本体（tac パッケージ）。context=tac/ の中身を /app/tac に配置する。
COPY . /app/tac

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
