# x402 Gateway Deployment

This document describes how to deploy the gateway in development, test, and
production-like environments. The gateway loads local `provider.yml` files,
returns x402 `402 Payment Required` challenges for paid endpoints, verifies and
settles payments through a facilitator, and proxies successful requests to
upstream APIs.

Keep provider YAML, `.env`, upstream API keys, bearer tokens, wallet private
keys, and internal upstream URLs in the gateway runtime environment only.

## 1. Clone

```bash
git clone git@github.com:BofAI/x402-gateway.git
cd x402-gateway
```

## 2. Deployment Modes

The repository supports two practical modes:

```text
Sandbox mode      Local facilitator, no real on-chain payment.
Live mode         External facilitator and real recipient addresses.
```

Start with sandbox mode for API and payment-flow debugging, then switch to live
mode when facilitator and wallet configuration are ready.

## 3. Sandbox Mode

Sandbox mode uses `docker-compose.yml` and starts:

```text
gateway
mock facilitator
demo upstream
```

Create a local environment file:

```bash
cp .env.example .env
```

Make sure `.env` contains at least:

```bash
X402_GATEWAY_PUBLIC_BASE_URL=http://host.docker.internal:4020
X402_FACILITATOR_URL=http://facilitator:4021
ACME_API_TOKEN=demo-upstream-token
```

Build and start:

```bash
docker compose build gateway upstream facilitator
docker compose up -d gateway
```

You can also use the Docker Hub test image published by CI:

```bash
export X402_GATEWAY_IMAGE=bankofai/x402-gateway:test
docker compose pull gateway
docker compose up -d --no-build gateway
```

Image publishing rules:

```text
Git tag: test-v* -> Docker image: bankofai/x402-gateway:test
Git tag: v*      -> Docker image: bankofai/x402-gateway:<tag>
```

GitHub Actions requires these repository secrets:

```text
DOCKERHUB_USERNAME
DOCKERHUB_TOKEN
```

Default port mapping:

```text
0.0.0.0:4020 -> container 8080
```

Log locations:

```text
gateway container:          /app/log/gateway.log
mock facilitator container: /app/log/mock-facilitator.log
host:                       ./log/
```

Inspect logs:

```bash
docker compose logs -f gateway
tail -f ./log/gateway.log
```

Health checks:

```bash
docker compose ps
curl -fsS http://127.0.0.1:4020/__402/health
curl -fsS http://127.0.0.1:4020/__402/providers
curl -fsS http://127.0.0.1:4020/__402/endpoints
curl -fsS http://127.0.0.1:4021/supported
```

Paid endpoint check:

```bash
curl -i "http://127.0.0.1:4020/providers/acme-weather/v1/current?city=Singapore"
```

The expected unpaid response is `402 Payment Required`.

## 4. Live Mode

Live mode does not use the mock facilitator. Configure an external facilitator
and real recipient addresses in provider YAML.

Required inputs:

```text
X402_FACILITATOR_URL
X402_GATEWAY_PUBLIC_BASE_URL
operator.recipient in provider.yml
wallet funds for the selected network
accepted token balance for the selected network
```

Example `.env`:

```bash
X402_GATEWAY_PUBLIC_BASE_URL=https://gateway.example.com
X402_FACILITATOR_URL=https://facilitator.example.com
X402_GATEWAY_PORT=4020
```

Validate provider configuration:

```bash
x402-cli gateway check providers/acme-weather/provider.yml
```

Build an image:

```bash
docker build -t x402-gateway:live .
```

Start the gateway:

```bash
docker run -d \
  --name x402-gateway \
  --restart unless-stopped \
  --env-file .env \
  -p 4020:8080 \
  -v "$(pwd)/providers:/app/providers:ro" \
  -v "$(pwd)/log:/app/log" \
  x402-gateway:live \
  sh -c 'mkdir -p /app/log && x402-gateway server start \
    --providers-dir /app/providers \
    --host 0.0.0.0 \
    --port 8080 \
    --quiet 2>&1 | tee -a /app/log/gateway.log'
```

Check the running service:

```bash
curl -fsS http://127.0.0.1:4020/__402/health
curl -fsS http://127.0.0.1:4020/__402/providers
curl -fsS http://127.0.0.1:4020/__402/endpoints
tail -n 20 ./log/gateway.log
```

Public URL check:

```bash
curl -fsS https://gateway.example.com/__402/health
```

## 5. Reverse Proxy

Nginx example:

```nginx
server {
    listen 443 ssl;
    server_name gateway.example.com;

    location / {
        proxy_pass http://127.0.0.1:4020;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 6. CLI Validation

Install:

```bash
python3 -m pip install --upgrade pip
python3 -m pip install --pre bankofai-x402-cli==0.6.1b1
```

If the beta package is not published yet, install from branches:

```bash
python3 -m pip install \
  "bankofai-x402-gateway @ git+https://github.com/BofAI/x402-gateway.git@feature/0.6.1-gateway-init"

python3 -m pip install \
  "bankofai-x402-cli @ git+https://github.com/BofAI/x402-cli.git@feature/0.6.1-gateway-align"
```

Call a paid gateway URL:

```bash
x402-cli pay \
  "https://gateway.example.com/providers/acme-weather/v1/current?city=Singapore"
```

Restrict to a specific network when needed:

```bash
x402-cli pay \
  --network eip155:56 \
  "https://gateway.example.com/providers/acme-weather/v1/current?city=Singapore"
```

## 7. Validation Checklist

```text
Gateway container starts successfully.
/__402/health returns 200.
/__402/providers lists the expected providers.
/__402/endpoints lists the expected endpoint paths and prices.
Unpaid requests return 402 Payment Required.
Paid requests verify, settle, and proxy upstream.
Upstream auth headers are injected only by the gateway.
Client IP headers are forwarded to upstream.
Gateway logs do not contain secrets.
```

## 8. Rollback

Container rollback:

```bash
docker rm -f x402-gateway
git checkout <previous-good-commit>
docker build -t x402-gateway:live .
docker run ...
```

Compose rollback:

```bash
git checkout <previous-good-commit>
docker compose build gateway upstream facilitator
docker compose up -d gateway
```
