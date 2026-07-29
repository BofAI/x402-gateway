# x402 Gateway

TypeScript reverse proxy for paid HTTP APIs. This version uses the npm
TypeScript x402 SDK packages only:

- `@bankofai/x402-core@1.0.1`
- `@bankofai/x402-evm@1.0.1`
- `@bankofai/x402-tron@1.0.1`

Payment requirements support logical `scheme=exact` and TRON
`scheme=exact_gasfree`. The transfer method is selected by the registered
asset: Base USDC uses EIP-3009 metadata, while BSC/TRON USDT uses Permit2.
GasFree uses the TRON GasFree relayer flow without Permit2 metadata.

## Install

```bash
npm install
npm run build
```

After installing the npm package globally, use the binary directly:

```bash
npm install -g @bankofai/x402-gateway@1.0.2
x402-gateway --help
```

## Run

Start from a single provider file:

```bash
x402-gateway --provider examples/provider.yml --host 127.0.0.1 --port 4020
```

Start from a runtime Provider directory:

```bash
x402-gateway --providers /path/to/providers --host 127.0.0.1 --port 4020
```

Validate the generic Base Sepolia USDC example:

```bash
X402_PROVIDER_FORWARD_URL=https://api.example.com \
X402_GATEWAY_PUBLIC_BASE_URL=https://gateway.example.com \
X402_PROVIDER_RECIPIENT_BASE=0x0000000000000000000000000000000000000001 \
X402_FACILITATOR_URL=https://x402.org/facilitator \
x402-gateway check --provider examples/base-usdc-provider.yml
```

The example uses `eip155:84532`, official Base Sepolia USDC, the `exact`
EIP-3009 flow, and `https://x402.org/facilitator`. Test and production Provider
configuration is deployed separately and is not stored in this runtime
repository.

Validate provider files without starting the server:

```bash
x402-gateway check --providers /path/to/providers
x402-gateway check --provider examples/provider.yml --json
```

`check` validates provider YAML syntax, required fields, environment expansion,
network aliases, endpoint shape, and duplicate provider names. It does not call
upstream APIs, facilitator services, RPC endpoints, recipient addresses, or the
full payment path.

Run from source during development:

```bash
npm run dev -- --providers /path/to/providers --host 127.0.0.1 --port 4020
```

Health check:

```bash
curl http://127.0.0.1:4020/__402/health
```

Paid provider path:

```bash
curl -i http://127.0.0.1:4020/providers/defillama-tvl-base-sepolia/protocols
```

If the endpoint has metering, the gateway returns `402 Payment Required` with a
`PAYMENT-REQUIRED` header. After the client retries with `PAYMENT-SIGNATURE`,
the gateway verifies and settles with the facilitator, then forwards to the
configured upstream.

## CLI

```text
x402-gateway start --providers <dir> [options]
x402-gateway --providers <dir> [options]
x402-gateway check --providers <dir>
x402-gateway check --provider <file>
```

Options:

- `--provider <file>`: load one provider YAML file.
- `--providers <dir>`: recursively load `provider.yml` / `provider.yaml`.
  `--provider` and `--providers` are mutually exclusive.
- `--host <host>`: bind host. The CLI default is `127.0.0.1`; Docker passes `0.0.0.0`.
- `--port <port>`: bind port, default `8080`.
- `--json`: machine-readable startup/check/error output.
- `--quiet`: suppress startup, shutdown, and successful `check` output.
- `--debug`: include stack traces for startup errors.
- `--help`, `--version`: inspect usage and installed version.

Startup prints human-readable URLs by default:

```text
x402-gateway listening on http://127.0.0.1:4020
providers: 1 loaded
health: http://127.0.0.1:4020/__402/health
ready: http://127.0.0.1:4020/__402/ready
```

With `--json`, startup output includes `source`, `host`, `port`, `count`,
`providers`, `health`, and `ready` for deployment scripts. CLI errors also
honor `--json`, including argument parsing errors such as unknown options.

Admin endpoints such as `/__402/providers`, `/__402/endpoints`, and `/metrics`
are protected by default. Set `X402_GATEWAY_ADMIN_TOKEN` in deployed
environments and call them with `Authorization: Bearer <token>`. For a
deliberately public test deployment, set `X402_GATEWAY_ADMIN_ALLOW_PUBLIC=true`.

## Provider Config

Provider files stay in YAML:

```yaml
name: example-price-tron
forward_url: ${X402_PROVIDER_FORWARD_URL}

routing:
  auth:
    method: header
    key: Authorization
    prefix: "Bearer "
    value_from_env: X402_PROVIDER_API_TOKEN

operator:
  network: tron:0xcd8690dc
  currencies:
    usd: ["USDT"]
  recipient: ${X402_PROVIDER_RECIPIENT_TRON}
  scheme: exact
  protocol: exact
  asset_transfer_method: permit2
  facilitator_url: ${X402_FACILITATOR_URL}
  facilitator_api_key_env: X402_FACILITATOR_API_KEY
  valid_for_seconds: 300

endpoints:
  - method: GET
    path: /v1/ping
    request:
      query:
        symbol:
          required: true
          type: string
          min_length: 1
    response:
      json:
        field: code
        equals: 1
    metering:
      dimensions:
        - tiers:
            - price_usd: 0.002
```

`scheme: exact` describes settlement semantics, not a universal token
authorization mechanism. Registered token metadata selects EIP-3009 or
Permit2. To force Permit2 for a supported token, use
`asset_transfer_method: permit2`. Historical protocol aliases such as
`exact_permit` and `exact_permit2` are rejected with a migration error instead
of silently changing transfer methods. For GasFree, set both `scheme` and
`protocol` to `exact_gasfree`; the facilitator must support GasFree for the
selected TRON network and token.

Paid endpoints can declare required query parameters, headers, and JSON body
fields under `request`. Validation runs before the gateway returns a 402, so a
known-invalid request is never verified or settled. Supported field checks are
`required`, `type`, `format`, `min_length`, `max_length`, and `enum`. Supported
formats are `evm_address` and `evm_address_list`.

APIs that encode business failures inside HTTP 200 JSON responses can declare
`response.json.field` and `response.json.equals`. A mismatch is treated as an
upstream failure and is not counted as a successful delivery.

Non-CAIP TRON aliases are rejected. Provider files must use canonical TRON
CAIP-2 IDs.

EVM convenience aliases accepted:

- `bsc-mainnet` -> `eip155:56`
- `bsc-testnet` -> `eip155:97`
- `base-mainnet` -> `eip155:8453`
- `base-sepolia` -> `eip155:84532`

## Environment

```bash
X402_GATEWAY_PROVIDERS_DIR=providers
X402_GATEWAY_HOST=127.0.0.1
PORT=8080
X402_GATEWAY_ADMIN_TOKEN=<admin-token>
X402_GATEWAY_PUBLIC_BASE_URL=https://gateway.example.com
X402_GATEWAY_BUILD_COMMIT=<git-sha>
X402_GATEWAY_MAX_BODY_BYTES=1000000
X402_GATEWAY_MAX_RESPONSE_BYTES=20000000
X402_GATEWAY_FACILITATOR_TIMEOUT_MS=10000
X402_GATEWAY_UPSTREAM_TIMEOUT_MS=30000
X402_GATEWAY_MAX_CONCURRENT_REQUESTS=100
X402_GATEWAY_RATE_LIMIT_PER_MINUTE=300
X402_GATEWAY_ALLOW_INSECURE_HTTP=false
X402_FACILITATOR_URL=https://facilitator.bankofai.io
X402_FACILITATOR_API_KEY=<facilitator-api-key>
X402_PROVIDER_FORWARD_URL=<upstream-base-url>
X402_PROVIDER_RECIPIENT_TRON=<recipient-T-address>
X402_PROVIDER_RECIPIENT_BASE=<recipient-0x-address>
X402_PROVIDER_API_TOKEN=<upstream-token>
```

Provider YAML may also use `operator.facilitator_api_key_env:
X402_FACILITATOR_API_KEY` so deployments can inject the facilitator API key via
environment variable without storing it in the mounted provider file.
When `facilitator_api_key_env` is configured, that named variable is required;
`check` and `start` fail instead of silently contacting the facilitator without
authentication.

`X402_GATEWAY_PUBLIC_BASE_URL` must be the externally reachable gateway origin.
It makes the challenge `resource.url` absolute, which is required when the
container's internal host or request path is not the public payment URL.

## Docker

`docker compose up --build` uses the current checkout and
`pull_policy: build`; it does not pull the mutable remote `test` image. The
local GasFree HTTP fixture is intentionally stored under `examples/` and is
not loaded by default.

Set `X402_GATEWAY_PROVIDERS_HOST_DIR` to a runtime Provider directory. Compose
mounts it read-only at `/app/providers`. The fallback `./providers` directory
is Git-ignored:

```bash
X402_GATEWAY_PROVIDERS_HOST_DIR=/path/to/providers docker compose up --build
```

Release deployments should use an immutable semantic version or image digest.
The container runs the same CLI binary:

```bash
docker run --rm -p 4020:8080 \
  -v "$PWD/providers:/app/providers:ro" \
  -e X402_GATEWAY_ADMIN_TOKEN=<admin-token> \
  -e X402_FACILITATOR_API_KEY=<facilitator-api-key> \
  bankofai/x402-gateway@<sha256-digest>
```

The Docker command binds `0.0.0.0:8080` explicitly; local CLI runs default to
`127.0.0.1` unless `--host` or `X402_GATEWAY_HOST` is set.

## Metrics

The protected `/metrics` endpoint separates request, settlement, and delivery
semantics:

- `x402_gateway_http_requests_total`: all HTTP traffic, including admin paths.
- `x402_gateway_provider_requests_total`: requests matched to provider routes.
- `x402_gateway_settlements_total`: successful payment settlements.
- `x402_gateway_deliveries_total`: settled requests delivered from upstream.
- `x402_gateway_post_settlement_failures_total`: failures after settlement.

These counters do not claim that a settlement implies successful business
delivery.
