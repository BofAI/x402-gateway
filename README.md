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

## Run

Start from a single provider file:

```bash
node dist/cli.js --provider examples/provider.yml --host 127.0.0.1 --port 4020
```

Start from a providers directory:

```bash
node dist/cli.js --providers providers --host 0.0.0.0 --port 4020
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

Network aliases accepted:

- `tron-mainnet` -> `tron:mainnet`
- `tron-nile` -> `tron:nile`
- `bsc-mainnet` -> `eip155:56`
- `bsc-testnet` -> `eip155:97`

## Environment

```bash
X402_FACILITATOR_URL=https://facilitator.bankofai.io
X402_FACILITATOR_API_KEY=<optional-facilitator-api-key>
X402_PROVIDER_FORWARD_URL=<upstream-base-url>
X402_PROVIDER_RECIPIENT_TRON=<recipient-T-address>
X402_PROVIDER_API_TOKEN=<upstream-token>
```
