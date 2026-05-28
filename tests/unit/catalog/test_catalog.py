from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from bankofai.x402_gateway.catalog.build import build_catalog
from bankofai.x402_gateway.catalog.discover import discover
from bankofai.x402_gateway.catalog.probe import (
    ProbeStatus,
    probe_endpoint,
)
from bankofai.x402_gateway.catalog.scaffold import scaffold_listing

LISTING = """\
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
- TODO

## When to use
- TODO
"""


def _write_listing(providers_root: Path, fqn: str, body: str = LISTING) -> Path:
    path = providers_root.joinpath(*fqn.split("/"))
    path.mkdir(parents=True, exist_ok=True)
    target = path / "listing.md"
    target.write_text(body)
    return target


def test_discover_derives_fqn_from_path(tmp_path: Path) -> None:
    _write_listing(tmp_path, "acme/weather")
    results = discover(tmp_path)
    assert len(results) == 1
    assert results[0].fqn == "acme/weather"
    assert results[0].spec.name == "weather"


def test_discover_rejects_fqn_mismatch(tmp_path: Path) -> None:
    body = LISTING.replace("name: weather", "name: rainbow")
    _write_listing(tmp_path, "acme/weather", body=body)
    with pytest.raises(ValueError, match="frontmatter.name"):
        discover(tmp_path)


def test_scaffold_writes_template(tmp_path: Path) -> None:
    path = scaffold_listing(tmp_path, "sunio/perp-swap", "https://api.example/openapi.json")
    text = path.read_text()
    assert "name: perp-swap" in text
    assert "https://api.example/openapi.json" in text
    assert "TODO_SERVICE_URL" in text


@pytest.mark.asyncio
async def test_probe_classifies_ok_response() -> None:
    """A 402 with a parseable PAYMENT-REQUIRED on tron:mainnet USDT == Ok."""
    from bankofai.x402.encoding import encode_payment_payload
    from bankofai.x402.tokens import TokenRegistry
    from bankofai.x402.types import PaymentRequired, PaymentRequirements

    usdt = TokenRegistry.get_token("tron:mainnet", "USDT")
    payment_required = PaymentRequired(
        x402Version=2,
        accepts=[
            PaymentRequirements(
                scheme="exact_permit",
                network="tron:mainnet",
                amount="1000",
                asset=usdt.address,
                payTo="TRecipient",
            )
        ],
    )
    header = encode_payment_payload(payment_required)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            headers={"PAYMENT-REQUIRED": header},
            json=payment_required.model_dump(by_alias=True, exclude_none=True),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await probe_endpoint(client, "https://gw.example.com/x", "GET", "/v1/foo")

    assert result.status == ProbeStatus.OK
    assert result.network == "tron:mainnet"
    assert result.currency == "USDT"


@pytest.mark.asyncio
async def test_probe_classifies_wrong_chain() -> None:
    from bankofai.x402.encoding import encode_payment_payload
    from bankofai.x402.types import PaymentRequired, PaymentRequirements

    payment_required = PaymentRequired(
        x402Version=2,
        accepts=[
            PaymentRequirements(
                scheme="exact",
                network="solana:mainnet",
                amount="1000",
                asset="So1aNaToken",
                payTo="SolanaRecipient",
            )
        ],
    )
    header = encode_payment_payload(payment_required)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            headers={"PAYMENT-REQUIRED": header},
            json=payment_required.model_dump(by_alias=True, exclude_none=True),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await probe_endpoint(client, "https://gw.example.com/x", "GET", "/v1/foo")

    assert result.status == ProbeStatus.WRONG_CHAIN
    assert result.network == "solana:mainnet"


@pytest.mark.asyncio
async def test_probe_classifies_free_when_2xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await probe_endpoint(client, "https://gw.example.com/x", "GET", "/health")
    assert result.status == ProbeStatus.FREE


def test_build_catalog_writes_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    providers_root = tmp_path / "providers"
    providers_root.mkdir()
    _write_listing(providers_root, "acme/weather")

    # Force probe to short-circuit with a NotPaywalled (2xx with no payment) since
    # service_url is not reachable from tests.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    dist_dir = tmp_path / "dist"
    asyncio.run(build_catalog(providers_root, dist_dir))

    skills = json.loads((dist_dir / "skills.json").read_text())
    assert skills["providers"][0]["fqn"] == "acme/weather"

    detail = json.loads((dist_dir / "providers" / "acme__weather.json").read_text())
    assert detail["category"] == "data"
    assert detail["endpoints"][0]["probe_status"] == "free"
