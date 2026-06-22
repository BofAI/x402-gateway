"""Live probe of a gateway service_url for x402 compatibility.

Maps every endpoint of a listing to one of seven `ProbeStatus` values
(gateway.md §3.4). The probe is read-only: it sends a single HTTP request
with no `PAYMENT-SIGNATURE` and inspects the response.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional

import httpx
from bankofai.x402.encoding import decode_payment_payload
from bankofai.x402.tokens import TokenRegistry
from bankofai.x402.types import PaymentRequired

logger = logging.getLogger(__name__)

# allowlist for catalog membership; mirrors gateway.md §3.4
ALLOWED_NETWORK_PREFIXES = ("tron:", "eip155:")
ALLOWED_CURRENCIES = ("USDT", "USDC", "USDD")


class ProbeStatus(StrEnum):
    OK = "ok"
    FREE = "free"
    WRONG_CHAIN = "wrong_chain"
    WRONG_CURRENCY = "wrong_currency"
    UNKNOWN_PROTOCOL = "unknown_protocol"
    NOT_PAYWALLED = "not_paywalled"
    ERROR = "error"


@dataclass
class ProbeResult:
    status: ProbeStatus
    method: str
    path: str
    network: Optional[str] = None
    currency: Optional[str] = None
    amount_raw: Optional[str] = None
    detail: Optional[str] = None


def _classify_402(payment_required: PaymentRequired) -> ProbeResult:
    wrong_chain: ProbeResult | None = None
    wrong_currency: ProbeResult | None = None
    for accepts in payment_required.accepts:
        if not any(accepts.network.startswith(prefix) for prefix in ALLOWED_NETWORK_PREFIXES):
            wrong_chain = wrong_chain or ProbeResult(
                status=ProbeStatus.WRONG_CHAIN,
                method="",
                path="",
                network=accepts.network,
            )
            continue
        token = TokenRegistry.find_by_address(accepts.network, accepts.asset)
        symbol = token.symbol if token else None
        if symbol is None or symbol.upper() not in ALLOWED_CURRENCIES:
            wrong_currency = wrong_currency or ProbeResult(
                status=ProbeStatus.WRONG_CURRENCY,
                method="",
                path="",
                network=accepts.network,
                currency=symbol,
            )
            continue
        return ProbeResult(
            status=ProbeStatus.OK,
            method="",
            path="",
            network=accepts.network,
            currency=symbol,
            amount_raw=accepts.amount,
        )
    if wrong_currency is not None:
        return wrong_currency
    if wrong_chain is not None:
        return wrong_chain
    return ProbeResult(status=ProbeStatus.UNKNOWN_PROTOCOL, method="", path="")


async def probe_endpoint(
    client: httpx.AsyncClient,
    service_url: str,
    method: str,
    path: str,
) -> ProbeResult:
    """Send the actual HTTP request and classify the response."""
    url = service_url.rstrip("/") + "/" + path.lstrip("/")
    try:
        response = await client.request(method, url)
    except httpx.HTTPError as exc:
        return ProbeResult(
            status=ProbeStatus.ERROR, method=method, path=path, detail=str(exc)
        )

    if response.status_code == 402:
        # Try header first, then body (per spec.protocol fallback rule)
        header = response.headers.get("PAYMENT-REQUIRED")
        body_dict: Optional[dict] = None
        try:
            body_dict = response.json()
        except ValueError:
            body_dict = None

        payment_required: Optional[PaymentRequired] = None
        if header:
            try:
                decoded = decode_payment_payload(header, PaymentRequired)
                payment_required = decoded if isinstance(decoded, PaymentRequired) else None
            except Exception:
                payment_required = None
        if payment_required is None and isinstance(body_dict, dict):
            try:
                payment_required = PaymentRequired.model_validate(body_dict)
            except Exception:
                payment_required = None
        if payment_required is None:
            return ProbeResult(
                status=ProbeStatus.UNKNOWN_PROTOCOL,
                method=method,
                path=path,
                detail="402 response carried no parseable PAYMENT-REQUIRED",
            )

        result = _classify_402(payment_required)
        return ProbeResult(
            status=result.status,
            method=method,
            path=path,
            network=result.network,
            currency=result.currency,
            amount_raw=result.amount_raw,
            detail=result.detail,
        )

    if 200 <= response.status_code < 300:
        return ProbeResult(status=ProbeStatus.FREE, method=method, path=path)

    return ProbeResult(
        status=ProbeStatus.NOT_PAYWALLED,
        method=method,
        path=path,
        detail=f"unexpected status {response.status_code}",
    )
