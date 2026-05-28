from __future__ import annotations

import pytest

from bankofai.x402_gateway.config.spec import ProviderSpec


def _minimal_provider_dict() -> dict:
    return {
        "name": "acme-weather",
        "title": "Acme Weather API",
        "description": "Current weather API",
        "category": "data",
        "version": "v1",
        "routing": {"type": "proxy", "url": "https://internal.example"},
        "operator": {
            "network": "tron-mainnet",
            "currencies": {"usd": ["USDT"]},
            "recipient": "TRecipient",
        },
        "endpoints": [{"method": "GET", "path": "/health"}],
    }


def test_provider_spec_minimal_example() -> None:
    spec = ProviderSpec.model_validate(_minimal_provider_dict())
    assert spec.name == "acme-weather"
    assert spec.operator.network == "tron:mainnet"


def test_method_must_be_supported() -> None:
    payload = _minimal_provider_dict()
    payload["endpoints"][0]["method"] = "BOGUS"
    with pytest.raises(ValueError, match="unsupported HTTP method"):
        ProviderSpec.model_validate(payload)


def test_path_must_start_with_slash() -> None:
    payload = _minimal_provider_dict()
    payload["endpoints"][0]["path"] = "missing-slash"
    with pytest.raises(ValueError, match="path must start with"):
        ProviderSpec.model_validate(payload)


def test_proxy_routing_requires_url() -> None:
    payload = _minimal_provider_dict()
    payload["routing"] = {"type": "proxy"}
    with pytest.raises(ValueError, match="routing.url"):
        ProviderSpec.model_validate(payload)


def test_forward_url_shorthand_populates_routing() -> None:
    payload = _minimal_provider_dict()
    payload["routing"] = {"type": "proxy"}
    payload["forward_url"] = "https://upstream.example"
    spec = ProviderSpec.model_validate(payload)
    assert spec.routing.url == "https://upstream.example"


def test_splits_must_reference_declared_recipient() -> None:
    payload = _minimal_provider_dict()
    payload["endpoints"][0]["metering"] = {
        "dimensions": [
            {
                "unit": "requests",
                "tiers": [
                    {
                        "price_usd": 0.01,
                        "splits": [{"recipient": "ghost", "percent": 50}],
                    }
                ],
            }
        ]
    }
    with pytest.raises(ValueError, match="ghost"):
        ProviderSpec.model_validate(payload)


def test_splits_with_known_recipient_validates() -> None:
    payload = _minimal_provider_dict()
    payload["recipients"] = {"vendor": {"account": "TVendor"}}
    payload["endpoints"][0]["metering"] = {
        "dimensions": [
            {
                "unit": "requests",
                "tiers": [
                    {
                        "price_usd": 0.01,
                        "splits": [{"recipient": "vendor", "percent": 60}],
                    }
                ],
            }
        ]
    }
    spec = ProviderSpec.model_validate(payload)
    metering = spec.endpoints[0].metering
    assert metering is not None
    assert metering.dimensions[0].tiers[0].splits[0].recipient == "vendor"


def test_splits_sum_cannot_exceed_100() -> None:
    payload = _minimal_provider_dict()
    payload["recipients"] = {"a": {"account": "TA"}, "b": {"account": "TB"}}
    payload["endpoints"][0]["metering"] = {
        "dimensions": [
            {
                "unit": "requests",
                "tiers": [
                    {
                        "price_usd": 0.01,
                        "splits": [
                            {"recipient": "a", "percent": 70},
                            {"recipient": "b", "percent": 40},
                        ],
                    }
                ],
            }
        ]
    }
    with pytest.raises(ValueError, match="exceeds 100"):
        ProviderSpec.model_validate(payload)


def test_unbounded_tier_must_be_last() -> None:
    payload = _minimal_provider_dict()
    payload["endpoints"][0]["metering"] = {
        "dimensions": [
            {
                "unit": "requests",
                "tiers": [
                    {"price_usd": 0.01},
                    {"price_usd": 0.005, "up_to": 1000},
                ],
            }
        ]
    }
    with pytest.raises(ValueError, match="unbounded tier"):
        ProviderSpec.model_validate(payload)


def test_invalid_category_rejected() -> None:
    payload = _minimal_provider_dict()
    payload["category"] = "maps"  # explicitly excluded from our 17-item whitelist
    with pytest.raises(ValueError):
        ProviderSpec.model_validate(payload)
