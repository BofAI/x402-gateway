from __future__ import annotations

from pathlib import Path

from bankofai.x402_gateway.catalog.search import search_catalog


LISTING = """\
---
name: weather
title: "Acme Weather"
description: "Weather data"
use_case: "Look up current weather"
category: data
service_url: https://gw.example.com/acme/weather
tags: [weather, forecast]
---

## Spend-aware usage
- Prefer current weather before forecast.

## When to use
- For city weather lookup.
"""


PROVIDER = """\
name: weather
title: "Acme Weather API"
description: "Current weather API"
category: data
version: v1

forward_url: https://internal.example

routing:
  type: proxy

operator:
  network: tron-mainnet
  currencies:
    usd: ["USDT"]
  recipient: "TProviderWalletBase58"

display:
  service_url: https://gw.example.com/providers/weather
  logo: https://example.com/logo.png
  tags: [weather, data]

discovery:
  use_case: "Look up current weather"
  spend_aware_usage:
    - "Use health checks before paid calls."
  when_to_use:
    - "Use for live weather lookup."

endpoints:
  - method: GET
    path: /v1/current
    description: "Current weather for a city"
    metering:
      dimensions:
        - unit: requests
          tiers:
            - price_usd: 0.002
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_search_listing_metadata(tmp_path: Path) -> None:
    _write(tmp_path / "acme" / "weather" / "listing.md", LISTING)

    hits = search_catalog(tmp_path, "forecast")

    assert len(hits) == 1
    assert hits[0].fqn == "acme/weather"
    assert "tags" in hits[0].matched_fields


def test_search_provider_yml_endpoints(tmp_path: Path) -> None:
    _write(tmp_path / "weather" / "provider.yml", PROVIDER)

    hits = search_catalog(tmp_path, "current")

    assert len(hits) == 1
    assert hits[0].fqn == "weather"
    assert hits[0].provider_yml is not None
    assert hits[0].endpoints[0].gateway_path == "/providers/weather/v1/current"


def test_search_returns_empty_for_no_match(tmp_path: Path) -> None:
    _write(tmp_path / "acme" / "weather" / "listing.md", LISTING)

    assert search_catalog(tmp_path, "settlement") == []
