"""FastAPI app factory."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

from bankofai.x402_gateway.server.admin import require_admin
from bankofai.x402_gateway.server.admin import router as admin_router
from bankofai.x402_gateway.server.proxy import router as proxy_router
from bankofai.x402_gateway.server.registry import ProviderRegistry
from bankofai.x402_gateway.telemetry.logging import log_event
from bankofai.x402_gateway.telemetry.metrics import MetricsStore

logger = logging.getLogger(__name__)


def create_app(registry: ProviderRegistry | None = None) -> FastAPI:
    app = FastAPI(title="x402-gateway")
    app.state.provider_registry = registry or ProviderRegistry()
    app.state.metrics = MetricsStore()
    app.state.admin_token = os.environ.get("X402_GATEWAY_ADMIN_TOKEN")
    app.state.admin_allow_public = (
        os.environ.get("X402_GATEWAY_ADMIN_ALLOW_PUBLIC", "").lower()
        in {"1", "true", "yes"}
    )

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics(request: Request) -> PlainTextResponse:
        await require_admin(request)
        store = app.state.metrics
        return PlainTextResponse(store.to_prometheus(), media_type="text/plain; version=0.0.4")

    @app.middleware("http")
    async def log_requests(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        client_host = request.client.host if request.client else None
        metrics = getattr(request.app.state, "metrics", None)
        try:
            response = await call_next(request)
        except Exception:
            duration_seconds = time.perf_counter() - started
            duration_ms = round(duration_seconds * 1000, 2)
            if isinstance(metrics, MetricsStore):
                metrics.record_http_request(
                    method=request.method,
                    path=_route_path(request),
                    status_code=500,
                    duration_seconds=duration_seconds,
                )
            log_event(
                logger,
                logging.ERROR,
                "gateway.request.failed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
                client_ip=client_host,
            )
            raise

        duration_seconds = time.perf_counter() - started
        duration_ms = round(duration_seconds * 1000, 2)
        response.headers.setdefault("x-request-id", request_id)
        if isinstance(metrics, MetricsStore):
            metrics.record_http_request(
                method=request.method,
                path=_route_path(request),
                status_code=response.status_code,
                duration_seconds=duration_seconds,
            )
        log_event(
            logger,
            logging.INFO,
            "gateway.request.completed",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            client_ip=client_host,
        )
        return response

    app.include_router(admin_router)
    app.include_router(proxy_router)
    return app


def _route_path(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else request.url.path
