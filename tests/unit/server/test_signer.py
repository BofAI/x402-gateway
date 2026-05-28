from __future__ import annotations

from pathlib import Path

import pytest

from bankofai.x402_gateway.config.spec import ProviderSpec
from bankofai.x402_gateway.server.signer import (
    SignerNotConfigured,
    resolve_signer,
)


def _make_spec(network: str, *, signer_block: dict | None = None, metering=None) -> ProviderSpec:
    payload = {
        "name": "p",
        "title": "P",
        "description": "P",
        "category": "data",
        "version": "v1",
        "routing": {"type": "proxy", "url": "https://upstream.example"},
        "operator": {
            "network": network,
            "currencies": {"usd": ["USDT"]},
            "recipient": "TRecipient",
        },
        "endpoints": [{"method": "GET", "path": "/health", "metering": metering}],
    }
    if signer_block is not None:
        payload["operator"]["signer"] = signer_block
    return ProviderSpec.model_validate(payload)


def test_testnet_resolves_to_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    spec = _make_spec("tron-nile")
    handle = resolve_signer(spec)

    assert handle.origin == "sandbox"
    assert handle.network == "tron:nile"
    assert handle.address is not None and handle.address.startswith("0x")
    assert handle.sandbox_path is not None and handle.sandbox_path.exists()


def test_operator_signer_block_wins_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    spec = _make_spec(
        "tron-mainnet",
        signer_block={"backend": "local_secure", "profile": "prod-tron"},
    )
    handle = resolve_signer(spec)

    assert handle.origin == "operator"
    assert handle.backend == "local_secure"
    assert handle.profile == "prod-tron"


def test_profile_used_when_operator_block_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    spec = _make_spec("tron-mainnet")
    handle = resolve_signer(spec, profile="prod-tron")

    assert handle.origin == "profile"
    assert handle.profile == "prod-tron"


def test_no_signer_with_paid_endpoints_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    metering = {
        "dimensions": [
            {"unit": "requests", "tiers": [{"price_usd": 0.01}]},
        ],
    }
    spec = _make_spec("tron-mainnet", metering=metering)
    with pytest.raises(SignerNotConfigured):
        resolve_signer(spec)


def test_no_signer_with_only_free_endpoints_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    spec = _make_spec("tron-mainnet")
    handle = resolve_signer(spec)
    assert handle.origin == "none"
