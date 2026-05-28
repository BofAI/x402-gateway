"""Generic access-token DSL.

For upstreams that issue a token via a non-OAuth2 endpoint. The DSL is small:

  RoutingAuthSpec.params:
    fetch_url: str         (required)
    fetch_method: str      (default "POST")
    fetch_body: dict       (optional, sent as JSON body)
    fetch_body_from_env:   dict[field_name, env_var_name]   (overrides fetch_body)
    token_jsonpath: str    (default "access_token"; dotted path inside JSON response)
    refresh_seconds: int   (default 3600)

The token is injected on the upstream request as `<key>: <prefix><token>`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

import httpx

from bankofai.x402_gateway.config.spec import RoutingAuthSpec

logger = logging.getLogger(__name__)


def _jsonpath(value: Any, path: str) -> Any:
    cursor: Any = value
    for part in path.split("."):
        if isinstance(cursor, dict) and part in cursor:
            cursor = cursor[part]
        else:
            return None
    return cursor


class AccessTokenAuthStrategy:
    def __init__(self, spec: RoutingAuthSpec) -> None:
        self._spec = spec
        self._lock = asyncio.Lock()
        self._token: Optional[str] = None
        self._expires_at: float = 0.0

    async def apply(self, request: httpx.Request) -> None:
        token = await self._ensure_token()
        if token:
            request.headers[self._spec.key or "Authorization"] = f"{self._spec.prefix}{token}"

    async def _ensure_token(self) -> Optional[str]:
        if self._token and time.time() < self._expires_at:
            return self._token

        async with self._lock:
            if self._token and time.time() < self._expires_at:
                return self._token
            return await self._refresh()

    async def _refresh(self) -> Optional[str]:
        params = self._spec.params
        fetch_url = params.get("fetch_url")
        if not fetch_url:
            logger.warning("access_token strategy: fetch_url missing")
            return None

        fetch_method = str(params.get("fetch_method", "POST")).upper()
        body = dict(params.get("fetch_body") or {})
        env_overrides = params.get("fetch_body_from_env") or {}
        if isinstance(env_overrides, dict):
            for field, env_name in env_overrides.items():
                body[str(field)] = os.environ.get(str(env_name), "")
        token_path = str(params.get("token_jsonpath", "access_token"))
        refresh_seconds = int(params.get("refresh_seconds", 3600))

        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
            response = await client.request(fetch_method, str(fetch_url), json=body)
            response.raise_for_status()
            payload = response.json()

        token = _jsonpath(payload, token_path)
        if not isinstance(token, str) or not token:
            logger.warning("access_token strategy: %s not found in response", token_path)
            return None

        self._token = token
        self._expires_at = time.time() + max(refresh_seconds - 60, 1)
        return self._token
