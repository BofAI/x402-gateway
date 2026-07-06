import fs from "node:fs";
import path from "node:path";
import YAML from "yaml";
import { getToken, normalizeNetwork, toSmallestUnit } from "./tokens.js";
import type { PaymentRequirement } from "./x402.js";

export type ProviderConfig = {
  name: string;
  title?: string;
  forward_url: string;
  operator: {
    network: string;
    currencies?: Record<string, string[]>;
    recipient: string;
    scheme?: string;
    facilitator_url?: string;
    facilitator_api_key?: string;
    facilitator_api_key_env?: string;
    valid_for_seconds?: number;
  };
  recipients?: Record<string, { account: string }>;
  routing?: {
    auth?: {
      method?: string;
      key?: string;
      prefix?: string;
      value?: string;
      value_from_env?: string;
      param?: string;
    };
  };
  endpoints?: Array<{
    method: string;
    path: string;
    metering?: {
      dimensions?: Array<{ tiers?: Array<{ price_usd: number }> }>;
      variants?: Array<{
        param: string;
        value: string;
        dimensions?: Array<{ tiers?: Array<{ price_usd: number }> }>;
      }>;
    };
  }>;
};

export type ProviderEntry = {
  config: ProviderConfig;
  facilitatorUrl: string;
  facilitatorApiKey?: string;
};

function expandEnv(value: string): string {
  return value.replace(/\$\{([^}]+)\}/g, (_, name: string) => process.env[name] ?? "");
}

function expandDeep<T>(value: T): T {
  if (typeof value === "string") return expandEnv(value) as T;
  if (Array.isArray(value)) return value.map(expandDeep) as T;
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, expandDeep(item)]),
    ) as T;
  }
  return value;
}

function assertString(value: unknown, name: string): asserts value is string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${name} is required`);
}

function validateProvider(config: ProviderConfig, file: string): void {
  assertString(config.name, `${file}: name`);
  assertString(config.forward_url, `${file}: forward_url`);
  assertString(config.operator?.network, `${file}: operator.network`);
  assertString(config.operator?.recipient, `${file}: operator.recipient`);
  if (!config.endpoints?.length) throw new Error(`${file}: endpoints must contain at least one endpoint`);
  const seen = new Set<string>();
  for (const [index, endpoint] of config.endpoints.entries()) {
    assertString(endpoint.method, `${file}: endpoints[${index}].method`);
    assertString(endpoint.path, `${file}: endpoints[${index}].path`);
    if (!endpoint.path.startsWith("/")) throw new Error(`${file}: endpoint path must start with /`);
    const method = endpoint.method.toUpperCase();
    if (!["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"].includes(method)) {
      throw new Error(`${file}: unsupported endpoint method ${endpoint.method}`);
    }
    const key = `${method} ${endpoint.path}`;
    if (seen.has(key)) throw new Error(`${file}: duplicate endpoint ${key}`);
    seen.add(key);
    for (const [tierIndex, tier] of (endpoint.metering?.dimensions?.[0]?.tiers ?? []).entries()) {
      if (typeof tier.price_usd !== "number" || tier.price_usd < 0) {
        throw new Error(`${file}: endpoints[${index}].metering tier ${tierIndex} price_usd must be >= 0`);
      }
    }
  }
}

export function loadProvider(file: string): ProviderEntry {
  const config = expandDeep(YAML.parse(fs.readFileSync(file, "utf8"))) as ProviderConfig;
  validateProvider(config, file);
  config.operator.network = normalizeNetwork(config.operator.network);
  config.operator.scheme = "exact";
  return {
    config,
    facilitatorUrl:
      config.operator.facilitator_url ||
      process.env.X402_FACILITATOR_URL ||
      process.env.FACILITATOR_URL ||
      "https://facilitator.bankofai.io",
    facilitatorApiKey:
      config.operator.facilitator_api_key ||
      (config.operator.facilitator_api_key_env
        ? process.env[config.operator.facilitator_api_key_env]
        : undefined) ||
      process.env.X402_FACILITATOR_API_KEY ||
      process.env.FACILITATOR_API_KEY,
  };
}

export function loadProviders(providerPath: string): Map<string, ProviderEntry> {
  const stat = fs.statSync(providerPath);
  const files = stat.isDirectory()
    ? fs.readdirSync(providerPath, { recursive: true })
        .map(item => path.join(providerPath, String(item)))
        .filter(item => item.endsWith("provider.yml") || item.endsWith("provider.yaml"))
    : [providerPath];
  const entries = files.map(file => {
    const entry = loadProvider(file);
    return [entry.config.name, entry] as const;
  });
  const names = new Set<string>();
  for (const [name] of entries) {
    if (names.has(name)) throw new Error(`duplicate provider name: ${name}`);
    names.add(name);
  }
  return new Map(entries);
}

export function endpointFor(provider: ProviderConfig, method: string, routePath: string) {
  return provider.endpoints?.find(
    endpoint => endpoint.method.toUpperCase() === method.toUpperCase() && endpoint.path === routePath,
  );
}

export function priceUsd(endpoint: ReturnType<typeof endpointFor>, params: Record<string, string> = {}): number {
  const variant = endpoint?.metering?.variants?.find(item => params[item.param] === item.value);
  return (variant?.dimensions ?? endpoint?.metering?.dimensions)?.[0]?.tiers?.[0]?.price_usd ?? 0;
}

export function paymentRequirements(provider: ProviderConfig, price: number): PaymentRequirement[] {
  if (price <= 0) return [];
  const network = normalizeNetwork(provider.operator.network);
  const symbols = provider.operator.currencies?.usd ?? ["USDT"];
  const payTo = provider.recipients?.[provider.operator.recipient]?.account ?? provider.operator.recipient;
  return symbols.map(symbol => {
    const token = getToken(network, symbol);
    return {
      scheme: "exact",
      network,
      amount: toSmallestUnit(price, token.decimals),
      asset: token.address,
      payTo,
      maxTimeoutSeconds: provider.operator.valid_for_seconds ?? 300,
      extra: token.assetTransferMethod ? { assetTransferMethod: token.assetTransferMethod } : {},
    };
  });
}
