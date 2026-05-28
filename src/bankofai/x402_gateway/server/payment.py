"""402 challenge construction, decode, verify, settle.

The gateway's request pipeline is:
    1. metering.resolve_endpoint_price(endpoint, request_params) -> price USD
    2. build_payment_requirements(price, provider) -> list[PaymentRequirements]
    3. build_payment_required(provider, endpoint, requirements) -> PaymentRequired
    4. encode body + PAYMENT-REQUIRED header (base64 JSON)
    5. on retry: decode PAYMENT-SIGNATURE, match to a requirement, call
       facilitator.verify, then facilitator.settle, then proxy upstream and
       attach PAYMENT-RESPONSE on the response.

PaymentRequired uses x402Version=2 (Coinbase v2 wire format).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from bankofai.x402.encoding import decode_payment_payload, encode_payment_payload
from bankofai.x402.tokens import TokenRegistry
from bankofai.x402.types import (
    PaymentPayload,
    PaymentRequired,
    PaymentRequirements,
    ResourceInfo,
    SettleResponse,
    VerifyResponse,
)
from pydantic import ValidationError

from bankofai.x402_gateway.config.spec import EndpointSpec, ProviderSpec
from bankofai.x402_gateway.facilitator.client import FacilitatorAPI
from bankofai.x402_gateway.server.metering import (
    PriceResolution,
    provider_currency_symbols,
    resolve_endpoint_price,
)

logger = logging.getLogger(__name__)

PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE"
PAYMENT_REQUIRED_HEADER = "PAYMENT-REQUIRED"
PAYMENT_RESPONSE_HEADER = "PAYMENT-RESPONSE"


def build_resource_url(provider_name: str, endpoint: EndpointSpec) -> str:
    return f"/providers/{provider_name}{endpoint.path}"


def _usd_to_smallest_unit(price_usd: float, decimals: int) -> int:
    return int(Decimal(str(price_usd)) * (Decimal(10) ** decimals))


def build_payment_requirements_for_price(
    provider: ProviderSpec,
    price_usd: float,
) -> list[PaymentRequirements]:
    """Build per-currency PaymentRequirements for a single USD amount."""
    if price_usd <= 0:
        return []

    requirements: list[PaymentRequirements] = []
    for symbol in provider_currency_symbols(provider):
        token = TokenRegistry.get_token(provider.operator.network, symbol)
        amount = _usd_to_smallest_unit(price_usd, token.decimals)
        requirements.append(
            PaymentRequirements(
                scheme=provider.operator.scheme,
                network=provider.operator.network,
                amount=str(amount),
                asset=token.address,
                payTo=provider.operator.recipient,
                maxTimeoutSeconds=provider.operator.valid_for_seconds,
            )
        )
    return requirements


def build_payment_requirements(
    provider: ProviderSpec,
    endpoint: EndpointSpec,
    *,
    request_params: Optional[dict[str, str]] = None,
) -> tuple[PriceResolution, list[PaymentRequirements]]:
    resolution = resolve_endpoint_price(endpoint, request_params=request_params)
    return resolution, build_payment_requirements_for_price(provider, resolution.price_usd)


def build_payment_required(
    provider: ProviderSpec,
    endpoint: EndpointSpec,
    *,
    request_params: Optional[dict[str, str]] = None,
) -> PaymentRequired:
    _, requirements = build_payment_requirements(provider, endpoint, request_params=request_params)
    return PaymentRequired(
        x402Version=2,
        error="Payment required",
        resource=ResourceInfo(
            url=build_resource_url(provider.name, endpoint),
            description=endpoint.description or provider.description,
        ),
        accepts=requirements,
    )


def encode_payment_required_header(payment_required: PaymentRequired) -> str:
    """Base64-encoded JSON suitable for the `PAYMENT-REQUIRED` HTTP header."""
    return encode_payment_payload(payment_required)


def decode_payment_header(header_value: str) -> PaymentPayload:
    """Decode the client-supplied `PAYMENT-SIGNATURE` header back to a payload."""
    return decode_payment_payload(header_value, PaymentPayload)


def match_requirement(
    payload: PaymentPayload, requirements: list[PaymentRequirements]
) -> Optional[PaymentRequirements]:
    """Find the requirement that matches the payload accepted block."""
    accepted = payload.accepted
    for requirement in requirements:
        if (
            accepted.scheme == requirement.scheme
            and accepted.network == requirement.network
            and accepted.asset.lower() == requirement.asset.lower()
            and str(accepted.amount) == str(requirement.amount)
            and accepted.pay_to == requirement.pay_to
        ):
            return requirement
    return None


def verify_payment_payload_offline(
    payload: PaymentPayload,
    requirements: list[PaymentRequirements],
) -> dict[str, object]:
    """Structural-only verify: matches the payload to a known requirement.

    Used by /__402/verify (debug) and as a pre-check before going to the
    facilitator. Real cryptographic / on-chain verify is the facilitator's job.
    """
    requirement = match_requirement(payload, requirements)
    if requirement is None:
        return {"isValid": False, "invalidReason": "payment_requirement_mismatch"}
    return {
        "isValid": True,
        "invalidReason": None,
        "requirement": requirement.model_dump(by_alias=True),
    }


def verify_payment_header(
    provider: ProviderSpec,
    endpoint: EndpointSpec,
    header_value: str,
    *,
    request_params: Optional[dict[str, str]] = None,
) -> dict[str, object]:
    try:
        payload = decode_payment_header(header_value)
    except (ValueError, ValidationError, TypeError) as exc:
        return {"isValid": False, "invalidReason": f"invalid_payment_payload: {exc}"}
    _, requirements = build_payment_requirements(provider, endpoint, request_params=request_params)
    return verify_payment_payload_offline(payload, requirements)


async def verify_with_facilitator(
    facilitator: FacilitatorAPI,
    payload: PaymentPayload,
    requirement: PaymentRequirements,
) -> VerifyResponse:
    return await facilitator.verify(payload, requirement)


async def settle_with_facilitator(
    facilitator: FacilitatorAPI,
    payload: PaymentPayload,
    requirement: PaymentRequirements,
) -> SettleResponse:
    return await facilitator.settle(payload, requirement)
