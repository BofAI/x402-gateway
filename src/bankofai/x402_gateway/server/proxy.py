"""Reverse proxy: 402 challenge, facilitator verify+settle, upstream forward.

Pipeline (see gateway.md §2.4):
  1. Match provider + endpoint against the allowlist (404 if missing).
  2. If free endpoint: forward upstream directly.
  3. If metered, no PAYMENT-SIGNATURE: respond 402 + PAYMENT-REQUIRED header.
  4. If metered with PAYMENT-SIGNATURE:
       a. decode payload, structurally match to a requirement
       b. facilitator.verify -> 400 if invalid
       c. facilitator.settle -> 500 if failed
       d. forward upstream
       e. attach PAYMENT-RESPONSE header (base64 settle response)
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx
from bankofai.x402.encoding import encode_payment_payload
from bankofai.x402.types import PaymentRequired
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.responses import Response as FastAPIResponse

from bankofai.x402_gateway.server.auth import build_auth_strategy
from bankofai.x402_gateway.server.payment import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    build_payment_required,
    decode_payment_header,
    encode_payment_required_header,
    match_requirement,
)
from bankofai.x402_gateway.server.registry import ProviderEntry, ProviderRegistry

logger = logging.getLogger(__name__)

# Headers we strip from the *incoming* client request before forwarding upstream.
# `authorization` is critical: client auth must never reach upstream.
STRIP_REQUEST_HEADERS = frozenset(
    {
        "host",
        "connection",
        "transfer-encoding",
        "authorization",
        "proxy-authorization",
        "x-payment",
        "payment-signature",
        "x-payment-required",
        "payment-required",
    }
)

# Headers we strip from the *upstream* response before returning to client.
# `content-encoding` / `content-length` must be re-derived by httpx because
# upstream gzip is already decompressed by the time we read .content.
STRIP_RESPONSE_HEADERS = frozenset(
    {
        "connection",
        "transfer-encoding",
        "content-encoding",
        "content-length",
        "authorization",
        "proxy-authorization",
    }
)

router = APIRouter()


def get_registry(request: Request) -> ProviderRegistry:
    registry = getattr(request.app.state, "provider_registry", None)
    if not isinstance(registry, ProviderRegistry):
        raise RuntimeError("provider registry is not configured")
    return registry


def filter_request_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in STRIP_REQUEST_HEADERS
    }


def filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        key: value for key, value in headers.items() if key.lower() not in STRIP_RESPONSE_HEADERS
    }


def upstream_url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _request_params(request: Request) -> dict[str, str]:
    """Flat key->value dict used for metering variant selection."""
    return {k: v for k, v in request.query_params.items()}


def _payment_required_response(payment_required: PaymentRequired) -> JSONResponse:
    body = payment_required.model_dump(by_alias=True, exclude_none=True)
    response = JSONResponse(status_code=402, content=body)
    response.headers[PAYMENT_REQUIRED_HEADER] = encode_payment_required_header(payment_required)
    return response


async def _forward_upstream(
    entry: ProviderEntry, request: Request, target_path: str, *, body: bytes | None = None
) -> tuple[int, dict[str, str], bytes, httpx.Headers]:
    provider = entry.spec
    if not provider.routing.url:
        raise HTTPException(status_code=502, detail="provider has no upstream URL")

    target = upstream_url(provider.routing.url, target_path)
    headers = filter_request_headers(dict(request.headers))
    payload_body = body if body is not None else await request.body()

    timeout = httpx.Timeout(30.0, connect=5.0)
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
        upstream_request = client.build_request(
            request.method,
            target,
            params=request.query_params,
            headers=headers,
            content=payload_body,
        )
        auth = build_auth_strategy(provider.routing.auth)
        if auth is not None:
            await auth.apply(upstream_request)
        upstream_response = await client.send(upstream_request)

    return (
        upstream_response.status_code,
        filter_response_headers(upstream_response.headers),
        upstream_response.content,
        upstream_response.headers,
    )


@router.api_route(
    "/providers/{provider_name}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy(provider_name: str, path: str, request: Request) -> Response:
    registry = get_registry(request)
    entry = registry.get_entry(provider_name)
    if entry is None:
        raise HTTPException(status_code=404, detail="provider not found")

    endpoint = registry.match_endpoint(provider_name, request.method, path)
    if endpoint is None:
        # endpoints[] is an allowlist (gateway.md §2.2): unknown paths are 404
        raise HTTPException(status_code=404, detail="endpoint not in allowlist")

    request_params = _request_params(request)

    # Free endpoint -> respond mode short-circuit or proxy forward
    if endpoint.metering is None:
        if entry.spec.routing.type == "respond":
            return JSONResponse(content={"status": "ok", "endpoint": endpoint.path})
        status, headers, body, _ = await _forward_upstream(entry, request, endpoint.path)
        return FastAPIResponse(content=body, status_code=status, headers=headers)

    payment_header = request.headers.get(PAYMENT_SIGNATURE_HEADER)

    payment_required = build_payment_required(
        entry.spec, endpoint, request_params=request_params
    )
    if not payment_required.accepts:
        # metered but no currencies declared — treat as misconfiguration
        return JSONResponse(
            status_code=500,
            content={"error": "metered endpoint has no payment requirements; "
                              "check operator.currencies in provider.yml"},
        )

    if not payment_header:
        return _payment_required_response(payment_required)

    # Decode + structural-match
    try:
        payload = decode_payment_header(payment_header)
    except Exception as exc:  # pragma: no cover - hard to coerce in tests
        logger.warning("failed to decode PAYMENT-SIGNATURE: %s", exc)
        return JSONResponse(
            status_code=400,
            content={"isValid": False, "invalidReason": f"invalid_payment_payload: {exc}"},
        )

    requirement = match_requirement(payload, payment_required.accepts)
    if requirement is None:
        return JSONResponse(
            status_code=400,
            content={"isValid": False, "invalidReason": "payment_requirement_mismatch"},
        )

    # Hand off to facilitator
    verify_response = await entry.facilitator.verify(payload, requirement)
    if not verify_response.is_valid:
        return JSONResponse(
            status_code=400,
            content={
                "isValid": False,
                "invalidReason": verify_response.invalid_reason or "verify_failed",
            },
        )

    settle_response = await entry.facilitator.settle(payload, requirement)
    if not settle_response.success:
        return JSONResponse(
            status_code=500,
            content={
                "error": "settlement failed",
                "errorReason": settle_response.error_reason,
                "txHash": settle_response.transaction,
                "network": settle_response.network,
            },
        )

    # Forward upstream and attach PAYMENT-RESPONSE
    status, headers, body, _raw_headers = await _forward_upstream(
        entry, request, endpoint.path
    )
    headers[PAYMENT_RESPONSE_HEADER] = encode_payment_payload(
        settle_response.model_dump(by_alias=True)
    )
    return FastAPIResponse(content=body, status_code=status, headers=headers)
