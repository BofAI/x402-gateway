"""Shared fixtures for end-to-end smoke tests.

The smoke tier brings up real HTTP servers (mock facilitator + mock upstream
+ our gateway) and drives them with the real x402 client SDK. These tests
ARE slow (multi-second) and ARE network-touching (loopback), so they're
excluded from the default pytest run via the `smoke` marker.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request

REPO_ROOT = Path(__file__).resolve().parents[3].parent
# Make `e2e.mock_facilitator.app` importable without installing it.
sys.path.insert(0, str(REPO_ROOT / "e2e"))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerHandle:
    """A uvicorn instance running in a background thread."""

    def __init__(self, app: FastAPI, port: int) -> None:
        self.app = app
        self.port = port
        self._config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(
            target=lambda: asyncio.run(self._server.serve()), daemon=True
        )

    def start(self, ready_path: str = "/__402/health", timeout: float = 5.0) -> None:
        self._thread.start()
        deadline = time.time() + timeout
        url = f"http://127.0.0.1:{self.port}{ready_path}"
        while time.time() < deadline:
            try:
                if httpx.get(url, timeout=0.5).status_code < 500:
                    return
            except httpx.HTTPError:
                time.sleep(0.05)
        raise RuntimeError(f"server did not come up at {url}")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=3.0)


def make_upstream_app() -> tuple[FastAPI, list[dict[str, Any]]]:
    """Mock upstream that records every incoming request.

    Used to prove that the gateway:
      - stripped the client's `Authorization` / `PAYMENT-SIGNATURE` headers
      - injected the configured upstream auth header
      - forwarded the body / method / path unchanged
    """
    app = FastAPI()
    captures: list[dict[str, Any]] = []

    @app.get("/__ready", include_in_schema=False)
    async def _ready() -> dict[str, str]:
        # readiness probe used by the fixture; intentionally bypasses capture
        return {"status": "ready"}

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def catch_all(path: str, request: Request) -> dict[str, Any]:
        body = await request.body()
        captures.append(
            {
                "method": request.method,
                "path": "/" + path,
                "headers": {k.lower(): v for k, v in request.headers.items()},
                "query": dict(request.query_params),
                "body": body.decode("utf-8", errors="replace"),
            }
        )
        return {"upstream_ok": True, "echoed_path": path}

    return app, captures


def write_provider_yml(
    *,
    output_path: Path,
    facilitator_url: str,
    forward_url: str,
    recipient: str,
    auth_method: str = "header",
    auth_key: str = "Authorization",
    auth_prefix: str = "Bearer ",
    auth_env_var: str = "UPSTREAM_TOKEN",
) -> Path:
    """Write a minimal provider.yml for the smoke test."""
    yml = f"""\
name: smoke-api
title: "Smoke API"
description: "End-to-end smoke target"
category: data
version: v1

forward_url: {forward_url}

routing:
  type: proxy
  auth:
    method: {auth_method}
    key: {auth_key}
    prefix: "{auth_prefix}"
    value_from_env: {auth_env_var}

operator:
  network: bsc-testnet
  currencies:
    usd: ["USDT"]
  recipient: "{recipient}"
  scheme: exact
  facilitator_url: {facilitator_url}

endpoints:
  - method: GET
    path: /v1/data
    description: "Smoke target endpoint"
    metering:
      dimensions:
        - direction: usage
          unit: requests
          scale: 1
          tiers:
            - price_usd: 0.01

  - method: GET
    path: /health
"""
    output_path.write_text(yml)
    return output_path


@pytest.fixture
def smoke_environment(monkeypatch: pytest.MonkeyPatch):
    """Bring up mock facilitator + mock upstream + gateway; yield a context dict.

    Teardown stops all three uvicorn instances and restores monkeypatched env.
    """
    from mock_facilitator.app import create_app as create_mock_facilitator_app  # type: ignore  # noqa: I001

    facilitator_log = Path(tempfile.mkdtemp()) / "facilitator.log"
    monkeypatch.setenv("MOCK_FACILITATOR_LOG", str(facilitator_log))
    monkeypatch.setenv("UPSTREAM_TOKEN", "supersecret-upstream-token")

    facilitator_port = _free_port()
    upstream_port = _free_port()
    gateway_port = _free_port()

    fac_handle = ServerHandle(create_mock_facilitator_app(), facilitator_port)
    fac_handle.start(ready_path="/supported")

    upstream_app, upstream_captures = make_upstream_app()
    upstream_handle = ServerHandle(upstream_app, upstream_port)
    upstream_handle.start(ready_path="/__ready")

    workdir = Path(tempfile.mkdtemp())
    provider_yml = workdir / "provider.yml"
    write_provider_yml(
        output_path=provider_yml,
        facilitator_url=f"http://127.0.0.1:{facilitator_port}",
        forward_url=f"http://127.0.0.1:{upstream_port}",
        recipient="0x1234567890123456789012345678901234567890",
    )

    from bankofai.x402_gateway.server.startup import build_app

    gateway_app = build_app(
        provider_yml,
        sandbox=True,
        print_banners=False,
    )
    gateway_handle = ServerHandle(gateway_app, gateway_port)
    gateway_handle.start(ready_path="/__402/health")

    ctx = {
        "facilitator_port": facilitator_port,
        "upstream_port": upstream_port,
        "gateway_port": gateway_port,
        "facilitator_url": f"http://127.0.0.1:{facilitator_port}",
        "upstream_url": f"http://127.0.0.1:{upstream_port}",
        "gateway_url": f"http://127.0.0.1:{gateway_port}",
        "facilitator_log": facilitator_log,
        "upstream_captures": upstream_captures,
        "provider_yml": provider_yml,
    }
    try:
        yield ctx
    finally:
        gateway_handle.stop()
        upstream_handle.stop()
        fac_handle.stop()


def read_facilitator_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]
