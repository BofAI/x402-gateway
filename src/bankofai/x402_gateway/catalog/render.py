"""Render catalog dist artifacts (`dist/skills.json` + `dist/providers/<fqn>.json`).

The dist contract is consumed by agents / third-party frontends — they
*do not read* listing.md source. See gateway.md §3.6.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bankofai.x402_gateway.catalog.check import CatalogCheckResult, ListingCheck
from bankofai.x402_gateway.catalog.discover import DiscoveredListing
from bankofai.x402_gateway.catalog.probe import ProbeStatus


def _render_endpoints(check: ListingCheck) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for probe in check.probes:
        entry: dict[str, Any] = {
            "method": probe.method,
            "path": probe.path,
            "metered": probe.status != ProbeStatus.FREE,
            "probe_status": probe.status.value,
        }
        if probe.status == ProbeStatus.OK:
            entry["paid"] = {
                "network": probe.network,
                "currency": probe.currency,
                "amount_raw": probe.amount_raw,
            }
        elif probe.network:
            entry["network"] = probe.network
        if probe.detail:
            entry["detail"] = probe.detail
        out.append(entry)
    return out


def render_provider(
    listing: DiscoveredListing, check: ListingCheck
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "fqn": listing.fqn,
        "title": listing.spec.title,
        "category": listing.spec.category,
        "use_case": listing.spec.use_case,
        "description": listing.spec.description,
        "service_url": listing.spec.service_url,
        "tags": list(listing.spec.tags),
        "endpoints": _render_endpoints(check),
        "verdict": {
            "block": check.block,
            "ok_count": check.ok_count,
            "non_compat_count": check.non_compat_count,
            "error_count": check.error_count,
        },
    }
    if listing.spec.logo:
        out["logo"] = listing.spec.logo
    if listing.spec.banner:
        out["banner"] = listing.spec.banner
    if listing.spec.screenshots:
        out["screenshots"] = list(listing.spec.screenshots)
    return out


def render_skills_index(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Minimal index for catalog consumers."""
    providers: list[dict[str, Any]] = []
    for entry in entries:
        provider_entry: dict[str, Any] = {
            "fqn": entry["fqn"],
            "title": entry["title"],
            "category": entry["category"],
            "service_url": entry["service_url"],
            "tags": entry.get("tags", []),
            "block": entry["verdict"]["block"],
        }
        if entry.get("logo"):
            provider_entry["logo"] = entry["logo"]
        providers.append(provider_entry)
    return {"providers": providers}


def write_dist(
    dist_dir: Path,
    listings: list[DiscoveredListing],
    result: CatalogCheckResult,
) -> None:
    providers_dir = dist_dir / "providers"
    providers_dir.mkdir(parents=True, exist_ok=True)

    by_fqn = {listing.fqn: listing for listing in listings}
    rendered: list[dict[str, Any]] = []
    for check in result.listings:
        listing = by_fqn.get(check.fqn)
        if listing is None:
            continue
        provider_json = render_provider(listing, check)
        rendered.append(provider_json)
        target = providers_dir / f"{check.fqn.replace('/', '__')}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(provider_json, indent=2, sort_keys=True) + "\n")

    skills_index = render_skills_index(rendered)
    (dist_dir / "skills.json").write_text(json.dumps(skills_index, indent=2, sort_keys=True) + "\n")
