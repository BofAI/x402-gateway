"""Gateway-facing facilitator client.

Thin wrapper around `bankofai.x402.facilitator.FacilitatorClient` that adds:
- A `Protocol` boundary the gateway can use in tests without spinning up the
  real httpx client.
- A no-op stub used when the gateway has no facilitator configured yet — keeps
  the runtime path uniform: the proxy always calls `verify` / `settle` and the
  stub returns a deterministic `is_valid=False` so we 400 without surprises.
"""

from __future__ import annotations

import logging
from typing import Optional, Protocol

from bankofai.x402.facilitator.facilitator_client import FacilitatorClient
from bankofai.x402.types import (
    PaymentPayload,
    PaymentRequirements,
    SettleResponse,
    SupportedResponse,
    VerifyResponse,
)

logger = logging.getLogger(__name__)


class FacilitatorAPI(Protocol):
    """Subset of FacilitatorClient the gateway depends on."""

    async def supported(self) -> SupportedResponse: ...

    async def verify(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> VerifyResponse: ...

    async def settle(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> SettleResponse: ...


class StubFacilitator:
    """In-memory facilitator used when no real one is configured.

    `verify` returns valid=False, `settle` returns success=False. This keeps
    the request pipeline shape consistent in tests / dry runs without
    accidentally minting transactions.
    """

    async def supported(self) -> SupportedResponse:
        return SupportedResponse(kinds=[])

    async def verify(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> VerifyResponse:
        return VerifyResponse(isValid=False, invalidReason="facilitator_not_configured")

    async def settle(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> SettleResponse:
        return SettleResponse(success=False, errorReason="facilitator_not_configured")


def build_facilitator(url: Optional[str]) -> FacilitatorAPI:
    """Build a facilitator from a URL, or return the stub when url is None."""
    if not url:
        logger.warning("no facilitator configured; payments will not settle")
        return StubFacilitator()
    return FacilitatorClient(base_url=url)
