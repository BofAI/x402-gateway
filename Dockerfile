FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    HOME=/home/x402-gateway

ARG X402_SDK_SPEC="bankofai-x402>=0.6.0"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip install --upgrade pip \
    && pip install "${X402_SDK_SPEC}" \
    && pip install .

RUN useradd -r -u 1000 -g users -s /usr/sbin/nologin x402-gateway \
    && mkdir -p /app/providers /app/dist /app/log /home/x402-gateway \
    && chown -R x402-gateway:users /app /home/x402-gateway

USER x402-gateway

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/__402/ready', timeout=3).read()"

CMD ["x402-gateway", "server", "start", "--providers-dir", "/app/providers", "--host", "0.0.0.0", "--port", "8080", "--quiet"]
