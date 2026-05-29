# x402 Gateway Development Plan

## Goal

`x402-gateway` lets provider APIs become x402-paid APIs without changing provider application code. Providers are described with YAML, loaded by the gateway, exposed through stable `/providers/<provider>/<path>` routes, and discoverable through generated catalog files.

## Architecture

```text
Provider files
  -> config loader
  -> provider registry
  -> FastAPI gateway
  -> facilitator verify / settle
  -> upstream API

Provider listing files
  -> catalog builder
  -> dist/skills.json
  -> CLI / agent discovery
```

## Implemented Scope

- File-backed provider registry.
- Single-provider and multi-provider startup.
- Paid and free endpoint routing.
- x402 challenge generation.
- Facilitator verify and settle client integration.
- Upstream proxying with request and response header filtering.
- Header, query parameter, HMAC, OAuth2, and access-token upstream auth strategies.
- Management endpoints for health, providers, endpoints, and verify-only debugging.
- Catalog generate, check, build, and search commands.
- Dockerfile and Docker Compose support.
- Local mock facilitator for development.

## Provider Onboarding Flow

```text
Provider sends onboarding information
  -> operator creates provider.yml and listing.md
  -> operator validates provider.yml
  -> gateway is deployed or restarted
  -> provider appears in /__402/providers
  -> endpoints appear in /__402/endpoints
  -> catalog build publishes discovery artifacts
```

The first implementation is operator-managed. A self-service provider portal can be added later without changing the gateway data model: it would still create or update provider records that map to the same runtime fields.

## Buyer Flow

```text
Buyer or agent searches catalog
  -> selects provider endpoint
  -> calls gateway URL
  -> receives 402 challenge when payment is missing
  -> signs payment with x402 SDK / CLI / agent wallet
  -> retries request with payment header
  -> gateway verifies and settles through facilitator
  -> gateway forwards request upstream
  -> buyer receives upstream response
```

## Deployment Shape

The official service runs the same gateway container used for local development. Provider files are mounted or included in the image. The local Compose stack also starts a mock facilitator so the payment path can be debugged before the official facilitator is finalized.

```bash
docker compose up --build -d gateway
```

## Current Status

The project is ready for debugging. Core development is in place; remaining work is integration validation, real provider onboarding, real facilitator testing, and CI hardening.
