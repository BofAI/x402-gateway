# x402-gateway

基于 [bankofai-x402](https://github.com/BofAI/x402) Python SDK,把任何 HTTP API 包成 x402 收费端点的反向代理。卖家写一份 `provider.yml`,声明上游 URL、收钱钱包、按 endpoint 的价格;网关自动处理 402 challenge、调 facilitator 验证签名、settle 上链、再把请求转发上游。结算直付商家钱包,平台不持币。

链:TRON + BSC。同时附带一个 catalog 模块(`listing.md` + 一组 CLI),把上架的 API 编进静态 JSON 目录给 agent 发现。

## 仓库结构

```text
src/                         # gateway runtime 和 catalog 代码
providers/<provider>/         # 运营管理的商家接入配置
examples/                     # provider.yml / listing.md 示例
tests/                        # 单测、smoke 测试
```

`providers/` 是线上接入资产目录。每个商家一个子目录,至少包含:

```text
providers/acme-weather/
  provider.yml
  listing.md
```

## Provider 接入方式

早期由运营人员维护 `providers/` 目录。商家通过邮件、表单或线下沟通提交接入信息,运营审核后把配置提交到仓库。

最小闭环:

1. 新建 `providers/<provider-name>/provider.yml`
2. 在 `provider.yml` 里配置上游 `forward_url`、收款地址 `operator.recipient`、network / currency、endpoint 和价格
3. 需要 catalog 展示时补 `listing.md`,或用 `catalog generate` 从 `provider.yml` 生成
4. 运行 `x402-gateway server check providers/<provider-name>/provider.yml`
5. 部署时运行 `x402-gateway server start --providers-dir providers`

买家访问路径统一为:

```text
GET /providers/<provider-name>/<endpoint-path>
```

例如:

```text
GET /providers/acme-weather/v1/current
```

Gateway 启动时一次性读取 `providers/**/provider.yml`。同一个进程内所有 provider 进入同一个 registry,`/__402/providers` 和 `/__402/endpoints` 用来查看当前加载结果。

## 常用命令

```bash
x402-gateway server start --providers-dir providers
x402-gateway server check providers/acme-weather/provider.yml
x402-gateway server scaffold acme-weather

x402-gateway catalog scaffold acme-weather https://api.example.com/openapi.json
x402-gateway catalog generate providers/acme-weather/provider.yml
x402-gateway catalog check providers
x402-gateway catalog build providers --dist-dir dist
x402-gateway catalog search providers weather
```

## 官方中心服务部署

官方 gateway 以一个常驻 HTTP 服务运行,读取仓库里的 `providers/` 目录。当前阶段不需要 DB:provider 配置、价格、收款地址、上游地址、catalog 展示信息都由文件持久化,随仓库提交和部署发布。容器重启后会重新读取挂载的 `providers/`,不会丢配置。

本地或服务器启动:

```bash
cp .env.example .env
docker compose up --build -d gateway
curl http://127.0.0.1:4020/__402/health
```

部署形态:

```text
Buyer / Agent
  -> https://gateway.bankofai.io/providers/<provider>/<path>
  -> x402-gateway container
  -> provider upstream API

Operator
  -> commit providers/<provider>/provider.yml + listing.md
  -> deploy/restart gateway
  -> /__402/providers and /__402/endpoints show loaded providers
```

`docker-compose.yml` 默认把本仓库 `./providers` 只读挂载到容器 `/app/providers`。上游 API key、OAuth client secret、HMAC secret 等运行时密钥放在 `.env` 或线上 secret manager,在 `provider.yml` 里只引用环境变量名。

开发环境会同时启动一个本地 mock facilitator:

```text
gateway -> http://facilitator:4021
```

`provider.yml` 通过 `${X402_FACILITATOR_URL}` 读取 facilitator 地址。当前 `.env.example` 默认指向 compose 里的本地 facilitator;正式环境改成线上 facilitator 地址即可。

生成 catalog 静态目录:

```bash
docker compose --profile tools run --rm catalog-build
```

输出在 `dist/`,用于后续发布给 CLI、MCP 或 agent 做 API 发现。gateway 服务本身只负责 402 challenge、verify、settle、转发和管理端点。

生产部署时至少保留这些探针:

```text
/__402/health      liveness,返回 ok
/__402/providers   当前加载的 provider、状态、错误
/__402/endpoints   当前 endpoint、价格、network、currency
/__402/verify      只 verify 不 settle,用于联调
```

## 与 pay.sh 的关系

设计对标 [`solana-foundation/pay`](https://github.com/solana-foundation/pay) 的 gateway 反代和 catalog 工具链两部分。支付协议层完全用我们自己的 bankofai-x402(TRON + BSC,跟 pay.sh 的 Solana x402/MPP 无关);gateway 的 YAML 驱动 + endpoint allowlist + STRIP_HEADERS 模式照搬;catalog 的 PAY.md / scaffold / probe / build 思路改名 listing.md 用上。

## 文档

- [x402-gateway.md](x402-gateway.md) —— 当前 roadmap `v0.6.1` 的主设计文档。覆盖可执行范围、架构、模块边界、测试、CI 与验收标准。
- [gateway.md](gateway.md) —— 我们网关 + catalog 的实现要点。与 pay.sh.md 对称的简版,~440 行。日常开发优先看这份。
- [pay.sh.md](pay.sh.md) —— 单独讲 pay.sh 怎么实现的。包含启动、请求流水线、catalog CI 的 4 张时序图。要去看 pay.sh 源码先翻这里。
- [DESIGN.md](DESIGN.md) —— 完整设计文档(5000+ 行)。开始写代码先看 §3.0 的 6 角色时序图,再看 §13 的 MVP 范围和 §17 的 W1-W4 周节奏。
