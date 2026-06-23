"""Business validation for provider specs.

Pydantic validators on `ProviderSpec` cover structural rules. Validators here
cover relationships against external registries (token registry) and cross-
field constraints that pydantic doesn't model.
"""

from __future__ import annotations

from bankofai.x402.exceptions import UnknownTokenError
from bankofai.x402.tokens import TokenRegistry

from bankofai.x402_gateway.config.spec import ProviderSpec


def validate_provider_spec(spec: ProviderSpec) -> None:
    """Validate rules that depend on external state (token registry, uniqueness)."""

    # 1. endpoint method+path must be unique
    seen: set[tuple[str, str]] = set()
    for endpoint in spec.endpoints:
        key = (endpoint.method, endpoint.path)
        if key in seen:
            raise ValueError(f"duplicate endpoint: {endpoint.method} {endpoint.path}")
        seen.add(key)

    # 2. every currency must be known by the token registry for the operator network
    for currency_symbols in spec.operator.currencies.values():
        for symbol in currency_symbols:
            try:
                TokenRegistry.get_token(spec.operator.network, symbol)
            except UnknownTokenError as exc:
                raise ValueError(
                    f"unknown token {symbol} on network {spec.operator.network}: {exc}"
                ) from exc
