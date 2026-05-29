"""`catalog check` pipeline: static -> probe -> verdict.

Verdict semantics (gateway.md §3.4):
  - block=True if zero OK endpoints across the listing
  - non-OK results are warnings, not errors, unless we have nothing else
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import httpx

from bankofai.x402_gateway.catalog.discover import DiscoveredListing, discover
from bankofai.x402_gateway.catalog.probe import ProbeResult, ProbeStatus, probe_endpoint
from bankofai.x402_gateway.catalog.scaffold import (
    OpenAPIOperation,
    fetch_openapi,
    parse_openapi_operations,
)

logger = logging.getLogger(__name__)

DEFAULT_PROBE_CONCURRENCY = 8
# Cap how many OpenAPI operations we probe so a 500-path API doesn't DoS the
# seller's own gateway. First N covers the common case (small APIs).
PROBE_OPERATIONS_PER_LISTING = 16


@dataclass
class ListingCheck:
    fqn: str
    path: Path
    probes: list[ProbeResult] = field(default_factory=list)
    block: bool = False
    ok_count: int = 0
    non_compat_count: int = 0
    error_count: int = 0
    section_warnings: list[str] = field(default_factory=list)

    def summarize(self) -> None:
        self.ok_count = sum(1 for p in self.probes if p.status == ProbeStatus.OK)
        self.non_compat_count = sum(
            1
            for p in self.probes
            if p.status in (ProbeStatus.WRONG_CHAIN, ProbeStatus.WRONG_CURRENCY)
        )
        error_statuses = {
            ProbeStatus.UNKNOWN_PROTOCOL,
            ProbeStatus.NOT_PAYWALLED,
            ProbeStatus.ERROR,
        }
        self.error_count = sum(1 for p in self.probes if p.status in error_statuses)
        self.block = self.ok_count == 0


@dataclass
class CatalogCheckResult:
    listings: list[ListingCheck] = field(default_factory=list)
    block: bool = False


def static_check(providers_root: Path) -> list[DiscoveredListing]:
    """Run the static-only step. Raises on any frontmatter/FQN/body problem."""
    return discover(providers_root)


def _fallback_probe_targets() -> list[tuple[str, str]]:
    """When OpenAPI is unavailable we probe the service root path."""
    return [("GET", "/")]


async def _resolve_probe_targets(
    listing: DiscoveredListing,
    *,
    client: httpx.AsyncClient,
) -> list[tuple[str, str]]:
    if listing.probe_targets:
        return listing.probe_targets
    if listing.spec.openapi and listing.spec.openapi.url:
        try:
            document = await fetch_openapi(listing.spec.openapi.url)
        except httpx.HTTPError as exc:
            logger.warning(
                "%s: openapi fetch failed (%s); falling back to /-probe", listing.fqn, exc
            )
            return _fallback_probe_targets()
        operations = parse_openapi_operations(document)
        operations = operations[:PROBE_OPERATIONS_PER_LISTING]
        return [(op.method, op.path) for op in operations] or _fallback_probe_targets()
    return _fallback_probe_targets()


async def _probe_listing(
    listing: DiscoveredListing,
    *,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    endpoints: Optional[list[tuple[str, str]]] = None,
) -> ListingCheck:
    out = ListingCheck(fqn=listing.fqn, path=listing.path)
    out.section_warnings = [
        f"missing advisory section: {section}"
        for section in listing.missing_advisory_sections
    ]
    targets = endpoints if endpoints is not None else await _resolve_probe_targets(
        listing, client=client
    )

    async def _one(method: str, path: str) -> ProbeResult:
        async with sem:
            return await probe_endpoint(client, listing.spec.service_url, method, path)

    out.probes = list(await asyncio.gather(*[_one(m, p) for m, p in targets]))
    out.summarize()
    return out


async def probe_all(
    listings: Iterable[DiscoveredListing],
    *,
    concurrency: int = DEFAULT_PROBE_CONCURRENCY,
    endpoints_for: Optional[dict[str, list[tuple[str, str]]]] = None,
) -> CatalogCheckResult:
    sem = asyncio.Semaphore(concurrency)
    timeout = httpx.Timeout(10.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        checks = await asyncio.gather(
            *[
                _probe_listing(
                    listing,
                    client=client,
                    sem=sem,
                    endpoints=(endpoints_for or {}).get(listing.fqn),
                )
                for listing in listings
            ]
        )

    result = CatalogCheckResult(listings=list(checks))
    result.block = all(c.block for c in result.listings) if result.listings else True
    return result


async def check_catalog(providers_root: Path) -> CatalogCheckResult:
    listings = static_check(providers_root)
    return await probe_all(listings)


__all__ = [
    "CatalogCheckResult",
    "ListingCheck",
    "OpenAPIOperation",
    "check_catalog",
    "probe_all",
    "static_check",
]
