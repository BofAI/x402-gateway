# x402 Gateway Development Plan

## Goal

`x402-gateway` lets provider APIs become x402-paid APIs without changing
provider application code. Providers are described with private YAML files,
loaded by the gateway, and exposed through stable
`/providers/<provider>/<path>` routes.

## Architecture

```text
Provider YAML
  -> config loader
  -> provider registry
  -> FastAPI gateway
  -> facilitator verify / settle
  -> upstream API
```

## Implemented Scope

- File-backed provider registry.
- Single-provider and multi-provider startup.
- Paid and free endpoint routing.
- x402 challenge generation.
- Facilitator verify and settle client integration.
- Upstream proxying with request and response header filtering.
- Header, query parameter, HMAC, OAuth2, and access-token upstream auth
  strategies.
- Client IP forwarding to upstream services.
- Management endpoints for health, providers, endpoints, and verify-only
  debugging.
- Dockerfile and Docker Compose support.

## Provider Onboarding Flow

```text
Provider sends onboarding information
  -> operator creates provider.yml
  -> operator validates provider.yml
  -> gateway is deployed or restarted
  -> provider appears in /__402/providers
  -> endpoints appear in /__402/endpoints
```

The first implementation is operator-managed. A self-service provider portal can
be added later without changing the gateway data model: it would still create or
update provider records that map to the same runtime fields.

## Buyer Flow

```text
Buyer or agent calls gateway URL
  -> receives 402 challenge when payment is missing
  -> signs payment with x402 SDK, CLI, or agent wallet
  -> retries request with payment header
  -> gateway verifies and settles through facilitator
  -> gateway forwards request upstream
  -> buyer receives upstream response
```

## Deployment Shape

The official service runs the same gateway container used for local development.
Provider files are mounted or included in the image.

```bash
docker compose up --build -d gateway
```

## Current Status

The project is ready for integration debugging. Core development is in place;
remaining work is real provider onboarding, facilitator compatibility testing,
live network settlement validation, and CI hardening.
