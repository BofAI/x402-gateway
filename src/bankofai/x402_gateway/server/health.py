"""Startup-time health checks (gateway.md §2.1).

Two checks run during startup; both are advisory (warn / banner-color), they
never block startup:

  - facilitator `/supported` probe — confirms the URL is reachable and lists
    schemes/networks we'll be issuing requirements for.
  - recipient native-token balance probe — TRON via TronGrid, BSC via public
    RPC. A zero balance prints a loud warning; a low balance prints a softer
    note.

Both probes have hard timeouts and degrade gracefully when the network is
flaky — the gateway must still come up so seller debugging is possible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Public RPC defaults; can be overridden via env-vars.
TRON_RPC_DEFAULT = "https://api.trongrid.io"
TRON_SHASTA_RPC_DEFAULT = "https://api.shasta.trongrid.io"
TRON_NILE_RPC_DEFAULT = "https://nile.trongrid.io"
BSC_MAINNET_RPC_DEFAULT = "https://bsc-dataseed.binance.org"
BSC_TESTNET_RPC_DEFAULT = "https://data-seed-prebsc-1-s1.binance.org:8545"

# Below this balance the banner colors yellow; below MIN we color red.
TRX_LOW_THRESHOLD_SUN = 50_000_000  # 50 TRX
BNB_LOW_THRESHOLD_WEI = 5 * 10**16  # 0.05 BNB


@dataclass(frozen=True)
class FacilitatorSupportedReport:
    reachable: bool
    schemes: list[str]
    networks: list[str]
    detail: Optional[str] = None


@dataclass(frozen=True)
class BalanceReport:
    network: str
    address: str
    raw: int
    display: str
    severity: str  # "ok" | "low" | "zero" | "unknown"
    detail: Optional[str] = None


async def probe_facilitator_supported(url: Optional[str]) -> FacilitatorSupportedReport:
    """Hit `/supported` and summarize. Soft-fails on any network error."""
    if not url:
        return FacilitatorSupportedReport(
            reachable=False, schemes=[], networks=[], detail="facilitator_url not configured"
        )

    try:
        async with httpx.AsyncClient(
            base_url=url.rstrip("/"), timeout=httpx.Timeout(5.0, connect=2.0)
        ) as client:
            response = await client.get("/supported")
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        return FacilitatorSupportedReport(
            reachable=False, schemes=[], networks=[], detail=str(exc)
        )

    kinds = payload.get("kinds", []) if isinstance(payload, dict) else []
    schemes = sorted({k["scheme"] for k in kinds if isinstance(k, dict) and "scheme" in k})
    networks = sorted({k["network"] for k in kinds if isinstance(k, dict) and "network" in k})
    return FacilitatorSupportedReport(reachable=True, schemes=schemes, networks=networks)


async def probe_balance(network: str, address: str) -> BalanceReport:
    """Query native-token balance for a recipient on TRON / BSC. Soft-fails."""
    try:
        if network.startswith("tron:"):
            return await _probe_tron_balance(network, address)
        if network.startswith("eip155:"):
            return await _probe_evm_balance(network, address)
    except httpx.HTTPError as exc:
        return BalanceReport(
            network=network,
            address=address,
            raw=0,
            display="?",
            severity="unknown",
            detail=str(exc),
        )

    return BalanceReport(
        network=network,
        address=address,
        raw=0,
        display="?",
        severity="unknown",
        detail=f"unsupported network: {network}",
    )


async def _probe_tron_balance(network: str, address: str) -> BalanceReport:
    rpc_url = {
        "tron:mainnet": TRON_RPC_DEFAULT,
        "tron:shasta": TRON_SHASTA_RPC_DEFAULT,
        "tron:nile": TRON_NILE_RPC_DEFAULT,
    }.get(network, TRON_RPC_DEFAULT)

    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
        response = await client.post(
            f"{rpc_url}/wallet/getaccount",
            json={"address": address, "visible": True},
        )
        response.raise_for_status()
        data = response.json() or {}

    balance_sun = int(data.get("balance", 0))
    severity = _classify(balance_sun, TRX_LOW_THRESHOLD_SUN)
    display = f"{balance_sun / 1_000_000:.4f} TRX"
    return BalanceReport(
        network=network,
        address=address,
        raw=balance_sun,
        display=display,
        severity=severity,
    )


async def _probe_evm_balance(network: str, address: str) -> BalanceReport:
    rpc_url = {
        "eip155:56": BSC_MAINNET_RPC_DEFAULT,
        "eip155:97": BSC_TESTNET_RPC_DEFAULT,
    }.get(network, BSC_MAINNET_RPC_DEFAULT)

    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
        response = await client.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_getBalance",
                "params": [address, "latest"],
            },
        )
        response.raise_for_status()
        data = response.json() or {}

    hex_balance = (data.get("result") or "0x0").removeprefix("0x")
    balance_wei = int(hex_balance, 16) if hex_balance else 0
    severity = _classify(balance_wei, BNB_LOW_THRESHOLD_WEI)
    display = f"{balance_wei / 10**18:.6f} BNB"
    return BalanceReport(
        network=network,
        address=address,
        raw=balance_wei,
        display=display,
        severity=severity,
    )


def _classify(raw: int, low_threshold: int) -> str:
    if raw <= 0:
        return "zero"
    if raw < low_threshold:
        return "low"
    return "ok"
