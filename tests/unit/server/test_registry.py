from __future__ import annotations

import asyncio

from bankofai.x402_gateway.config.loader import load_provider_text
from bankofai.x402_gateway.server.registry import ProviderRegistry


def _provider_yaml() -> str:
    return """
name: path-template-provider
title: "Path Template Provider"
description: "Path template tests"
category: data
version: v1
forward_url: https://api.example.com
routing:
  type: proxy
operator:
  network: tron-mainnet
  currencies:
    usd: ["USDT"]
  recipient: "TProviderWalletBase58"
  scheme: exact_gasfree
  facilitator_url: https://facilitator.example.com
endpoints:
  - method: GET
    path: /prices/current/static
    description: "Exact static price path"
  - method: GET
    path: /prices/current/{coins}
    description: "Current prices"
  - method: GET
    path: /v1/assetQuotation/{blockchain}/{address}
    description: "Asset quotation"
  - method: POST
    path: /v1/assetQuotation/{blockchain}/{address}
    description: "Asset quotation post"
"""


def _registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    spec = load_provider_text(_provider_yaml())
    asyncio.run(registry.replace_all([spec]))
    return registry


def test_match_endpoint_prefers_exact_path() -> None:
    registry = _registry()

    endpoint = registry.match_endpoint(
        "path-template-provider", "GET", "/prices/current/static"
    )

    assert endpoint is not None
    assert endpoint.path == "/prices/current/static"


def test_match_endpoint_supports_single_segment_template() -> None:
    registry = _registry()

    endpoint = registry.match_endpoint(
        "path-template-provider",
        "GET",
        "/prices/current/bsc:0x55d398326f99059ff775485246999027b3197955",
    )

    assert endpoint is not None
    assert endpoint.path == "/prices/current/{coins}"


def test_match_endpoint_supports_multiple_template_segments() -> None:
    registry = _registry()

    endpoint = registry.match_endpoint(
        "path-template-provider",
        "GET",
        "/v1/assetQuotation/Tron/0x0000000000000000000000000000000000000000",
    )

    assert endpoint is not None
    assert endpoint.path == "/v1/assetQuotation/{blockchain}/{address}"


def test_match_endpoint_keeps_method_matching_strict() -> None:
    registry = _registry()

    endpoint = registry.match_endpoint(
        "path-template-provider",
        "POST",
        "/v1/assetQuotation/Tron/0x0000000000000000000000000000000000000000",
    )

    assert endpoint is not None
    assert endpoint.method == "POST"


def test_match_endpoint_rejects_different_segment_count() -> None:
    registry = _registry()

    endpoint = registry.match_endpoint(
        "path-template-provider",
        "GET",
        "/prices/current/bsc:0x55d398326f99059ff775485246999027b3197955/extra",
    )

    assert endpoint is None


def test_match_endpoint_rejects_unknown_provider() -> None:
    registry = _registry()

    assert registry.match_endpoint("missing-provider", "GET", "/prices/current/BTC") is None
