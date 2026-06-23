"""Signer 4-level fallback (gateway.md §2.3).

Priority order (first match wins):
  1. sandbox / testnet            -> ephemeral file-backed account
  2. operator.signer block        -> declared backend (privy / local_secure / raw_secret)
  3. CLI --profile or setup       -> agent_wallet.load_profile (TODO when agent-wallet lands)
  4. nothing                      -> no signer handle

For v0.6.1 the gateway does not actually need a signer in the request path —
the buyer signs the payment authorization; the facilitator submits on-chain.
We resolve a `SignerHandle` here purely so startup can surface configuration
errors early for explicitly configured signers and so future schemes
(merchant co-sign) can hook in.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from bankofai.x402_gateway.config.spec import OperatorSpec, ProviderSpec

logger = logging.getLogger(__name__)

SignerOrigin = Literal["sandbox", "operator", "profile", "none"]


@dataclass(frozen=True)
class SignerHandle:
    """Opaque pointer to where the signing material lives.

    No private-key material is stored on this dataclass; it stays in the
    backend (OS keystore, env var, sandbox file).
    """

    origin: SignerOrigin
    network: str
    address: Optional[str] = None
    backend: Optional[str] = None  # e.g. "raw_secret", "local_secure", "privy"
    profile: Optional[str] = None
    sandbox_path: Optional[Path] = None


class SignerNotConfigured(Exception):
    """Raised when a future signer-required mode cannot resolve signing material."""


def _is_testnet(network: str) -> bool:
    return any(
        network.endswith(suffix)
        for suffix in (
            ":shasta",
            ":nile",
            "-testnet",
        )
    )


def _sandbox_path() -> Path:
    return Path.home() / ".x402-gateway" / "sandbox" / "accounts.yml"


def _ensure_sandbox_account(network: str) -> tuple[str, Path]:
    """Return (sandbox_address, sandbox_file). Creates a per-network entry on first use."""
    path = _sandbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, dict[str, str]] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text() or "{}")
        except json.JSONDecodeError:
            data = {}
    if network not in data:
        # 20-byte random address; sandbox mode is not security-sensitive.
        addr = "0x" + secrets.token_hex(20)
        data[network] = {"address": addr}
        path.write_text(json.dumps(data, indent=2, sort_keys=True))
    return data[network]["address"], path


def has_paid_endpoints(spec: ProviderSpec) -> bool:
    return any(endpoint.metering is not None for endpoint in spec.endpoints)


def resolve_signer(
    spec: ProviderSpec,
    *,
    sandbox: bool = False,
    profile: Optional[str] = None,
) -> SignerHandle:
    """Walk the 4-level fallback and return a SignerHandle."""

    operator = spec.operator

    # Level 1: --sandbox or testnet network
    if sandbox or _is_testnet(operator.network):
        address, path = _ensure_sandbox_account(operator.network)
        return SignerHandle(
            origin="sandbox",
            network=operator.network,
            address=address,
            sandbox_path=path,
        )

    # Level 2: operator.signer block
    if operator.signer is not None:
        return _resolve_from_operator(operator)

    # Level 3: --profile
    if profile:
        return SignerHandle(
            origin="profile",
            network=operator.network,
            address=operator.recipient,
            profile=profile,
        )

    # Level 4: no signer. This is valid for the current paid proxy flow:
    # the client signs the payment authorization and the facilitator handles
    # verification/settlement. The provider recipient is still resolved from
    # operator.recipient or recipients aliases.
    if has_paid_endpoints(spec):
        logger.info(
            "provider %s has paid endpoints without a gateway signer; "
            "continuing because current x402 payment flows are client-signed",
            spec.name,
        )
    return SignerHandle(origin="none", network=operator.network, address=operator.recipient)


def _resolve_from_operator(operator: OperatorSpec) -> SignerHandle:
    assert operator.signer is not None
    backend = operator.signer.backend
    if backend == "raw_secret":
        env_name = (operator.signer.profile or "X402_GATEWAY_RAW_SECRET").upper()
        if not os.environ.get(env_name):
            logger.warning(
                "raw_secret signer references missing env var %s — "
                "expect signing operations to fail at runtime",
                env_name,
            )
    return SignerHandle(
        origin="operator",
        network=operator.network,
        address=operator.recipient,
        backend=backend,
        profile=operator.signer.profile,
    )
