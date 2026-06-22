# Gateway Implementation Notes

This document describes the implemented gateway shape for `0.6.1`.

## Runtime Model

`x402-gateway` runs as a reverse proxy in front of provider APIs. It loads
provider configuration from one file or from a provider directory:

```bash
x402-cli gateway start providers/acme-weather/provider.yml
x402-cli gateway start --providers-dir providers
```

In directory mode, every `providers/**/provider.yml` file becomes one provider
in the in-memory registry. Configuration is persisted as files. Runtime registry
state is rebuilt on startup.

## Request Flow

```text
Buyer / Agent
  -> GET /providers/<provider>/<path>
  -> gateway endpoint matcher
  -> price resolver
  -> x402 challenge if payment is missing
  -> facilitator verify if payment is present
  -> facilitator settle
  -> upstream auth injection
  -> upstream HTTP request
  -> response to buyer
```

Free endpoints skip verify and settle.

## Provider File

`provider.yml` contains:

- provider identity: `name`, `title`, `description`, `category`, `version`
- upstream base URL: `forward_url`
- upstream auth strategy: `routing.auth`
- payment terms: `operator.network`, `operator.currencies`,
  `operator.recipient`, `operator.scheme`
- facilitator URL: `operator.facilitator_url`
- endpoint list and pricing: `endpoints[]`
- optional recipient aliases and splits: `recipients`

Environment variables can be used with `${VAR}` placeholders. Missing variables
fail startup or validation.

## Management Endpoints

```text
/__402/health      Liveness check, returns "ok"
/__402/providers   Loaded providers, status, signer, and load errors
/__402/endpoints   Endpoint definitions with price, network, and currency
/__402/verify      Verify-only endpoint for local debugging
```

## Local Development

Docker Compose starts the gateway, a local mock facilitator, and a demo upstream:

```bash
docker compose up --build -d gateway
```

Services:

```text
gateway       http://127.0.0.1:4020
facilitator   http://127.0.0.1:4021
upstream      http://127.0.0.1:8080
```

The example provider reads `X402_FACILITATOR_URL`, which defaults to
`http://facilitator:4021` inside Compose.

## Debugging Focus

The basic implementation is complete enough to enter integration debugging. The
active focus is:

- paid request flow with the local facilitator
- provider-specific upstream auth behavior
- client IP forwarding behavior
- real facilitator compatibility
- live network settlement smoke tests
