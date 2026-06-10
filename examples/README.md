# x402 Gateway Provider Examples

## 中文说明

这里是服务方接入 Gateway 的示例。默认入口是 `x402-cli`：

```bash
x402-cli gateway scaffold ...
x402-cli gateway check ...
x402-cli gateway start ...
x402-cli catalog export-gateway ...
```

关键规则：

- `provider.yml` 是私有运行时配置，只放在服务方自己的 Gateway 环境。
- 上游 API key、bearer token、钱包私钥、`.env` 不进入公开仓库。
- 对外提交到 `x402-catelog` 的只有 `catalog.json` 和 `pay.md`。

## English

These examples show how an API provider uses the gateway through the single
user-facing command, `x402-cli`.

## Files

```text
examples/provider.yml        Minimal provider config template
examples/listing.md          Catalog listing template
providers/acme-weather/      Runnable Acme Weather demo provider
```

`provider.yml` is a private runtime file. It may reference upstream auth,
wallet profiles, recipient addresses, and facilitator URLs. Do not submit it to
the public catalog repository.

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
  --network tron:shasta
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

## 5. Build Provider Catalog Assets

For local validation:

```bash
x402-cli gateway catalog generate providers/acme-weather/provider.yml
x402-cli gateway catalog pay-assets providers/acme-weather/provider.yml
x402-cli gateway catalog check providers
x402-cli gateway catalog build providers --dist-dir dist
x402-cli gateway catalog search providers weather
```

## 6. Export Public Files for Catalog PR

Once the gateway is running publicly:

```bash
x402-cli catalog export-gateway https://gateway.example.com \
  --provider acme-weather \
  --output-dir providers/acme-weather
```

Submit only:

```text
providers/acme-weather/catalog.json
providers/acme-weather/pay.md
```

Never submit:

```text
provider.yml
.env
API keys
bearer tokens
passwords
private upstream URLs
```
