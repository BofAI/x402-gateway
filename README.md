# x402 Gateway

`x402-gateway` is a self-hosted reverse proxy for paid HTTP APIs. It loads
private provider runtime YAML, returns x402 `402 Payment Required` challenges
for paid endpoints, verifies and settles payment through a facilitator, and
then forwards successful requests to the upstream API.

Provider runtime files, `.env` files, upstream API keys, bearer tokens, wallet
private keys, and internal upstream URLs must stay in the gateway operator's
runtime environment.

## Features

- Multi-provider loading from `providers/**/provider.yml`.
- Paid reverse proxy routes at `/providers/<provider>/<endpoint-path>`.
- x402 challenge, facilitator verify, facilitator settle, and upstream proxying.
- Per-endpoint prices from provider YAML.
- Upstream authentication injection from environment variables.
- Provider recipient metadata for settlement routing.
- Client IP forwarding to upstream services.
- Management endpoints for health, provider state, endpoint state, and
  verify-only debugging.
- Docker and Docker Compose support.

## Repository Layout

```text
src/                         Gateway runtime and config loader
providers/<provider>/         Provider runtime files managed by operators
examples/                     Starter provider.yml example
deploy/                       Container support files
tests/                        Unit and smoke tests
```

Each provider directory contains a runtime config:

```text
providers/acme-weather/
  provider.yml
```

`provider.yml` is the runtime source of truth. It defines upstream routing,
payment terms, endpoint pricing, and upstream authentication.

## Run Locally

Start the gateway:

```bash
cp .env.example .env
docker compose up --build -d gateway
```

Check the service:

```bash
curl http://127.0.0.1:4020/__402/health
curl http://127.0.0.1:4020/__402/providers
curl http://127.0.0.1:4020/__402/endpoints
```

Default local ports:

```text
Gateway:      http://127.0.0.1:4020
```

The default `.env.example` uses a placeholder facilitator URL:

```text
X402_FACILITATOR_URL=https://admin-facilitator.bankofai.io/
X402_GATEWAY_PUBLIC_BASE_URL=http://host.docker.internal:4020
```

For production, replace this with the production facilitator URL and a public
gateway base URL.

## Provider Onboarding

Early onboarding is operator-managed. An operator adds or updates:

```text
providers/<provider-name>/provider.yml
```

Validate a provider:

```bash
X402_FACILITATOR_URL=http://127.0.0.1:4021 \
  x402-cli gateway check providers/acme-weather/provider.yml
```

Start from local Python instead of Docker:

```bash
X402_FACILITATOR_URL=http://127.0.0.1:4021 \
  x402-cli gateway start --providers-dir providers --host 0.0.0.0 --port 4020
```

Buyer or agent request path:

```text
GET /providers/<provider-name>/<endpoint-path>
```

Example:

```bash
curl http://127.0.0.1:4020/providers/acme-weather/v1/current
```

If the endpoint is paid and the request has no x402 payment header, the gateway
returns `402 Payment Required`.

## Container Startup

Build and start the local demo stack:

```bash
cp .env.example .env
docker compose build gateway upstream facilitator
docker compose up -d gateway
```

Check runtime state:

```bash
docker compose ps
curl http://127.0.0.1:4020/__402/health
curl http://127.0.0.1:4020/__402/providers
curl http://127.0.0.1:4020/__402/endpoints
curl -i 'http://127.0.0.1:4020/providers/acme-weather/v1/current?city=Singapore'
```

The paid endpoint should return `402 Payment Required` until called by a client
that retries with a valid x402 payment header.

## Management Endpoints

```text
/__402/health      Liveness check
/__402/providers   Loaded providers, signer status, and load errors
/__402/endpoints   Loaded endpoint definitions and prices
/__402/verify      Verify-only endpoint for local debugging
```

## Client IP Forwarding

The gateway forwards the resolved caller IP to upstream services through:

```text
x-real-ip
x-client-ip
x-forwarded-for
```

Resolution priority is:

1. `CF-Connecting-IP`
2. the first value in `X-Forwarded-For`
3. `X-Real-IP`
4. the socket client host seen by the gateway

When `CF-Connecting-IP` is present, the gateway treats it as authoritative and
sets `x-forwarded-for` to that value before forwarding upstream.

## Security Notes

- Keep provider YAML, `.env`, upstream auth values, and wallet material private.
- Inject upstream credentials from environment variables or a secret manager.
- Do not store bearer tokens or private upstream URLs in public examples.
- Put the gateway behind TLS in production.
- Configure the production facilitator URL explicitly.

## Useful Commands

```bash
x402-cli gateway start --providers-dir providers
x402-cli gateway check providers/acme-weather/provider.yml
x402-cli gateway scaffold acme-weather --output-dir providers/acme-weather
docker compose up --build -d gateway
```
