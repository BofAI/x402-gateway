import { paymentRequirements, priceUsd, type ProviderConfig, type ProviderEntry } from "./config.js";

function configs(input: Map<string, ProviderEntry> | Iterable<ProviderConfig>): ProviderConfig[] {
  return input instanceof Map ? [...input.values()].map(entry => entry.config) : [...input];
}

export function providerCatalogProjection(provider: ProviderConfig): Record<string, unknown> {
  return {
    name: provider.name,
    title: provider.title ?? provider.name,
    description: "",
    category: "other",
    network: provider.operator.network,
    currency: provider.operator.currencies?.usd?.[0] ?? "USDT",
    endpoints: (provider.endpoints ?? []).map(endpoint => {
      const price = priceUsd(endpoint);
      const requirements = paymentRequirements(provider, price);
      return {
        method: endpoint.method.toUpperCase(),
        path: `/providers/${provider.name}${endpoint.path}`,
        upstream_path: endpoint.path,
        description: "",
        paid: price > 0 ? {
          scheme: requirements[0]?.scheme,
          network: requirements[0]?.network,
          currency: provider.operator.currencies?.usd?.[0] ?? "USDT",
          price_usd: price,
        } : null,
        x402_routes: requirements.map(requirement => ({
          provider: provider.name,
          network: requirement.network,
          scheme: requirement.scheme,
          ...(requirement.extra?.assetTransferMethod ? { assetTransferMethod: requirement.extra.assetTransferMethod } : {}),
          url: `/providers/${provider.name}${endpoint.path}`,
        })),
      };
    }),
  };
}

export function buildGatewayCatalog(input: Map<string, ProviderEntry> | Iterable<ProviderConfig>): Record<string, unknown> {
  return { version: 1, generatedAt: new Date().toISOString(), providers: configs(input).map(providerCatalogProjection) };
}

export function providerPaymentAssets(input: Map<string, ProviderEntry> | Iterable<ProviderConfig>): Array<Record<string, unknown>> {
  return configs(input).flatMap(provider => (provider.endpoints ?? []).flatMap(endpoint => {
    const price = priceUsd(endpoint);
    const currencies = provider.operator.currencies?.usd ?? ["USDT"];
    return paymentRequirements(provider, price).map((requirement, index) => ({
      provider: provider.name,
      method: endpoint.method,
      path: `/providers/${provider.name}${endpoint.path}`,
      network: requirement.network,
      currency: currencies[index % currencies.length],
      price_usd: price,
      scheme: requirement.scheme,
      ...(requirement.extra?.assetTransferMethod ? { assetTransferMethod: requirement.extra.assetTransferMethod } : {}),
    }));
  }));
}
