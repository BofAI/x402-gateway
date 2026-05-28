"""Management endpoints under /__402.

These are mounted with an `__402` prefix so they don't shadow seller endpoints.
The shape mirrors gateway.md §2.5.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from bankofai.x402_gateway.server.metering import endpoint_price_options
from bankofai.x402_gateway.server.payment import verify_payment_header
from bankofai.x402_gateway.server.registry import ProviderRegistry

router = APIRouter(prefix="/__402")


def get_registry(request: Request) -> ProviderRegistry:
    registry = getattr(request.app.state, "provider_registry", None)
    if not isinstance(registry, ProviderRegistry):
        raise RuntimeError("provider registry is not configured")
    return registry


@router.get("/health")
async def health() -> str:
    return "ok"


@router.get("/providers")
async def providers(request: Request) -> list[dict[str, Any]]:
    registry = get_registry(request)
    specs = registry.snapshot()
    states = registry.state_snapshot()
    result: list[dict[str, Any]] = []
    for name, spec in specs.items():
        state = states.get(name)
        result.append(
            {
                "name": spec.name,
                "title": spec.title,
                "category": spec.category,
                "version": spec.version,
                "status": state.config_status if state else "unknown",
                "upstreamStatus": state.upstream_status if state else "unknown",
                "paymentStatus": state.payment_status if state else "unknown",
                "lastLoadedAt": state.last_loaded_at.isoformat()
                if state and state.last_loaded_at
                else None,
                "lastError": state.last_error if state else None,
                "signer": (
                    {
                        "origin": state.signer.origin,
                        "address": state.signer.address,
                        "backend": state.signer.backend,
                    }
                    if state and state.signer
                    else None
                ),
            }
        )
    return result


@router.get("/endpoints")
async def endpoints(request: Request) -> list[dict[str, Any]]:
    registry = get_registry(request)
    result: list[dict[str, Any]] = []
    for provider in registry.snapshot().values():
        for endpoint in provider.endpoints:
            result.append(
                {
                    "provider": provider.name,
                    "method": endpoint.method,
                    "path": endpoint.path,
                    "gatewayPath": f"/providers/{provider.name}{endpoint.path}",
                    "description": endpoint.description,
                    "network": provider.operator.network,
                    "currencies": provider.operator.currencies,
                    "prices": endpoint_price_options(provider, endpoint),
                    "metered": endpoint.metering is not None,
                }
            )
    return result


@router.post("/verify")
async def verify(request: Request) -> dict[str, Any]:
    body = await request.json()
    provider_name = body.get("provider")
    method = body.get("method", "GET")
    path = body.get("path")
    payment = body.get("payment") or request.headers.get("PAYMENT-SIGNATURE")
    if not provider_name or not path or not payment:
        raise HTTPException(
            status_code=400,
            detail="provider, path, and payment are required",
        )

    registry = get_registry(request)
    provider = registry.get(provider_name)
    endpoint = registry.match_endpoint(provider_name, method, path)
    if provider is None or endpoint is None:
        raise HTTPException(status_code=404, detail="provider endpoint not found")
    return verify_payment_header(provider, endpoint, payment)
