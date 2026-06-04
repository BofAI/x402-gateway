"""Local catalog search over providers/**/{provider.yml,listing.md}."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bankofai.x402_gateway.catalog.discover import DiscoveredListing, discover
from bankofai.x402_gateway.config.loader import load_provider_file
from bankofai.x402_gateway.config.spec import ProviderSpec


@dataclass
class SearchEndpoint:
    method: str
    path: str
    description: str | None = None
    gateway_path: str | None = None


@dataclass
class SearchHit:
    fqn: str
    name: str
    title: str
    description: str
    category: str
    service_url: str
    tags: list[str] = field(default_factory=list)
    source: str = "listing.md"
    provider_yml: str | None = None
    listing_md: str | None = None
    endpoints: list[SearchEndpoint] = field(default_factory=list)
    matched_fields: list[str] = field(default_factory=list)
    score: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fqn": self.fqn,
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "serviceUrl": self.service_url,
            "tags": self.tags,
            "source": self.source,
            "providerYml": self.provider_yml,
            "listingMd": self.listing_md,
            "matchedFields": self.matched_fields,
            "score": self.score,
            "endpoints": [
                {
                    "method": endpoint.method,
                    "path": endpoint.path,
                    "description": endpoint.description,
                    "gatewayPath": endpoint.gateway_path,
                }
                for endpoint in self.endpoints
            ],
        }


def _provider_path(listing: DiscoveredListing) -> Path | None:
    if listing.path.name == "provider.yml":
        return listing.path
    candidate = listing.path.parent / "provider.yml"
    return candidate if candidate.exists() else None


def _listing_path(listing: DiscoveredListing) -> Path | None:
    if listing.path.name == "listing.md":
        return listing.path
    candidate = listing.path.parent / "listing.md"
    return candidate if candidate.exists() else None


def _load_provider(listing: DiscoveredListing) -> ProviderSpec | None:
    path = _provider_path(listing)
    if path is None:
        return None
    return load_provider_file(path)


def _endpoints(provider: ProviderSpec | None) -> list[SearchEndpoint]:
    if provider is None:
        return []
    return [
        SearchEndpoint(
            method=endpoint.method,
            path=endpoint.path,
            description=endpoint.description,
            gateway_path=f"/providers/{provider.name}{endpoint.path}",
        )
        for endpoint in provider.endpoints
    ]


def _field_values(
    listing: DiscoveredListing,
    provider: ProviderSpec | None,
) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {
        "fqn": [listing.fqn],
        "name": [listing.spec.name],
        "title": [listing.spec.title],
        "description": [listing.spec.description],
        "category": [listing.spec.category],
        "use_case": [listing.spec.use_case],
        "tags": listing.spec.tags,
        "body": [listing.body_markdown],
        "service_url": [listing.spec.service_url],
    }
    if provider is None:
        return values

    values["network"] = [provider.operator.network]
    values["currencies"] = [
        symbol
        for symbols in provider.operator.currencies.values()
        for symbol in symbols
    ]
    values["endpoints"] = [
        fragment
        for endpoint in provider.endpoints
        for fragment in (endpoint.method, endpoint.path, endpoint.description or "")
    ]
    return values


FIELD_WEIGHTS = {
    "fqn": 12,
    "name": 12,
    "title": 10,
    "tags": 8,
    "category": 6,
    "endpoints": 6,
    "description": 4,
    "use_case": 4,
    "network": 3,
    "currencies": 3,
    "service_url": 2,
    "body": 1,
}


def _score(query_terms: list[str], fields: dict[str, list[str]]) -> tuple[int, list[str]]:
    score = 0
    matched: list[str] = []
    for field_name, values in fields.items():
        haystack = " ".join(str(value) for value in values).lower()
        if not haystack:
            continue
        count = sum(1 for term in query_terms if term in haystack)
        if count == 0:
            continue
        score += FIELD_WEIGHTS.get(field_name, 1) * count
        matched.append(field_name)
    return score, matched


def search_catalog(
    providers_root: Path,
    query: str,
    *,
    limit: int = 20,
) -> list[SearchHit]:
    terms = [term.lower() for term in query.split() if term.strip()]
    if not terms:
        return []

    hits: list[SearchHit] = []
    for listing in discover(providers_root):
        provider = _load_provider(listing)
        fields = _field_values(listing, provider)
        score, matched = _score(terms, fields)
        if score == 0:
            continue
        provider_path = _provider_path(listing)
        listing_path = _listing_path(listing)
        hits.append(
            SearchHit(
                fqn=listing.fqn,
                name=listing.spec.name,
                title=listing.spec.title,
                description=listing.spec.description,
                category=listing.spec.category,
                service_url=listing.spec.service_url,
                tags=listing.spec.tags,
                source=listing.source,
                provider_yml=str(provider_path) if provider_path else None,
                listing_md=str(listing_path) if listing_path else None,
                endpoints=_endpoints(provider),
                matched_fields=matched,
                score=score,
            )
        )

    hits.sort(key=lambda hit: (-hit.score, hit.fqn))
    return hits[:limit]
