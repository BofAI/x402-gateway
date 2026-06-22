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

### External Dependencies

The gateway integrates with an external facilitator and provider-owned upstream
APIs. Docker Compose in this repository starts only the gateway process:

```text
client -> gateway -> facilitator
client -> gateway -> provider upstream API
```

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

## Completion Criteria

Basic development is complete when:

- gateway starts from Docker Compose
- provider files validate
- gateway management endpoints work
- paid routes return x402 challenges
- free routes proxy successfully to provider upstream APIs
- upstream authentication injection works
- client IP headers are forwarded upstream
- facilitator receives verify and settle calls during paid-flow tests
