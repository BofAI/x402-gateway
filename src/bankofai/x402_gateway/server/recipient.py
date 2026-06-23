"""Recipient resolution (gateway.md §2.1).

3-level fallback for the operator's payment recipient:

  1. `operator.recipient` explicit value (after `${VAR}` expansion at load time).
  2. `recipients[<alias>].account` when `operator.recipient` references a
     declared alias by its key.
  3. Signer-derived address (sandbox / operator backend) — used only when
     `operator.recipient` was left blank.

Returns a `RecipientResolution` so the banner / health check can show where
the address came from without re-running the resolver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from bankofai.x402_gateway.config.spec import ProviderSpec
from bankofai.x402_gateway.server.signer import SignerHandle

RecipientOrigin = Literal["explicit", "alias", "signer"]


@dataclass(frozen=True)
class RecipientResolution:
    address: str
    origin: RecipientOrigin
    alias: Optional[str] = None


class RecipientNotConfigured(Exception):
    """Raised when no recipient can be resolved."""


def resolve_recipient(spec: ProviderSpec, signer: SignerHandle) -> RecipientResolution:
    recipient = spec.operator.recipient.strip() if spec.operator.recipient else ""

    if recipient:
        # alias lookup: recipient field equals a declared alias key
        if recipient in spec.recipients:
            return RecipientResolution(
                address=spec.recipients[recipient].account,
                origin="alias",
                alias=recipient,
            )
        return RecipientResolution(address=recipient, origin="explicit")

    if signer.address:
        return RecipientResolution(address=signer.address, origin="signer")

    raise RecipientNotConfigured(
        "no recipient configured; fix by one of: "
        "(a) set `operator.recipient` to a TRON/EVM address; "
        "(b) point `operator.recipient` at a key declared in `recipients`; "
        "(c) configure `operator.signer` so we can derive an address from the key."
    )
