"""Endpoint metering and price computation.

The metering engine resolves a per-request price by:
  1. Choosing the active variant (param + value match) if present, else the
     endpoint's base `metering.dimensions`.
  2. Iterating dimensions; per dimension we pick the tier whose `up_to`
     covers the current usage count (variant resolves per-request).
  3. Summing `price_usd` across dimensions.

For Phase 1 the gateway is stateless (no DB) so we only support `usage=1` per
request — token / character / second counters happen on the response side and
will land in v0.6.6 `upto`. We therefore always pick the first applicable
tier.
"""

from __future__ import annotations

from dataclasses import dataclass

from bankofai.x402_gateway.config.spec import (
    EndpointSpec,
    MeteringDimensionSpec,
    MeteringSpec,
    ProviderSpec,
    SplitSpec,
)


@dataclass(frozen=True)
class PriceResolution:
    price_usd: float
    splits: list[SplitSpec]

    @property
    def is_free(self) -> bool:
        return self.price_usd <= 0.0


def _pick_tier_price_and_splits(
    dim: MeteringDimensionSpec, usage: int = 1
) -> tuple[float, list[SplitSpec]]:
    """Pick the tier that covers `usage` under the dimension's tier ladder."""
    for tier in dim.tiers:
        if tier.up_to is None or usage <= tier.up_to:
            return tier.price_usd, list(tier.splits)
    last = dim.tiers[-1]
    return last.price_usd, list(last.splits)


def _resolve_dimensions(
    metering: MeteringSpec,
    request_params: dict[str, str] | None,
) -> list[MeteringDimensionSpec]:
    if request_params:
        for variant in metering.variants:
            if request_params.get(variant.param) == variant.value:
                return variant.dimensions
    return metering.dimensions


def resolve_endpoint_price(
    endpoint: EndpointSpec,
    *,
    request_params: dict[str, str] | None = None,
    usage: int = 1,
    endpoint_level_splits: list[SplitSpec] | None = None,
) -> PriceResolution:
    """Compute a single USD price for one request.

    `request_params` are used for variant selection; pass query/body params.
    `endpoint_level_splits` come from the endpoint's `metering.splits` and are
    used only when no per-tier splits are set on the chosen tier.
    """
    if endpoint.metering is None:
        return PriceResolution(price_usd=0.0, splits=[])

    dims = _resolve_dimensions(endpoint.metering, request_params)
    total = 0.0
    chosen_splits: list[SplitSpec] = []
    for dim in dims:
        price, tier_splits = _pick_tier_price_and_splits(dim, usage=usage)
        total += price
        if tier_splits and not chosen_splits:
            chosen_splits = tier_splits

    if not chosen_splits and endpoint_level_splits:
        chosen_splits = list(endpoint_level_splits)

    return PriceResolution(price_usd=total, splits=chosen_splits)


def endpoint_price_usd(endpoint: EndpointSpec) -> float:
    """Legacy single-shot price used by admin views.

    Picks the cheapest reachable tier per dimension (usage=1, no variant).
    """
    return resolve_endpoint_price(endpoint).price_usd


def provider_currency_symbols(provider: ProviderSpec) -> list[str]:
    symbols: list[str] = []
    for configured in provider.operator.currencies.values():
        for symbol in configured:
            if symbol not in symbols:
                symbols.append(symbol)
    return symbols


def endpoint_price_options(
    provider: ProviderSpec, endpoint: EndpointSpec
) -> list[dict[str, object]]:
    """Admin-view shape: list of (currency, amountUsd) pairs."""
    price_usd = endpoint_price_usd(endpoint)
    return [
        {
            "currency": currency,
            "amountUsd": price_usd,
            "unit": "request",
        }
        for currency in provider_currency_symbols(provider)
    ]
