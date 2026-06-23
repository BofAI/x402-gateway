from __future__ import annotations

import pytest

from bankofai.x402_gateway.config.spec import ProviderSpec
from bankofai.x402_gateway.server.recipient import (
    RecipientNotConfigured,
    resolve_recipient,
)
from bankofai.x402_gateway.server.signer import SignerHandle


def _spec(recipient: str, recipients: dict | None = None) -> ProviderSpec:
    payload = {
        "name": "p",
        "title": "P",
        "description": "P",
        "category": "data",
        "version": "v1",
        "routing": {"type": "proxy", "url": "https://upstream.example"},
        "operator": {
            "network": "tron-mainnet",
            "currencies": {"usd": ["USDT"]},
            "recipient": recipient,
        },
        "endpoints": [{"method": "GET", "path": "/health"}],
    }
    if recipients:
        payload["recipients"] = recipients
    return ProviderSpec.model_validate(payload)


def _signer(address: str | None = None) -> SignerHandle:
    return SignerHandle(
        origin="operator",
        network="tron:mainnet",
        address=address,
    )


def test_explicit_recipient_wins() -> None:
    spec = _spec("TExplicitRecipient")
    resolution = resolve_recipient(spec, _signer("TSignerAddr"))
    assert resolution.origin == "explicit"
    assert resolution.address == "TExplicitRecipient"


def test_alias_lookup_resolves_to_account() -> None:
    spec = _spec("vendor", recipients={"vendor": {"account": "TVendorWallet"}})
    resolution = resolve_recipient(spec, _signer())
    assert resolution.origin == "alias"
    assert resolution.alias == "vendor"
    assert resolution.address == "TVendorWallet"


def test_signer_fallback_when_recipient_blank() -> None:
    spec = _spec("")
    resolution = resolve_recipient(spec, _signer("TSignerDerivedAddr"))
    assert resolution.origin == "signer"
    assert resolution.address == "TSignerDerivedAddr"


def test_raises_when_nothing_resolves() -> None:
    spec = _spec("")
    with pytest.raises(RecipientNotConfigured):
        resolve_recipient(spec, _signer(None))
