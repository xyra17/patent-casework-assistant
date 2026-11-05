# syntax=docker/dockerfile:1.6
# 前端构建阶段
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
COPY config/session-examples.json /config/session-examples.json
RUN npm run build

# 应用运行阶段
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_NO_COMPILE=1 \
    HF_HOME=/app/models \
    TRANSFORMERS_CACHE=/app/models \
    SENTENCE_TRANSFORMERS_HOME=/app/models

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# 先复制完整源码，确保 setuptools 的包目录存在；再把前端构建产物覆盖进去。
COPY . .
RUN pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir --no-compile --index-url https://download.pytorch.org/whl/cpu "torch>=2.2" \
    && pip install --no-cache-dir --no-compile .
RUN mkdir -p data output upload models vector_store knowledge_base \
    && find /usr/local/lib/python3.12 -type d -name __pycache__ -prune -exec rm -rf {} + \
    && find /usr/local/lib/python3.12/site-packages -type d \( -name tests -o -name test \) -prune -exec rm -rf {} + \
    && rm -rf /usr/local/lib/python3.12/site-packages/pip* \
       /usr/local/lib/python3.12/site-packages/setuptools* \
       /usr/local/lib/python3.12/site-packages/wheel* \
    && rm -rf /root/.cache /tmp/* \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
COPY --from=frontend-build --chown=appuser:appuser /frontend/dist /app/frontend/dist

USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1
CMD ["python", "main.py"]
