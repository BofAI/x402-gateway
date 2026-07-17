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
    schemes?: string[];
    protocol?: string;
    asset_transfer_method?: string;
    assetTransferMethod?: string;
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
  return value.replace(/\$\{([^}]+)\}/g, (_, name: string) => {
    if (!(name in process.env)) throw new Error(`environment variable \${${name}} is not set`);
    return process.env[name] ?? "";
  });
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

function assertHttpUrl(value: string | undefined, name: string): void {
  if (!value) return;
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol)) throw new Error("unsupported protocol");
    if (url.username || url.password || url.search || url.hash) throw new Error("credentials, query, and fragment are not allowed");
    if (url.protocol === "http:" && !["localhost", "127.0.0.1", "::1"].includes(url.hostname) && process.env.X402_GATEWAY_ALLOW_INSECURE_HTTP !== "true") {
      throw new Error("remote HTTP is not allowed");
    }
  } catch {
    throw new Error(`${name} must be a valid http(s) URL`);
  }
}

function validateTiers(tiers: unknown, file: string, path: string): void {
  if (!Array.isArray(tiers) || !tiers.length) throw new Error(`${file}: ${path}.tiers must contain at least one tier`);
  for (const [tierIndex, tier] of tiers.entries()) {
    const price = (tier as any)?.price_usd;
    if (typeof price !== "number" || !Number.isFinite(price) || price < 0) {
      throw new Error(`${file}: ${path}.tiers[${tierIndex}].price_usd must be a finite number >= 0`);
    }
  }
}

function validateDimensions(dimensions: unknown, file: string, path: string): void {
  if (!Array.isArray(dimensions) || !dimensions.length) {
    throw new Error(`${file}: ${path}.dimensions must contain at least one dimension`);
  }
  if (dimensions.length !== 1) throw new Error(`${file}: ${path}.dimensions currently supports exactly one dimension`);
  for (const [dimensionIndex, dimension] of dimensions.entries()) {
    validateTiers((dimension as any)?.tiers, file, `${path}.dimensions[${dimensionIndex}]`);
  }
}

function validateMetering(endpoint: NonNullable<ProviderConfig["endpoints"]>[number], file: string, path: string): void {
  if (!endpoint.metering) return;
  validateDimensions(endpoint.metering.dimensions, file, `${path}.metering`);
  for (const [variantIndex, variant] of (endpoint.metering.variants ?? []).entries()) {
    assertString(variant.param, `${file}: ${path}.metering.variants[${variantIndex}].param`);
    assertString(variant.value, `${file}: ${path}.metering.variants[${variantIndex}].value`);
    validateDimensions(variant.dimensions, file, `${path}.metering.variants[${variantIndex}]`);
  }
}

function validateProvider(config: ProviderConfig, file: string): void {
  assertString(config.name, `${file}: name`);
  assertString(config.forward_url, `${file}: forward_url`);
  assertString(config.operator?.network, `${file}: operator.network`);
  assertString(config.operator?.recipient, `${file}: operator.recipient`);
  assertHttpUrl(config.forward_url, `${file}: forward_url`);
  assertHttpUrl(config.operator?.facilitator_url, `${file}: operator.facilitator_url`);
  if (config.operator?.valid_for_seconds !== undefined &&
    (!Number.isFinite(config.operator.valid_for_seconds) || config.operator.valid_for_seconds <= 0)) {
    throw new Error(`${file}: operator.valid_for_seconds must be > 0`);
  }
  const authMethod = config.routing?.auth?.method;
  if (authMethod && !["header", "query_param", "access_token", "oauth2"].includes(authMethod)) {
    throw new Error(`${file}: unsupported routing.auth.method ${authMethod}`);
  }
  const auth = config.routing?.auth;
  if (auth) {
    if (!auth.value && !auth.value_from_env) throw new Error(`${file}: routing.auth requires value or value_from_env`);
    if (auth.value_from_env && !process.env[auth.value_from_env]) throw new Error(`${file}: environment variable ${auth.value_from_env} is not set`);
  }
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
    validateMetering(endpoint, file, `endpoints[${index}]`);
  }
}

export function loadProvider(file: string): ProviderEntry {
  const config = expandDeep(YAML.parse(fs.readFileSync(file, "utf8"))) as ProviderConfig;
  validateProvider(config, file);
  config.operator.network = normalizeNetwork(config.operator.network);
  normalizePaymentProtocol(config, file);
  validatePaymentCapabilities(config, file);
  const facilitatorUrl =
    config.operator.facilitator_url ||
    process.env.X402_FACILITATOR_URL ||
    process.env.FACILITATOR_URL ||
    "https://facilitator.bankofai.io";
  assertHttpUrl(facilitatorUrl, `${file}: facilitator URL`);
  const configuredApiKeyEnv = config.operator.facilitator_api_key_env;
  if (configuredApiKeyEnv && !process.env[configuredApiKeyEnv]) {
    throw new Error(`${file}: environment variable ${configuredApiKeyEnv} is not set`);
  }
  return {
    config,
    facilitatorUrl,
    facilitatorApiKey:
      config.operator.facilitator_api_key ||
      (configuredApiKeyEnv
        ? process.env[configuredApiKeyEnv]
        : undefined) ||
      process.env.X402_FACILITATOR_API_KEY ||
      process.env.FACILITATOR_API_KEY,
  };
}

function normalizePaymentProtocol(config: ProviderConfig, file: string): void {
  if (config.operator.schemes !== undefined && (!Array.isArray(config.operator.schemes) || !config.operator.schemes.length)) {
    throw new Error(`${file}: operator.schemes must be a non-empty string array`);
  }
  const rawSchemes = config.operator.schemes ?? [config.operator.protocol || config.operator.scheme || "exact"];
  const schemes = [...new Set(rawSchemes.map(value => {
    if (typeof value !== "string" || !value.trim()) throw new Error(`${file}: operator.schemes must contain non-empty strings`);
    const raw = String(value).toLowerCase();
    const normalized = raw.replace(/[-:\s]/g, "_");
    if (!["exact", "exact_gasfree", "exact_permit", "permit2", "exact_permit2"].includes(normalized)) {
      throw new Error(`${file}: unsupported x402 protocol ${raw}; use exact or exact_gasfree`);
    }
    return normalized === "exact_gasfree" ? "exact_gasfree" : "exact";
  }))];
  if (schemes.includes("exact_gasfree") && !config.operator.network.startsWith("tron:")) {
    throw new Error(`${file}: exact_gasfree is supported only on TRON networks`);
  }
  config.operator.schemes = schemes;
  config.operator.scheme = schemes[0];
  config.operator.protocol = schemes[0];
  if (schemes.includes("exact")) {
    config.operator.asset_transfer_method = "permit2";
    config.operator.assetTransferMethod = "permit2";
  } else {
    delete config.operator.asset_transfer_method;
    delete config.operator.assetTransferMethod;
  }
}

function validatePaymentCapabilities(config: ProviderConfig, file: string): void {
  const symbols = config.operator.currencies?.usd ?? ["USDT"];
  if (!Array.isArray(symbols) || !symbols.length || symbols.some(symbol => typeof symbol !== "string" || !symbol.trim())) {
    throw new Error(`${file}: operator.currencies.usd must be a non-empty string array`);
  }
  if (new Set(symbols.map(symbol => symbol.toUpperCase())).size !== symbols.length) throw new Error(`${file}: operator.currencies.usd must not contain duplicates`);
  for (const symbol of symbols) getToken(config.operator.network, symbol);

  const recipient = config.recipients?.[config.operator.recipient]?.account ?? config.operator.recipient;
  assertString(recipient, `${file}: resolved recipient`);
  const validRecipient = config.operator.network.startsWith("tron:")
    ? /^T[1-9A-HJ-NP-Za-km-z]{33}$/.test(recipient)
    : /^0x[0-9a-fA-F]{40}$/.test(recipient);
  if (!validRecipient) throw new Error(`${file}: operator.recipient must be a valid address or a resolvable recipient alias`);

  for (const endpoint of config.endpoints ?? []) {
    if (!endpoint.metering) continue;
    const prices = [endpoint.metering.dimensions?.[0]?.tiers?.[0]?.price_usd,
      ...(endpoint.metering.variants ?? []).map(variant => variant.dimensions?.[0]?.tiers?.[0]?.price_usd)]
      .filter((price): price is number => typeof price === "number" && price > 0);
    for (const price of prices) if (!paymentRequirements(config, price).length) throw new Error(`${file}: paid endpoint cannot generate payment requirements`);
  }
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
    endpoint => endpoint.method.toUpperCase() === method.toUpperCase() && pathMatches(endpoint.path, routePath),
  );
}

function pathMatches(template: string, routePath: string): boolean {
  if (!routePath.startsWith("/") || routePath.startsWith("//") || routePath.includes("\\") || routePath.includes("\0") || /%(?:2f|5c)/i.test(routePath)) return false;
  const templateParts = template.split("/").filter(Boolean);
  const routeParts = routePath.split("/").filter(Boolean);
  if (templateParts.length !== routeParts.length) return false;
  return templateParts.every((part, index) => {
    if (part.startsWith("{") && part.endsWith("}")) return routeParts[index].length > 0;
    return part === routeParts[index];
  });
}

export function priceUsd(endpoint: ReturnType<typeof endpointFor>, params: Record<string, string> = {}): number {
  if (!endpoint?.metering) return 0;
  const variant = endpoint?.metering?.variants?.find(item => params[item.param] === item.value);
  const price = (variant?.dimensions ?? endpoint.metering.dimensions)?.[0]?.tiers?.[0]?.price_usd;
  if (typeof price !== "number" || !Number.isFinite(price) || price < 0) {
    throw new Error(`invalid metering price for ${endpoint.method} ${endpoint.path}`);
  }
  return price;
}

export function paymentRequirements(provider: ProviderConfig, price: number): PaymentRequirement[] {
  if (price <= 0) return [];
  const network = normalizeNetwork(provider.operator.network);
  const symbols = provider.operator.currencies?.usd ?? ["USDT"];
  const payTo = provider.recipients?.[provider.operator.recipient]?.account ?? provider.operator.recipient;
  const schemes: Array<PaymentRequirement["scheme"]> = provider.operator.schemes?.length
    ? provider.operator.schemes.map(scheme => scheme === "exact_gasfree" ? "exact_gasfree" : "exact")
    : [provider.operator.scheme === "exact_gasfree" ? "exact_gasfree" : "exact"];
  return schemes.flatMap(scheme => symbols.map(symbol => {
    const token = getToken(network, symbol);
    const transferMethod = provider.operator.assetTransferMethod || provider.operator.asset_transfer_method || token.assetTransferMethod;
    const amount = toSmallestUnit(price, token.decimals);
    if (amount === "0") throw new Error(`positive price produced zero amount for ${symbol} on ${network}`);
    return {
      scheme,
      network,
      amount,
      asset: token.address,
      payTo,
      maxTimeoutSeconds: provider.operator.valid_for_seconds ?? 300,
      extra: scheme === "exact" && transferMethod === "permit2" ? { assetTransferMethod: "permit2" } : {},
    };
  }));
}
