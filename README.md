# x402-gateway

## 中文说明

`x402-gateway` 是服务方自托管的 x402 支付网关。它读取本地 `provider.yml`，在付费接口前返回 `402 Payment Required`，通过 facilitator 完成支付校验和结算，然后把请求转发到真实上游 API。

社区用户只需要安装并记住 `x402-cli`。Gateway 相关能力都集成在：

```bash
x402-cli gateway ...
```

常用入口：

- 本地启动和测试：见 [`DEPLOYMENT.md`](DEPLOYMENT.md)
- Provider 示例：见 [`examples/README.md`](examples/README.md)
- 生成公开 catalog 文件：`x402-cli catalog export-gateway ...`

注意：`provider.yml`、`.env`、上游 API key、钱包私钥只保存在服务方自己的 Gateway 运行环境，不提交到公开 Catalog 仓库。

## English

`x402-gateway` is a YAML-driven payment gateway for provider APIs. It runs in front of normal HTTP APIs, returns x402 `402 Payment Required` challenges for paid endpoints, verifies and settles payments through a facilitator, then forwards the request to the upstream API.

For community users, the main entrypoint is `x402-cli`. Gateway commands are exposed as `x402-cli gateway ...`; the `x402-gateway` package remains the underlying runtime and compatibility entrypoint.

Provider copy-paste examples live in [`examples/README.md`](examples/README.md).
Test environment deployment steps live in [`DEPLOYMENT.md`](DEPLOYMENT.md).

The `0.6.1` beta service shape is in place: provider files are persisted in this repository, the runtime loads multiple providers from `providers/`, public catalog files can be exported for `x402-catelog`, and Docker Compose starts a local gateway plus a local mock facilitator.

## Current Features

- Multi-provider loading from `providers/**/provider.yml`.
- Paid reverse proxy routes at `/providers/<provider>/<endpoint-path>`.
- x402 challenge, facilitator verify, facilitator settle, and upstream forwarding.
- Per-endpoint prices from `provider.yml`.
- Upstream authentication injection from environment variables.
- Provider recipient splits for vendor or affiliate settlement metadata.
- Management endpoints:
  - `/__402/health`
  - `/__402/providers`
  - `/__402/endpoints`
  - `/__402/verify`
- Catalog tooling through `x402-cli gateway catalog ...`.
- Docker support for the official gateway service shape.
- Local mock facilitator support for development.

## Repository Layout

```text
src/                         Gateway runtime, config loader, catalog CLI
providers/<provider>/         Provider onboarding files managed by operators
examples/                     Starter provider.yml and listing.md examples
deploy/                       Container support files
tests/                        Unit and smoke tests
```

Each provider directory contains:

```text
providers/acme-weather/
  provider.yml
  listing.md
```

`provider.yml` is the runtime source of truth. `listing.md` is catalog metadata for discovery.

## Run Locally

Start the gateway and the local mock facilitator:

```bash
cp .env.example .env
docker compose up --build -d gateway
```

Check the service:

```bash
curl http://127.0.0.1:4020/__402/health
curl http://127.0.0.1:4020/__402/providers
curl http://127.0.0.1:4020/__402/endpoints
curl http://127.0.0.1:4021/supported
```

Default local ports:

```text
Gateway:      http://127.0.0.1:4020
Facilitator:  http://127.0.0.1:4021
Upstream:     http://127.0.0.1:8080
```

The default `.env.example` points providers at:

```text
X402_FACILITATOR_URL=http://facilitator:4021
X402_GATEWAY_PUBLIC_BASE_URL=http://host.docker.internal:4020
```

For production, replace this with the official facilitator URL.

## Provider Onboarding

Early onboarding is operator-managed. A provider sends the required API and payment information to the team, then an operator adds or updates:

```text
providers/<provider-name>/provider.yml
providers/<provider-name>/listing.md
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

If the endpoint is paid and the request has no x402 payment header, the gateway returns `402 Payment Required`.

The Compose stack also runs a demo upstream API. This lets the local gateway test a real proxy target:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:4020/providers/acme-weather/health
curl -i http://127.0.0.1:4020/providers/acme-weather/v1/current?city=Singapore
```

The first two calls should return `200`. The paid weather endpoint should return `402` until the client retries with a valid x402 payment header.

## Catalog Usage

Build static catalog artifacts:

```bash
docker compose --profile tools run --rm catalog-build
```

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

The paid endpoint should return `402 Payment Required` until called through
`x402-cli pay` with a configured wallet.

Output:

```text
dist/skills.json
dist/providers/<provider>.json
```

Search the local catalog:

```bash
x402-cli gateway catalog search providers weather
```

The same `dist/skills.json` can be consumed by `x402-cli gateway search`.

The public marketplace catalog lives in `x402-catelog`. A provider should not
submit `provider.yml`; after running their own gateway, they export public PR
files with:

```bash
x402-cli catalog export-gateway https://gateway.example.com \
  --provider acme-weather \
  --output-dir providers/acme-weather
```

The exported files are:

```text
providers/acme-weather/catalog.json
providers/acme-weather/pay.md
```

Those files contain public service, endpoint, price, and gateway URL metadata
only. Upstream auth, API keys, `.env`, and `provider.yml` stay on the provider's
machine.

## Official Service Shape

The official gateway service is a long-running HTTP process:

```text
Buyer / Agent
  -> https://gateway.bankofai.io/providers/<provider>/<path>
  -> x402-gateway
  -> provider upstream API

Operator
  -> commit providers/<provider>/provider.yml + listing.md
  -> deploy/restart gateway
  -> inspect /__402/providers and /__402/endpoints
```

At this stage, no database is required. Provider configuration, catalog metadata, prices, upstream URLs, and recipient addresses are persisted as files in this repository. Runtime state is rebuilt from files on startup.

## Test Readiness Checklist

The implementation is ready for debugging. The remaining work is validation, not core scaffolding:

- Run paid request flows against the local mock facilitator.
- Run at least one testnet flow against a real facilitator.
- Replace example provider values with real provider data.
- Confirm upstream authentication for each provider.
- Publish the generated catalog to the location used by CLI or agent tooling.
- Add CI jobs for provider validation and catalog build.

## Useful Commands

```bash
x402-cli gateway start --providers-dir providers
x402-cli gateway check providers/acme-weather/provider.yml
x402-cli gateway scaffold acme-weather --output-dir providers/acme-weather

x402-cli gateway catalog scaffold acme-weather https://api.example.com/openapi.json
x402-cli gateway catalog generate providers/acme-weather/provider.yml
x402-cli gateway catalog pay-assets providers/acme-weather/provider.yml
x402-cli gateway catalog check providers
x402-cli gateway catalog build providers --dist-dir dist
x402-cli gateway catalog search providers weather
x402-cli catalog export-gateway http://127.0.0.1:4020 --provider acme-weather

docker compose up --build -d gateway
docker compose ps
docker compose logs -f gateway facilitator
docker compose --profile tools run --rm catalog-build
```
