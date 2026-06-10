# Gateway Implementation Notes

This document describes the implemented gateway shape for `0.6.1`.

## Runtime Model

`x402-gateway` runs as a reverse proxy in front of provider APIs. It loads provider configuration from one file or from a provider directory:

```bash
x402-cli gateway start providers/acme-weather/provider.yml
x402-cli gateway start --providers-dir providers
```

In directory mode, every `providers/**/provider.yml` file becomes one provider in the in-memory registry. Configuration is persisted as files. Runtime registry state is rebuilt on startup.

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
- payment terms: `operator.network`, `operator.currencies`, `operator.recipient`, `operator.scheme`
- facilitator URL: `operator.facilitator_url`
- endpoint list and pricing: `endpoints[]`
- optional recipient aliases and splits: `recipients`

Environment variables can be used with `${VAR}` placeholders. Missing variables fail startup or validation.

## Management Endpoints

```text
/__402/health      Liveness check, returns "ok"
/__402/providers   Loaded providers, status, signer, and load errors
/__402/endpoints   Endpoint catalog with price, network, and currency
/__402/verify      Verify-only endpoint for local debugging
```

## Catalog

The catalog turns provider files and `listing.md` metadata into static discovery artifacts:

```bash
x402-cli gateway catalog build providers --dist-dir dist
x402-cli gateway catalog search providers weather
```

Generated artifacts:

```text
dist/skills.json
dist/providers/<provider>.json
```

The gateway runtime and catalog are separate. Runtime payment behavior is driven by `provider.yml`; catalog artifacts are for discovery by CLI, MCP, or agent tooling.

## Local Development

Docker Compose starts both the gateway and a local mock facilitator:

```bash
docker compose up --build -d gateway
```

Services:

```text
gateway       http://127.0.0.1:4020
facilitator   http://127.0.0.1:4021
```

The example provider reads `X402_FACILITATOR_URL`, which defaults to `http://facilitator:4021` inside Compose.

## Debugging Focus

The basic implementation is complete enough to enter debugging. The active focus is:

- paid request flow with local facilitator
- provider-specific upstream auth behavior
- catalog correctness for generated `dist`
- real facilitator compatibility
- testnet settlement smoke tests
