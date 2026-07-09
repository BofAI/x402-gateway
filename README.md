# x402 Gateway

TypeScript reverse proxy for paid HTTP APIs. This version uses the npm
TypeScript x402 SDK packages only:

- `@bankofai/x402-core@1.0.0`
- `@bankofai/x402-evm@1.0.0`
- `@bankofai/x402-tron@1.0.0`

Payment requirements are emitted as `scheme=exact`; supported stablecoins add
`extra.assetTransferMethod=permit2`.

## Install

```bash
npm install
npm run build
```

After installing the npm package globally, use the binary directly:

```bash
npm install -g @bankofai/x402-gateway
x402-gateway --help
```

## Run

Start from a single provider file:

```bash
x402-gateway --provider examples/provider.yml --host 127.0.0.1 --port 4020
```

Start from a providers directory:

```bash
x402-gateway --providers providers --host 127.0.0.1 --port 4020
```

Validate provider files without starting the server:

```bash
x402-gateway check --providers providers
x402-gateway check --provider examples/provider.yml --json
```

`check` validates provider YAML syntax, required fields, environment expansion,
network aliases, endpoint shape, and duplicate provider names. It does not call
upstream APIs, facilitator services, RPC endpoints, recipient addresses, or the
full payment path.

Run from source during development:

```bash
npm run dev -- --providers providers --host 127.0.0.1 --port 4020
```

Health check:

```bash
curl http://127.0.0.1:4020/__402/health
```

Paid provider path:

```bash
curl -i http://127.0.0.1:4020/providers/tron-nile-usdt/v1/ping
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
providers: 14 loaded
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
name: tron-nile-usdt
forward_url: ${X402_PROVIDER_FORWARD_URL}

routing:
  auth:
    method: header
    key: Authorization
    prefix: "Bearer "
    value_from_env: X402_PROVIDER_API_TOKEN

operator:
  network: tron-nile
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
    metering:
      dimensions:
        - tiers:
            - price_usd: 0.002
```

`@bankofai/x402-*` 1.0.0 uses `scheme: exact` with
`extra.assetTransferMethod: permit2` in the payment requirement. Older provider
configs that say `exact_permit` are normalized at load time, but new provider
configs should use `protocol: exact` and `asset_transfer_method: permit2`.

Network aliases accepted:

- `tron-mainnet` -> `tron:mainnet`
- `tron-nile` -> `tron:nile`
- `bsc-mainnet` -> `eip155:56`
- `bsc-testnet` -> `eip155:97`

## Environment

```bash
X402_GATEWAY_PROVIDERS_DIR=providers
X402_GATEWAY_HOST=127.0.0.1
PORT=8080
X402_GATEWAY_ADMIN_TOKEN=<admin-token>
X402_FACILITATOR_URL=https://facilitator-v2.bankofai.io
X402_FACILITATOR_API_KEY=<facilitator-api-key>
X402_PROVIDER_FORWARD_URL=<upstream-base-url>
X402_PROVIDER_RECIPIENT_TRON=<recipient-T-address>
X402_PROVIDER_API_TOKEN=<upstream-token>
```

Provider YAML may also use `operator.facilitator_api_key_env:
X402_FACILITATOR_API_KEY` so deployments can inject the facilitator API key via
environment variable without storing it in the mounted provider file.

## Docker

The container runs the same CLI binary:

```bash
docker run --rm -p 4020:8080 \
  -v "$PWD/providers:/app/providers:ro" \
  -e X402_GATEWAY_ADMIN_TOKEN=<admin-token> \
  -e X402_FACILITATOR_API_KEY=<facilitator-api-key> \
  bankofai/x402-gateway:v20260709182145
```

The Docker command binds `0.0.0.0:8080` explicitly; local CLI runs default to
`127.0.0.1` unless `--host` or `X402_GATEWAY_HOST` is set.
