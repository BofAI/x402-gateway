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

## 与 pay.sh 的关系

设计对标 [`solana-foundation/pay`](https://github.com/solana-foundation/pay) 的 gateway 反代和 catalog 工具链两部分。支付协议层完全用我们自己的 bankofai-x402(TRON + BSC,跟 pay.sh 的 Solana x402/MPP 无关);gateway 的 YAML 驱动 + endpoint allowlist + STRIP_HEADERS 模式照搬;catalog 的 PAY.md / scaffold / probe / build 思路改名 listing.md 用上。

## 文档

- [x402-gateway.md](x402-gateway.md) —— 当前 roadmap `v0.6.1` 的主设计文档。覆盖可执行范围、架构、模块边界、测试、CI 与验收标准。
- [gateway.md](gateway.md) —— 我们网关 + catalog 的实现要点。与 pay.sh.md 对称的简版,~440 行。日常开发优先看这份。
- [pay.sh.md](pay.sh.md) —— 单独讲 pay.sh 怎么实现的。包含启动、请求流水线、catalog CI 的 4 张时序图。要去看 pay.sh 源码先翻这里。
- [DESIGN.md](DESIGN.md) —— 完整设计文档(5000+ 行)。开始写代码先看 §3.0 的 6 角色时序图,再看 §13 的 MVP 范围和 §17 的 W1-W4 周节奏。
