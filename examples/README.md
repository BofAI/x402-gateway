# x402 Gateway Provider Examples

These examples show how an API provider configures and validates the gateway
through `x402-cli`.

## Files

```text
examples/provider.yml        Minimal provider config template
providers/acme-weather/      Runnable Acme Weather demo provider
```

`provider.yml` is a private runtime file. It may reference upstream auth,
wallet profiles, recipient addresses, and facilitator URLs. Keep it in the
gateway runtime environment.

## 1. Install the CLI

```bash
pip install bankofai-x402-cli==0.6.1b1
```

This automatically installs the gateway runtime package.

## 2. Create a Provider Config

```bash
x402-cli gateway scaffold acme-weather \
  --output-dir providers/acme-weather \
  --forward-url https://api.example.com \
  --network eip155:56
```

For the checked-in demo:

```bash
x402-cli gateway check providers/acme-weather/provider.yml
```

Expected:

```text
ok: acme-weather (2 endpoints)
```

## 3. Configure Secrets Locally

The demo uses environment variables instead of storing secrets in YAML:

```yaml
routing:
  auth:
    method: header
    key: Authorization
    prefix: "Bearer "
    value_from_env: ACME_API_TOKEN
```

Run locally with:

```bash
export ACME_API_TOKEN=your-upstream-token
export X402_GATEWAY_PUBLIC_BASE_URL=https://gateway.example.com
export X402_FACILITATOR_URL=https://facilitator.example.com
```

## 4. Start the Gateway

```bash
x402-cli gateway start --providers-dir providers --host 0.0.0.0 --port 4020
```

Gateway-facing endpoint:

```text
https://gateway.example.com/providers/acme-weather/v1/current
```

## 5. Validate Runtime Behavior

Inspect the loaded provider and endpoint state:

```bash
curl http://127.0.0.1:4020/__402/health
curl http://127.0.0.1:4020/__402/providers
curl http://127.0.0.1:4020/__402/endpoints
```

Call a paid endpoint without a payment header:

```bash
curl -i "http://127.0.0.1:4020/providers/acme-weather/v1/current?city=Singapore"
```

Expected result:

```text
HTTP/1.1 402 Payment Required
```

Never store these values in source control:

```text
.env
API keys
bearer tokens
wallet private keys
mnemonics
passwords
private upstream URLs
```
