"""Management endpoints under /__402.

These are mounted with an `__402` prefix so they don't shadow seller endpoints.
The shape mirrors gateway.md §2.5.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from bankofai.x402_gateway.catalog.pay_assets import generate_pay_json
from bankofai.x402_gateway.config.spec import EndpointSpec, ProviderSpec
from bankofai.x402_gateway.server.metering import endpoint_price_options, endpoint_price_usd
from bankofai.x402_gateway.server.payment import verify_payment_header
from bankofai.x402_gateway.server.registry import ProviderRegistry
from bankofai.x402_gateway.telemetry.metrics import MetricsStore

router = APIRouter(prefix="/__402")


def get_registry(request: Request) -> ProviderRegistry:
    registry = getattr(request.app.state, "provider_registry", None)
    if not isinstance(registry, ProviderRegistry):
        raise RuntimeError("provider registry is not configured")
    return registry


@router.get("/health")
async def health() -> str:
    return "ok"


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    registry = get_registry(request)
    specs = registry.snapshot()
    states = registry.state_snapshot()
    issues: list[dict[str, str | None]] = []

    if not specs:
        issues.append({"provider": None, "reason": "no providers loaded"})

    for name in specs:
        state = states.get(name)
        if state is None:
            issues.append({"provider": name, "reason": "missing runtime state"})
            continue
        if state.config_status != "loaded":
            issues.append({"provider": name, "reason": f"config {state.config_status}"})
        if state.last_error:
            issues.append({"provider": name, "reason": state.last_error})
        if state.payment_status == "unreachable":
            issues.append({"provider": name, "reason": "facilitator unreachable"})

    status_code = 200 if not issues else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if not issues else "not_ready",
            "provider_count": len(specs),
            "issues": issues,
        },
    )


@router.get("/metrics")
async def metrics(request: Request) -> PlainTextResponse:
    store = getattr(request.app.state, "metrics", None)
    if not isinstance(store, MetricsStore):
        raise HTTPException(status_code=500, detail="metrics store is not configured")
    return PlainTextResponse(store.to_prometheus(), media_type="text/plain; version=0.0.4")


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


def _gateway_base(request: Request) -> str:
    configured = getattr(request.app.state, "public_base_url", None)
    if isinstance(configured, str) and configured:
        return configured.rstrip("/")
    return f"{request.url.scheme}://{request.url.netloc}".rstrip("/")


def _provider_service_url(spec: ProviderSpec, request: Request) -> str:
    if spec.display.service_url:
        return spec.display.service_url.rstrip("/")
    return f"{_gateway_base(request)}/providers/{spec.name}"


def _endpoint_public_payload(
    spec: ProviderSpec,
    endpoint: EndpointSpec,
    *,
    service_url: str,
) -> dict[str, Any]:
    price = endpoint_price_usd(endpoint)
    return {
        "method": endpoint.method,
        "path": endpoint.path,
        "url": f"{service_url}{endpoint.path}",
        "title": endpoint.description or endpoint.path,
        "subtitle": endpoint.path,
        "description": endpoint.description or spec.description,
        "use_case": spec.discovery.use_case or spec.description,
        "i18n": {},
        "metered": endpoint.metering is not None,
        "min_price_usd": price,
        "max_price_usd": price,
    }


def _provider_catalog_detail(
    spec: ProviderSpec,
    request: Request,
    *,
    status: dict[str, str] | None = None,
) -> dict[str, Any]:
    service_url = _provider_service_url(spec, request)
    endpoints_payload = [
        _endpoint_public_payload(spec, endpoint, service_url=service_url)
        for endpoint in spec.endpoints
    ]
    prices = [
        price
        for endpoint in endpoints_payload
        for price in (endpoint["min_price_usd"], endpoint["max_price_usd"])
    ] or [0.0]
    chains = [spec.operator.network]
    return {
        "fqn": spec.name,
        "title": spec.title,
        "subtitle": spec.discovery.use_case or spec.description,
        "description": spec.description,
        "use_case": spec.discovery.use_case or spec.description,
        "i18n": {},
        "logo": spec.display.logo,
        "category": spec.category,
        "chains": chains,
        "is_first_party": False,
        "is_featured": False,
        "featured_tags": spec.display.tags,
        "service_url": service_url,
        "endpoint_count": len(spec.endpoints),
        "has_metering": any(endpoint.metering is not None for endpoint in spec.endpoints),
        "has_free_tier": any(endpoint["min_price_usd"] == 0 for endpoint in endpoints_payload),
        "min_price_usd": min(prices),
        "max_price_usd": max(prices),
        "sha": None,
        "endpoints": endpoints_payload,
        "status": status
        or {
            "catalog": "local",
            "gateway": "loaded",
            "payment": "unknown",
            "upstream": "unknown",
        },
    }


@router.get("/catalog")
async def catalog(request: Request) -> dict[str, Any]:
    registry = get_registry(request)
    states = registry.state_snapshot()
    providers_payload = []
    chains: set[str] = set()
    categories: dict[str, int] = {}
    for name, spec in registry.snapshot().items():
        state = states.get(name)
        detail = _provider_catalog_detail(
            spec,
            request,
            status={
                "catalog": "local",
                "gateway": state.config_status if state else "unknown",
                "payment": state.payment_status if state else "unknown",
                "upstream": state.upstream_status if state else "unknown",
            },
        )
        providers_payload.append(
            {
                key: value
                for key, value in detail.items()
                if key not in {"endpoints", "status"}
            }
        )
        categories[spec.category] = categories.get(spec.category, 0) + 1
        chains.update(detail["chains"])

    providers_payload.sort(key=lambda item: item["fqn"])
    return {
        "version": 1,
        "generated_at": None,
        "provider_count": len(providers_payload),
        "first_party_count": 0,
        "chain_count": len(chains),
        "base_url": f"{_gateway_base(request)}/__402/catalog",
        "frontend": {
            "featured_fqns": [],
            "categories": [
                {"id": category, "count": count}
                for category, count in sorted(categories.items())
            ],
            "chains": [{"id": chain, "count": 1} for chain in sorted(chains)],
        },
        "providers": providers_payload,
    }


@router.get("/catalog/providers/{provider_name}.json")
async def catalog_provider(provider_name: str, request: Request) -> dict[str, Any]:
    registry = get_registry(request)
    spec = registry.get(provider_name)
    if spec is None:
        raise HTTPException(status_code=404, detail="provider not found")
    state = registry.state_snapshot().get(provider_name)
    return _provider_catalog_detail(
        spec,
        request,
        status={
            "catalog": "local",
            "gateway": state.config_status if state else "unknown",
            "payment": state.payment_status if state else "unknown",
            "upstream": state.upstream_status if state else "unknown",
        },
    )


@router.get("/catalog/pay/{provider_name}.json")
async def catalog_pay(provider_name: str, request: Request) -> dict[str, Any]:
    registry = get_registry(request)
    spec = registry.get(provider_name)
    if spec is None:
        raise HTTPException(status_code=404, detail="provider not found")
    gateway_base = _gateway_base(request)
    return generate_pay_json(spec, fallback_gateway_base=gateway_base)


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
