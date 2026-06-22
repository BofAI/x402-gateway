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

import json
import logging
from urllib.parse import parse_qsl, urljoin

import httpx
from bankofai.x402.encoding import encode_payment_payload
from bankofai.x402.types import PaymentRequired, PaymentRequirementsExtra
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
        "cookie",
        "x-api-key",
        "api-key",
        "apikey",
        "x-auth-token",
        "x-access-token",
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


def _first_forwarded_ip(value: str | None) -> str | None:
    if not value:
        return None
    for part in value.split(","):
        candidate = part.strip()
        if candidate:
            return candidate
    return None


def _client_ip_for_upstream(request: Request) -> tuple[str | None, bool]:
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip(), True

    forwarded_ip = _first_forwarded_ip(request.headers.get("x-forwarded-for"))
    if forwarded_ip:
        return forwarded_ip, False

    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip(), False

    if request.client and request.client.host:
        return request.client.host, False
    return None, False


def add_client_ip_headers(headers: dict[str, str], request: Request) -> dict[str, str]:
    client_ip, from_cloudflare = _client_ip_for_upstream(request)
    if not client_ip:
        return headers

    headers["x-real-ip"] = client_ip
    headers["x-client-ip"] = client_ip

    existing_xff = request.headers.get("x-forwarded-for")
    if from_cloudflare:
        headers["x-forwarded-for"] = client_ip
    elif existing_xff:
        headers["x-forwarded-for"] = existing_xff
    else:
        headers["x-forwarded-for"] = client_ip
    return headers


def filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        key: value for key, value in headers.items() if key.lower() not in STRIP_RESPONSE_HEADERS
    }


def upstream_url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _scalar_params_from_json(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    params: dict[str, str] = {}
    for key, item in value.items():
        if isinstance(item, str | int | float | bool):
            params[str(key)] = str(item)
    return params


def _request_params(request: Request, body: bytes = b"") -> dict[str, str]:
    """Flat key->value dict used for metering variant selection."""
    params = {k: v for k, v in request.query_params.items()}
    if not body:
        return params

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    try:
        if content_type == "application/json":
            params.update(_scalar_params_from_json(json.loads(body.decode("utf-8"))))
        elif content_type == "application/x-www-form-urlencoded":
            form_params = parse_qsl(body.decode("utf-8"), keep_blank_values=True)
            params.update({k: v for k, v in form_params})
    except (UnicodeDecodeError, ValueError, TypeError):
        logger.debug("could not parse request body for metering variant selection", exc_info=True)
    return params


def _payment_required_response(payment_required: PaymentRequired) -> JSONResponse:
    body = payment_required.model_dump(by_alias=True, exclude_none=True)
    response = JSONResponse(status_code=402, content=body)
    response.headers[PAYMENT_REQUIRED_HEADER] = encode_payment_required_header(payment_required)
    return response


async def _attach_fee_quotes(entry: ProviderEntry, payment_required: PaymentRequired) -> None:
    """Attach facilitator fee quotes to PaymentRequirements before the client signs.

    Real facilitators verify feeTo/feeAmount as part of exact_permit. Returning
    a 402 without `accepts[].extra.fee` lets the client sign a zero-fee permit,
    which the facilitator correctly rejects during verify.
    """
    if not payment_required.accepts:
        return

    context = None
    if payment_required.extensions and payment_required.extensions.payment_permit_context:
        context = payment_required.extensions.payment_permit_context.model_dump(
            by_alias=True,
            exclude_none=True,
        )

    try:
        quotes = await entry.facilitator.fee_quote(payment_required.accepts, context)
    except Exception as exc:
        logger.warning("fee quote failed for provider %s: %s", entry.spec.name, exc)
        return

    quote_by_key = {
        (quote.scheme, quote.network, quote.asset.lower()): quote
        for quote in quotes
    }
    updated = []
    for requirement in payment_required.accepts:
        quote = quote_by_key.get(
            (requirement.scheme, requirement.network, requirement.asset.lower())
        )
        if quote is None:
            updated.append(requirement)
            continue
        extra = requirement.extra or PaymentRequirementsExtra()
        updated.append(
            requirement.model_copy(
                update={
                    "extra": extra.model_copy(update={"fee": quote.fee}),
                }
            )
        )
    payment_required.accepts = updated


async def _forward_upstream(
    entry: ProviderEntry, request: Request, target_path: str, *, body: bytes | None = None
) -> tuple[int, dict[str, str], bytes, httpx.Headers]:
    provider = entry.spec
    if not provider.routing.url:
        raise HTTPException(status_code=502, detail="provider has no upstream URL")

    target = upstream_url(provider.routing.url, target_path)
    headers = filter_request_headers(dict(request.headers))
    headers = add_client_ip_headers(headers, request)
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

    body_bytes = await request.body()
    request_params = _request_params(request, body_bytes)
    target_path = "/" + path.lstrip("/")

    # Free endpoint -> respond mode short-circuit or proxy forward
    if endpoint.metering is None:
        if entry.spec.routing.type == "respond":
            return JSONResponse(content={"status": "ok", "endpoint": endpoint.path})
        status, headers, body, _ = await _forward_upstream(
            entry, request, target_path, body=body_bytes
        )
        return FastAPIResponse(content=body, status_code=status, headers=headers)

    payment_header = request.headers.get(PAYMENT_SIGNATURE_HEADER)

    payment_required = build_payment_required(
        entry.spec, endpoint, request_params=request_params
    )
    await _attach_fee_quotes(entry, payment_required)
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
        entry, request, target_path, body=body_bytes
    )
    headers[PAYMENT_RESPONSE_HEADER] = encode_payment_payload(
        settle_response.model_dump(by_alias=True)
    )
    return FastAPIResponse(content=body, status_code=status, headers=headers)
