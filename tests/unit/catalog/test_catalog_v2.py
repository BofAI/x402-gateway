"""Coverage for the v0.6.1 catalog additions: body section enforcement,
OpenAPI-driven probe targets, and incremental build with --previous-dist."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from bankofai.x402_gateway.catalog.build import build_catalog
from bankofai.x402_gateway.catalog.discover import discover
from bankofai.x402_gateway.catalog.scaffold import (
    parse_openapi_operations,
    scaffold_listing,
)

LISTING_FULL = """\
---
name: weather
title: "Acme Weather"
description: "Weather data"
use_case: "Look up current weather"
category: data
service_url: https://gw.example.com/acme/weather
tags: [weather]
---

## Spend-aware usage
- Prefer current weather before forecast.

## When to use
- For paid weather lookups.

## When NOT to use
- For historical climate analysis.

## Request examples
- `GET /v1/current?city=Beijing`

## Response examples
- `{"temp_c": 26.5}`
"""

LISTING_MISSING_REQUIRED = """\
---
name: weather
title: "Acme Weather"
description: "Weather data"
use_case: "Lookup"
category: data
service_url: https://gw.example.com/acme/weather
tags: [weather]
---

## When to use
- Note: missing "Spend-aware usage" section
"""


def _write_listing(providers_root: Path, fqn: str, body: str) -> Path:
    path = providers_root.joinpath(*fqn.split("/"))
    path.mkdir(parents=True, exist_ok=True)
    target = path / "listing.md"
    target.write_text(body)
    return target


def test_discover_rejects_listing_missing_required_section(tmp_path: Path) -> None:
    _write_listing(tmp_path, "acme/weather", LISTING_MISSING_REQUIRED)
    with pytest.raises(ValueError, match="Spend-aware usage"):
        discover(tmp_path)


def test_discover_accepts_full_listing(tmp_path: Path) -> None:
    _write_listing(tmp_path, "acme/weather", LISTING_FULL)
    results = discover(tmp_path)
    assert len(results) == 1
    listing = results[0]
    assert "Spend-aware usage" in listing.section_titles
    assert listing.missing_required_sections == []
    assert listing.missing_advisory_sections == []


def test_discover_records_advisory_warnings(tmp_path: Path) -> None:
    minimal = LISTING_FULL.split("## Request examples")[0]
    _write_listing(tmp_path, "acme/weather", minimal)
    results = discover(tmp_path)
    assert "Request examples" in results[0].missing_advisory_sections
    assert "Response examples" in results[0].missing_advisory_sections


def test_parse_openapi_operations_extracts_methods() -> None:
    document = {
        "paths": {
            "/v1/foo": {"get": {"summary": "get foo"}, "post": {"summary": "create foo"}},
            "/v1/bar": {"delete": {}},
            "/v1/skip-this-non-dict": "noise",
        }
    }
    ops = parse_openapi_operations(document)
    methods = sorted((op.method, op.path) for op in ops)
    assert methods == [
        ("DELETE", "/v1/bar"),
        ("GET", "/v1/foo"),
        ("POST", "/v1/foo"),
    ]


def test_incremental_build_copies_previous_unchanged_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    providers_root = tmp_path / "providers"
    providers_root.mkdir()
    _write_listing(providers_root, "acme/weather", LISTING_FULL)
    _write_listing(providers_root, "acme/stocks", LISTING_FULL.replace("weather", "stocks"))

    # Pretend "previous-dist" already has a stocks JSON from a prior build
    prev_dist = tmp_path / "prev-dist"
    prev_providers = prev_dist / "providers"
    prev_providers.mkdir(parents=True)
    pre_existing = {
        "fqn": "acme/stocks",
        "title": "Acme Stocks (from previous build)",
        "category": "finance",
        "use_case": "Lookup stock prices",
        "description": "Stock data",
        "service_url": "https://gw.example.com/acme/stocks",
        "tags": ["stocks"],
        "endpoints": [],
        "verdict": {"block": False, "ok_count": 1, "non_compat_count": 0, "error_count": 0},
    }
    (prev_providers / "acme__stocks.json").write_text(json.dumps(pre_existing))

    # Mock httpx so we only "probe" weather; stocks should be copied verbatim
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    dist_dir = tmp_path / "dist"
    asyncio.run(
        build_catalog(
            providers_root,
            dist_dir,
            only=["acme/weather"],
            previous_dist=prev_dist,
        )
    )

    skills = json.loads((dist_dir / "skills.json").read_text())
    fqns = {entry["fqn"] for entry in skills["providers"]}
    assert fqns == {"acme/weather", "acme/stocks"}

    stocks_path = dist_dir / "providers" / "acme__stocks.json"
    stocks_content = json.loads(stocks_path.read_text())
    assert stocks_content["title"] == "Acme Stocks (from previous build)"


def test_scaffold_embeds_operations_comment(tmp_path: Path) -> None:
    ops = parse_openapi_operations(
        {
            "paths": {
                "/v1/foo": {"get": {"summary": "get foo"}},
                "/v1/bar": {"post": {"operationId": "createBar"}},
            }
        }
    )
    path = scaffold_listing(
        tmp_path,
        "demo/api",
        "https://api.example/openapi.json",
        operations=ops,
    )
    text = path.read_text()
    assert "discovered operations from OpenAPI" in text
    assert "GET     /v1/foo" in text
    assert "POST    /v1/bar" in text
