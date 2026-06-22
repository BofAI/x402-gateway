# x402 Gateway 测试环境部署

本文档用于部署 Gateway 测试环境。Gateway 负责读取本地 `provider.yml`，返回 x402 `402 Payment Required`，校验支付后转发到上游 API。

`provider.yml`、`.env`、上游 API key、钱包私钥只放在 Gateway 运行环境，不提交到 Catalog 仓库。

## English Summary

This document explains how to deploy the Gateway test environment. The Gateway loads local `provider.yml` files, returns x402 `402 Payment Required` challenges for paid endpoints, verifies and settles payments through a facilitator, and proxies successful requests to upstream APIs.

Keep `provider.yml`, `.env`, upstream API keys, and wallet private keys in the Gateway runtime environment only. Do not submit them to the public Catalog repository.

## 1. 拉取代码

```bash
git clone git@github.com:BofAI/x402-gateway.git
cd x402-gateway
git checkout feature/0.6.1-gateway-init
```

## 2. 部署模式

测试环境有两种模式：

```text
Sandbox 模式：用于前端和 402 流程联调，不做真实链上支付。
真实测试网模式：用于 BSC Testnet / TRON Nile 上链支付验收。
```

建议先部署 Sandbox 模式，再切真实测试网模式。

## 3. Sandbox 模式

Sandbox 模式使用本仓库的 `docker-compose.yml`，会启动：

```text
gateway
mock facilitator
demo upstream
```

准备 `.env`：

```bash
cp .env.example .env
```

确认 `.env` 至少包含：

```bash
X402_GATEWAY_PUBLIC_BASE_URL=https://tm-x402-gateway.bankofai.io
X402_FACILITATOR_URL=http://facilitator:4021
ACME_API_TOKEN=demo-upstream-token
```

构建并启动：

```bash
docker compose build gateway upstream facilitator
docker compose up -d gateway
```

也可以直接使用 CI 推送到 Docker Hub 的 Gateway 镜像。测试 tag `test-v*` 会发布为镜像 tag `test`：

```bash
export X402_GATEWAY_IMAGE=bankofai/x402-gateway:test
docker compose pull gateway
docker compose up -d --no-build gateway
```

镜像发布规则和 `x402-facilitator` 保持一致：

```text
Git tag: test-v* -> Docker image: bankofai/x402-gateway:test
Git tag: v*      -> Docker image: bankofai/x402-gateway:<tag>
```

GitHub Actions 需要在本仓库配置以下 secrets：

```text
DOCKERHUB_USERNAME
DOCKERHUB_TOKEN
```

默认端口映射：

```text
0.0.0.0:4020 -> container 8080
```

日志路径：

```text
gateway 容器内：/app/log/gateway.log
mock facilitator 容器内：/app/log/mock-facilitator.log
宿主机：./log/
```

查看日志：

```bash
docker compose logs -f gateway
tail -f ./log/gateway.log
```

检查：

```bash
docker compose ps
curl -fsS http://127.0.0.1:4020/__402/health
curl -fsS http://127.0.0.1:4020/__402/providers
curl -fsS http://127.0.0.1:4020/__402/endpoints
curl -fsS http://127.0.0.1:4021/supported
```

测试 paid endpoint，预期返回 `402 Payment Required`：

```bash
curl -i "http://127.0.0.1:4020/providers/open-meteo-weather/v1/forecast?latitude=31.2304&longitude=121.4737&current=temperature_2m&timezone=auto"
```

## 4. 真实测试网模式

真实测试网模式不使用 mock facilitator，需要配置真实 facilitator 和真实测试网收款地址。

需要准备：

```text
X402_FACILITATOR_URL
X402_GATEWAY_PUBLIC_BASE_URL
provider.yml 里的 operator.recipient
测试网钱包资金
测试网 token 余额
```

示例 `.env`：

```bash
X402_GATEWAY_PUBLIC_BASE_URL=https://tm-x402-gateway.bankofai.io
X402_FACILITATOR_URL=https://facilitator-tn.example.com
X402_GATEWAY_PORT=4020
```

确认 provider：

```bash
x402-cli gateway check providers/open-meteo-weather/provider.yml
```

构建镜像：

```bash
docker build -t x402-gateway:tn .
```

启动真实测试网 Gateway。注意这里不加 `--sandbox`：

```bash
docker run -d \
  --name x402-gateway-tn \
  --restart unless-stopped \
  --env-file .env \
  -p 4020:8080 \
  -v "$(pwd)/providers:/app/providers:ro" \
  -v "$(pwd)/log:/app/log" \
  x402-gateway:tn \
  sh -c 'mkdir -p /app/log && x402-gateway server start \
    --providers-dir /app/providers \
    --host 0.0.0.0 \
    --port 8080 \
    --quiet 2>&1 | tee -a /app/log/gateway.log'
```

检查：

```bash
curl -fsS http://127.0.0.1:4020/__402/health
curl -fsS http://127.0.0.1:4020/__402/providers
curl -fsS http://127.0.0.1:4020/__402/endpoints
tail -n 20 ./log/gateway.log
```

外部域名检查：

```bash
curl -fsS https://tm-x402-gateway.bankofai.io/__402/health
```

## 5. 反向代理

Nginx 示例：

```nginx
server {
    listen 443 ssl;
    server_name tm-x402-gateway.bankofai.io;

    location / {
        proxy_pass http://127.0.0.1:4020;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

## 6. Catalog 对接

Gateway 域名确定后，Catalog 中公开 endpoint 需要指向 Gateway TN：

```text
https://tm-x402-gateway.bankofai.io/providers/open-meteo-weather/v1/forecast
```

Catalog 仓库只提交：

```text
providers/<fqn>/catalog.json
providers/<fqn>/pay.md
dist/*
```

不要提交：

```text
provider.yml
.env
API key
bearer token
wallet private key
mnemonic
password
private internal URL
```

## 7. CLI 验收

安装：

```bash
python3 -m pip install --upgrade pip
python3 -m pip install --pre bankofai-x402-cli==0.6.1b1
```

如果 beta 包还未发布，可以从分支安装：

```bash
python3 -m pip install \
  "bankofai-x402-gateway @ git+https://github.com/BofAI/x402-gateway.git@feature/0.6.1-gateway-init"

python3 -m pip install \
  "bankofai-x402-cli @ git+https://github.com/BofAI/x402-cli.git@feature/0.6.1-gateway-align"
```

Catalog 搜索：

```bash
x402-cli catalog search meteo \
  --catalog https://tm-x402-catelog.bankofai.io/api/catalog.json \
  --json
```

查看 provider：

```bash
x402-cli catalog show open-meteo-weather \
  --catalog https://tm-x402-catelog.bankofai.io/api/catalog.json \
  --json
```

读取 pay-json：

```bash
x402-cli catalog pay-json open-meteo-weather \
  --catalog https://tm-x402-catelog.bankofai.io/api/catalog.json
```

调用 Gateway：

```bash
x402-cli pay \
  "https://tm-x402-gateway.bankofai.io/providers/open-meteo-weather/v1/forecast?latitude=31.2304&longitude=121.4737&current=temperature_2m&timezone=auto"
```

如果需要限制链：

```bash
x402-cli pay \
  --network eip155:97 \
  "https://tm-x402-gateway.bankofai.io/providers/open-meteo-weather/v1/forecast?latitude=31.2304&longitude=121.4737&current=temperature_2m&timezone=auto"
```

## 8. 验收清单

```text
Gateway 容器启动成功
/__402/health 返回 200
/__402/providers 能看到 open-meteo-weather
/__402/endpoints 能看到 /providers/open-meteo-weather/v1/forecast
未支付请求返回 402 Payment Required
Catalog endpoint.url 指向 tm-x402-gateway.bankofai.io
x402-cli catalog search 能搜到 open-meteo-weather
x402-cli pay 能完成 sandbox 或测试网支付流程
```

## 9. 回滚

```bash
docker rm -f x402-gateway-tn
git checkout <previous-good-commit>
docker build -t x402-gateway:tn .
docker run ...
```

Sandbox compose 回滚：

```bash
git checkout <previous-good-commit>
docker compose build gateway upstream facilitator
docker compose up -d gateway
```
