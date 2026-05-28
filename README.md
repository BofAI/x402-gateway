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

## 与 pay.sh 的关系

设计对标 [`solana-foundation/pay`](https://github.com/solana-foundation/pay) 的 gateway 反代和 catalog 工具链两部分。支付协议层完全用我们自己的 bankofai-x402(TRON + BSC,跟 pay.sh 的 Solana x402/MPP 无关);gateway 的 YAML 驱动 + endpoint allowlist + STRIP_HEADERS 模式照搬;catalog 的 PAY.md / scaffold / probe / build 思路改名 listing.md 用上。

## 文档

- [x402-gateway.md](x402-gateway.md) —— 当前 roadmap `v0.6.1` 的主设计文档。覆盖可执行范围、架构、模块边界、测试、CI 与验收标准。
- [gateway.md](gateway.md) —— 我们网关 + catalog 的实现要点。与 pay.sh.md 对称的简版,~440 行。日常开发优先看这份。
- [pay.sh.md](pay.sh.md) —— 单独讲 pay.sh 怎么实现的。包含启动、请求流水线、catalog CI 的 4 张时序图。要去看 pay.sh 源码先翻这里。
- [DESIGN.md](DESIGN.md) —— 完整设计文档(5000+ 行)。开始写代码先看 §3.0 的 6 角色时序图,再看 §13 的 MVP 范围和 §17 的 W1-W4 周节奏。
