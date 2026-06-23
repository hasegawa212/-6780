# BC自動生成サービス（FastAPI + openpyxl + anthropic）
FROM python:3.11-slim

WORKDIR /app

# 依存（先にインストールしてレイヤキャッシュを効かせる）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリ本体（テンプレWBやPIIはイメージに含めない＝実行時にマウント）
COPY *.py ./
COPY tests ./tests

ENV BC_PORT=8800 \
    BC_TEMPLATE_DIR=/templates

EXPOSE 8800

# 本番ワークブックは実行時に -v $(pwd)/templates:/templates でマウントする。
# ANTHROPIC_API_KEY 等は --env-file .env で渡す。
CMD ["sh", "-c", "uvicorn bc_service:app --host 0.0.0.0 --port ${BC_PORT}"]
