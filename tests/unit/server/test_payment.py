"""Tests for the payment.py pipeline helpers."""

from __future__ import annotations

import base64
import json

from bankofai.x402.encoding import encode_payment_payload
from bankofai.x402.types import (
    PaymentPayload,
    PaymentPayloadData,
    PaymentRequirements,
)

from bankofai.x402_gateway.config.spec import ProviderSpec
from bankofai.x402_gateway.server.payment import (
    build_payment_required,
    build_payment_requirements_for_price,
    encode_payment_required_header,
    match_requirement,
    verify_payment_header,
)


def _spec() -> ProviderSpec:
    return ProviderSpec.model_validate(
        {
            "name": "acme",
            "title": "Acme",
            "description": "Acme",
            "category": "data",
            "version": "v1",
            "routing": {"type": "proxy", "url": "https://upstream.example"},
            "operator": {
                "network": "tron-mainnet",
                "currencies": {"usd": ["USDT"]},
                "recipient": "TAcmeRecipient",
            },
            "endpoints": [
                {
                    "method": "GET",
                    "path": "/v1/info",
                    "metering": {
                        "dimensions": [
                            {
                                "unit": "requests",
                                "tiers": [{"price_usd": 0.01}],
                            }
                        ]
                    },
                }
            ],
        }
    )


def test_build_payment_requirements_includes_amount_in_smallest_units() -> None:
    spec = _spec()
    requirements = build_payment_requirements_for_price(spec, 0.01)
    assert len(requirements) == 1
    # USDT on TRON has 6 decimals: 0.01 USD == 10_000 base units
    assert requirements[0].amount == "10000"
    assert requirements[0].scheme == "exact_permit"
    assert requirements[0].network == "tron:mainnet"


def test_payment_required_carries_v2_protocol() -> None:
    spec = _spec()
    payment_required = build_payment_required(spec, spec.endpoints[0])
    assert payment_required.x402_version == 2
    assert payment_required.accepts[0].pay_to == "TAcmeRecipient"


def test_payment_required_header_round_trips() -> None:
    spec = _spec()
    payment_required = build_payment_required(spec, spec.endpoints[0])
    header = encode_payment_required_header(payment_required)

    decoded_json = json.loads(base64.b64decode(header).decode())
    assert decoded_json["x402Version"] == 2
    assert decoded_json["accepts"][0]["network"] == "tron:mainnet"


def test_match_requirement_finds_matching_block() -> None:
    spec = _spec()
    requirements = build_payment_requirements_for_price(spec, 0.01)
    accepted = requirements[0]
    payload = PaymentPayload(
        x402Version=2,
        accepted=accepted,
        payload=PaymentPayloadData(signature="0xdeadbeef"),
    )

    matched = match_requirement(payload, requirements)
    assert matched is not None
    assert matched.asset.lower() == accepted.asset.lower()


def test_match_requirement_rejects_amount_mismatch() -> None:
    spec = _spec()
    requirements = build_payment_requirements_for_price(spec, 0.01)
    accepted = PaymentRequirements(
        scheme=requirements[0].scheme,
        network=requirements[0].network,
        amount="999999",
        asset=requirements[0].asset,
        payTo=requirements[0].pay_to,
    )
    payload = PaymentPayload(
        x402Version=2,
        accepted=accepted,
        payload=PaymentPayloadData(signature="0xdeadbeef"),
    )
    assert match_requirement(payload, requirements) is None


def test_verify_payment_header_accepts_round_trip() -> None:
    spec = _spec()
    requirements = build_payment_requirements_for_price(spec, 0.01)
    payload = PaymentPayload(
        x402Version=2,
        accepted=requirements[0],
        payload=PaymentPayloadData(signature="0xdeadbeef"),
    )
    header = encode_payment_payload(payload)

    result = verify_payment_header(spec, spec.endpoints[0], header)
    assert result["isValid"] is True


def test_decode_payment_header_invalid_payload_fails_offline() -> None:
    spec = _spec()
    result = verify_payment_header(spec, spec.endpoints[0], "not-a-base64-payload")
    assert result["isValid"] is False
    reason = result["invalidReason"]
    assert isinstance(reason, str)
    assert "invalid_payment_payload" in reason
