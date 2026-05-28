"""Filtered OpenAPI mount (gateway.md §2.5).

When the gateway is launched with `--openapi <url>`, we fetch the upstream
OpenAPI document, drop every path not in the provider's `endpoints[]`
allowlist, rewrite `servers[]` to the gateway base URL, and expose the
result at `/openapi.json`.

We never inline the upstream URL — that would advertise the seller's
unprotected origin. We also don't proxy the upstream doc verbatim: rewriting
keeps the surface aligned with the allowlist so clients can't discover paths
that would 404.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

from bankofai.x402_gateway.server.registry import ProviderRegistry

logger = logging.getLogger(__name__)


async def _fetch_openapi(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


def _provider_allowlist(registry: ProviderRegistry) -> dict[str, set[tuple[str, str]]]:
    """{provider_name: set((method, path))}."""
    out: dict[str, set[tuple[str, str]]] = {}
    for name, spec in registry.snapshot().items():
        out[name] = {(endpoint.method, endpoint.path) for endpoint in spec.endpoints}
    return out


def filter_openapi(
    document: dict[str, Any],
    *,
    provider_name: str,
    allowlist: set[tuple[str, str]],
    gateway_base: str,
) -> dict[str, Any]:
    """Filter `paths` against allowlist, rewrite `servers`, return a copy."""
    filtered: dict[str, Any] = deepcopy(document)

    rewritten_paths: dict[str, Any] = {}
    for path, methods in filtered.get("paths", {}).items():
        if not isinstance(methods, dict):
            continue
        kept_methods: dict[str, Any] = {}
        for method, operation in methods.items():
            if (method.upper(), path) in allowlist:
                kept_methods[method] = operation
        if kept_methods:
            # rewrite the path to its gateway-facing form
            gateway_path = f"/providers/{provider_name}{path}"
            rewritten_paths[gateway_path] = kept_methods
    filtered["paths"] = rewritten_paths

    filtered["servers"] = [{"url": gateway_base.rstrip("/")}]
    return filtered


def mount_openapi(app: FastAPI, registry: ProviderRegistry, openapi_url: str) -> None:
    """Replace the default FastAPI /openapi.json with our filtered version."""

    @app.get("/openapi.json", include_in_schema=False)
    async def filtered_openapi(request: Request) -> dict[str, Any]:
        try:
            upstream = await _fetch_openapi(openapi_url)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"openapi fetch failed: {exc}") from exc

        gateway_base = f"{request.url.scheme}://{request.url.netloc}"
        allowlist_by_provider = _provider_allowlist(registry)

        # When the gateway hosts multiple providers we merge their filtered
        # paths into one doc so consumers see a single surface.
        merged: dict[str, Any] = {}
        for provider_name, allowlist in allowlist_by_provider.items():
            sub = filter_openapi(
                upstream,
                provider_name=provider_name,
                allowlist=allowlist,
                gateway_base=gateway_base,
            )
            if not merged:
                merged = sub
            else:
                merged["paths"].update(sub["paths"])

        return merged
