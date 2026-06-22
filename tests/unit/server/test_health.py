from __future__ import annotations

import httpx
import pytest

from bankofai.x402_gateway.server.health import (
    probe_balance,
    probe_facilitator_supported,
)


@pytest.mark.asyncio
async def test_facilitator_supported_returns_unreachable_when_url_none() -> None:
    report = await probe_facilitator_supported(None)
    assert report.reachable is False
    assert report.detail == "facilitator_url not configured"


@pytest.mark.asyncio
async def test_facilitator_supported_parses_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "kinds": [
                    {"x402Version": 2, "scheme": "exact", "network": "tron:mainnet"},
                    {"x402Version": 2, "scheme": "exact_permit", "network": "tron:mainnet"},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    report = await probe_facilitator_supported("https://facilitator.example")
    assert report.reachable is True
    assert "exact" in report.schemes
    assert "tron:mainnet" in report.networks


@pytest.mark.asyncio
async def test_facilitator_supported_soft_fails_on_non_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<!doctype html>")

    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    report = await probe_facilitator_supported("https://facilitator.example")
    assert report.reachable is False
    assert "invalid /supported JSON" in (report.detail or "")


@pytest.mark.asyncio
async def test_balance_probe_classifies_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"balance": 0})

    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    report = await probe_balance("tron:mainnet", "TZeroBalance")
    assert report.severity == "zero"
    assert report.raw == 0
    assert "TRX" in report.display


@pytest.mark.asyncio
async def test_balance_probe_soft_fails_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    report = await probe_balance("tron:mainnet", "TBadJson")
    assert report.severity == "unknown"
    assert report.display == "?"


@pytest.mark.asyncio
async def test_balance_probe_classifies_ok_for_funded_tron(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"balance": 200_000_000})  # 200 TRX

    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    report = await probe_balance("tron:mainnet", "TFundedWallet")
    assert report.severity == "ok"
    assert report.raw == 200_000_000


@pytest.mark.asyncio
async def test_balance_probe_evm_classifies_low(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # 0.01 BNB in wei
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": hex(10**16)}
        )

    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    report = await probe_balance("eip155:56", "0xdeadbeef")
    assert report.severity == "low"
    assert "BNB" in report.display


@pytest.mark.asyncio
async def test_balance_probe_unknown_network() -> None:
    report = await probe_balance("solana:mainnet", "SoLanaAddr")
    assert report.severity == "unknown"
    assert "unsupported" in (report.detail or "")
