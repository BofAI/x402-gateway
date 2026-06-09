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

COPY --chown=x402-gateway:users providers/ ./providers/

USER x402-gateway

EXPOSE 4020

CMD ["sh", "-c", "mkdir -p /app/log && x402-gateway server start --providers-dir /app/providers --host 0.0.0.0 --port 4020 --quiet 2>&1 | tee -a /app/log/gateway.log"]
