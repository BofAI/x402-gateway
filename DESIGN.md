# x402 Gateway Design

## Summary

`x402-gateway` is a provider-side HTTP gateway for paid APIs. Operators mount
private provider YAML files into the gateway runtime. The gateway reads those
files on startup, exposes paid proxy routes, coordinates x402 verification and
settlement through a facilitator, and forwards successful requests to upstream
provider APIs.

## Design Principles

- File-backed provider configuration for the first production shape.
- No database in the gateway runtime at this stage.
- Provider funds settle to provider-controlled recipient addresses.
- Local development must run without external infrastructure.
- Production deployment should reuse the same container entrypoint.
- Runtime secrets must stay outside source control.

## Components

### Gateway Runtime

Responsibilities:

- load provider YAML
- validate provider schema and pricing
- register providers and endpoints
- match `/providers/<provider>/<path>` routes
- return x402 challenges
- verify and settle payments through the configured facilitator
- inject upstream authentication
- forward upstream requests
- forward client IP headers to upstream services
- expose management endpoints

### Local Facilitator

The Docker Compose development stack includes a mock facilitator. It supports
`/supported`, `/verify`, `/settle`, and control endpoints for debugging. This is
a development dependency only. Production should point `X402_FACILITATOR_URL` at
the selected facilitator.

### Demo Upstream

The local Docker Compose stack also includes a demo upstream API. The sample
provider points `forward_url` at this service so debugging covers the full path:

```text
client -> gateway -> facilitator -> gateway -> demo upstream
```

The demo upstream exposes `/health` and `/v1/current`. `/v1/current` requires the
gateway-injected bearer token, which verifies that upstream authentication
injection works.

## Data Model

Provider runtime fields:

- `name`
- `title`
- `description`
- `category`
- `version`
- `forward_url`
- `routing`
- `operator`
- `recipients`
- `endpoints`

Runtime behavior is driven by provider YAML and environment variables.

## Persistence

The gateway does not require a database for the current phase. Persistent inputs
are:

- `providers/**/provider.yml`
- environment variables or secret manager values

On restart, the gateway rebuilds its runtime registry from provider files.

## Deployment

Local:

```bash
docker compose up --build -d gateway
```

Production-style container:

```bash
docker build -t x402-gateway .
docker run \
  -p 4020:8080 \
  -e X402_FACILITATOR_URL=https://facilitator.example.com \
  -v "$PWD/providers:/app/providers:ro" \
  x402-gateway
```

## Debugging Entry Points

```text
/__402/health
/__402/providers
/__402/endpoints
/__402/verify
```

Use the local mock facilitator log to inspect verify and settle calls:

```bash
curl http://127.0.0.1:4021/control/log
```

## Completion Criteria

Basic development is complete when:

- gateway and local facilitator start from Docker Compose
- provider files validate
- gateway management endpoints work
- paid routes return x402 challenges
- free routes proxy successfully to the demo upstream
- upstream authentication injection works
- client IP headers are forwarded upstream
- local facilitator receives verify and settle calls during paid-flow tests
