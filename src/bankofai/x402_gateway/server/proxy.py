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
import time
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
from bankofai.x402_gateway.telemetry.logging import log_event
from bankofai.x402_gateway.telemetry.metrics import MetricsStore

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
        "accept-encoding",
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


def get_metrics(request: Request) -> MetricsStore | None:
    metrics = getattr(request.app.state, "metrics", None)
    return metrics if isinstance(metrics, MetricsStore) else None


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


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
    # The gateway returns a materialized FastAPI response, not a streaming byte-for-byte
    # tunnel. Ask upstreams for an uncompressed body so response bytes and headers stay
    # consistent even when httpx cannot decode a newer content encoding.
    headers["accept-encoding"] = "identity"
    payload_body = body if body is not None else await request.body()

    timeout = httpx.Timeout(30.0, connect=5.0)
    started = time.perf_counter()
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
        try:
            upstream_response = await client.send(upstream_request)
        except httpx.HTTPError as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            metrics = get_metrics(request)
            if metrics is not None:
                metrics.inc(
                    "x402_gateway_upstream_requests_total",
                    provider=provider.name,
                    method=request.method,
                    result="error",
                )
            log_event(
                logger,
                logging.ERROR,
                "gateway.upstream.failed",
                request_id=_request_id(request),
                provider=provider.name,
                method=request.method,
                path=target_path,
                duration_ms=duration_ms,
            )
            raise HTTPException(status_code=502, detail="upstream request failed") from exc

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    metrics = get_metrics(request)
    if metrics is not None:
        metrics.inc(
            "x402_gateway_upstream_requests_total",
            provider=provider.name,
            method=request.method,
            status_code=upstream_response.status_code,
            result="success",
        )
    log_event(
        logger,
        logging.INFO,
        "gateway.upstream.completed",
        request_id=_request_id(request),
        provider=provider.name,
        method=request.method,
        path=target_path,
        status_code=upstream_response.status_code,
        duration_ms=duration_ms,
    )

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
        log_event(
            logger,
            logging.WARNING,
            "gateway.proxy.provider_not_found",
            request_id=_request_id(request),
            provider=provider_name,
            method=request.method,
            path="/" + path.lstrip("/"),
        )
        raise HTTPException(status_code=404, detail="provider not found")

    endpoint = registry.match_endpoint(provider_name, request.method, path)
    if endpoint is None:
        # endpoints[] is an allowlist (gateway.md §2.2): unknown paths are 404
        log_event(
            logger,
            logging.INFO,
            "gateway.proxy.endpoint_not_allowed",
            request_id=_request_id(request),
            provider=provider_name,
            method=request.method,
            path="/" + path.lstrip("/"),
        )
        raise HTTPException(status_code=404, detail="endpoint not in allowlist")

    body_bytes = await request.body()
    request_params = _request_params(request, body_bytes)
    target_path = "/" + path.lstrip("/")
    metrics = get_metrics(request)

    # Free endpoint -> respond mode short-circuit or proxy forward
    if endpoint.metering is None:
        if entry.spec.routing.type == "respond":
            log_event(
                logger,
                logging.INFO,
                "gateway.proxy.responded",
                request_id=_request_id(request),
                provider=provider_name,
                method=request.method,
                path=target_path,
                metered=False,
            )
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
        log_event(
            logger,
            logging.ERROR,
            "gateway.payment.misconfigured",
            request_id=_request_id(request),
            provider=provider_name,
            method=request.method,
            path=target_path,
            reason="no_payment_requirements",
        )
        return JSONResponse(
            status_code=500,
            content={"error": "metered endpoint has no payment requirements; "
                              "check operator.currencies in provider.yml"},
        )

    if not payment_header:
        if metrics is not None:
            metrics.inc(
                "x402_gateway_payment_challenges_total",
                provider=provider_name,
                method=request.method,
                endpoint=endpoint.path,
            )
        log_event(
            logger,
            logging.INFO,
            "gateway.payment.challenge",
            request_id=_request_id(request),
            provider=provider_name,
            method=request.method,
            path=target_path,
            endpoint=endpoint.path,
        )
        return _payment_required_response(payment_required)

    # Decode + structural-match
    try:
        payload = decode_payment_header(payment_header)
    except Exception as exc:  # pragma: no cover - hard to coerce in tests
        if metrics is not None:
            metrics.inc(
                "x402_gateway_payment_verify_total",
                provider=provider_name,
                method=request.method,
                endpoint=endpoint.path,
                result="decode_failed",
            )
        log_event(
            logger,
            logging.WARNING,
            "gateway.payment.decode_failed",
            request_id=_request_id(request),
            provider=provider_name,
            method=request.method,
            path=target_path,
            endpoint=endpoint.path,
            reason=str(exc),
        )
        return JSONResponse(
            status_code=400,
            content={"isValid": False, "invalidReason": f"invalid_payment_payload: {exc}"},
        )

    requirement = match_requirement(payload, payment_required.accepts)
    if requirement is None:
        if metrics is not None:
            metrics.inc(
                "x402_gateway_payment_verify_total",
                provider=provider_name,
                method=request.method,
                endpoint=endpoint.path,
                result="requirement_mismatch",
            )
        log_event(
            logger,
            logging.INFO,
            "gateway.payment.requirement_mismatch",
            request_id=_request_id(request),
            provider=provider_name,
            method=request.method,
            path=target_path,
            endpoint=endpoint.path,
        )
        return JSONResponse(
            status_code=400,
            content={"isValid": False, "invalidReason": "payment_requirement_mismatch"},
        )

    # Hand off to facilitator
    verify_started = time.perf_counter()
    try:
        verify_response = await entry.facilitator.verify(payload, requirement)
    except Exception as exc:
        verify_duration_ms = round((time.perf_counter() - verify_started) * 1000, 2)
        if metrics is not None:
            metrics.inc(
                "x402_gateway_payment_verify_total",
                provider=provider_name,
                method=request.method,
                endpoint=endpoint.path,
                result="error",
            )
        log_event(
            logger,
            logging.ERROR,
            "gateway.payment.verify_error",
            request_id=_request_id(request),
            provider=provider_name,
            method=request.method,
            path=target_path,
            endpoint=endpoint.path,
            duration_ms=verify_duration_ms,
        )
        raise HTTPException(status_code=502, detail="facilitator verify failed") from exc
    verify_duration_ms = round((time.perf_counter() - verify_started) * 1000, 2)
    if not verify_response.is_valid:
        if metrics is not None:
            metrics.inc(
                "x402_gateway_payment_verify_total",
                provider=provider_name,
                method=request.method,
                endpoint=endpoint.path,
                result="invalid",
            )
        log_event(
            logger,
            logging.INFO,
            "gateway.payment.verify_failed",
            request_id=_request_id(request),
            provider=provider_name,
            method=request.method,
            path=target_path,
            endpoint=endpoint.path,
            reason=verify_response.invalid_reason or "verify_failed",
            duration_ms=verify_duration_ms,
        )
        return JSONResponse(
            status_code=400,
            content={
                "isValid": False,
                "invalidReason": verify_response.invalid_reason or "verify_failed",
            },
        )
    if metrics is not None:
        metrics.inc(
            "x402_gateway_payment_verify_total",
            provider=provider_name,
            method=request.method,
            endpoint=endpoint.path,
            result="valid",
        )
    log_event(
        logger,
        logging.INFO,
        "gateway.payment.verified",
        request_id=_request_id(request),
        provider=provider_name,
        method=request.method,
        path=target_path,
        endpoint=endpoint.path,
        network=requirement.network,
        asset=requirement.asset,
        duration_ms=verify_duration_ms,
    )

    settle_started = time.perf_counter()
    try:
        settle_response = await entry.facilitator.settle(payload, requirement)
    except Exception as exc:
        settle_duration_ms = round((time.perf_counter() - settle_started) * 1000, 2)
        if metrics is not None:
            metrics.inc(
                "x402_gateway_payment_settle_total",
                provider=provider_name,
                method=request.method,
                endpoint=endpoint.path,
                result="error",
            )
        log_event(
            logger,
            logging.ERROR,
            "gateway.payment.settle_error",
            request_id=_request_id(request),
            provider=provider_name,
            method=request.method,
            path=target_path,
            endpoint=endpoint.path,
            duration_ms=settle_duration_ms,
        )
        raise HTTPException(status_code=502, detail="facilitator settle failed") from exc
    settle_duration_ms = round((time.perf_counter() - settle_started) * 1000, 2)
    if not settle_response.success:
        if metrics is not None:
            metrics.inc(
                "x402_gateway_payment_settle_total",
                provider=provider_name,
                method=request.method,
                endpoint=endpoint.path,
                result="failed",
            )
        log_event(
            logger,
            logging.ERROR,
            "gateway.payment.settle_failed",
            request_id=_request_id(request),
            provider=provider_name,
            method=request.method,
            path=target_path,
            endpoint=endpoint.path,
            reason=settle_response.error_reason,
            network=settle_response.network,
            duration_ms=settle_duration_ms,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "settlement failed",
                "errorReason": settle_response.error_reason,
                "txHash": settle_response.transaction,
                "network": settle_response.network,
            },
        )
    if metrics is not None:
        metrics.inc(
            "x402_gateway_payment_settle_total",
            provider=provider_name,
            method=request.method,
            endpoint=endpoint.path,
            result="success",
        )
    log_event(
        logger,
        logging.INFO,
        "gateway.payment.settled",
        request_id=_request_id(request),
        provider=provider_name,
        method=request.method,
        path=target_path,
        endpoint=endpoint.path,
        network=settle_response.network,
        duration_ms=settle_duration_ms,
    )

    # Forward upstream and attach PAYMENT-RESPONSE
    status, headers, body, _raw_headers = await _forward_upstream(
        entry, request, target_path, body=body_bytes
    )
    headers[PAYMENT_RESPONSE_HEADER] = encode_payment_payload(
        settle_response.model_dump(by_alias=True)
    )
    return FastAPIResponse(content=body, status_code=status, headers=headers)
