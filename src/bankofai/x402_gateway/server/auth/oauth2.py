"""OAuth2 client-credentials auth strategy.

Exchanges client credentials at `token_url` for an access token, caches it
until `expires_in - 60s`, and injects `Authorization: Bearer <token>` on
upstream requests. Uses `asyncio.Lock` to prevent stampeding refreshes.

Expected RoutingAuthSpec.params:
  token_url: str                (required)
  client_id_env: str            (env name holding client_id; required)
  client_secret_env: str        (env name holding client_secret; required)
  scope: str                    (optional)
  audience: str                 (optional)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import httpx

from bankofai.x402_gateway.config.spec import RoutingAuthSpec

logger = logging.getLogger(__name__)


class OAuth2AuthStrategy:
    def __init__(self, spec: RoutingAuthSpec) -> None:
        self._spec = spec
        self._lock = asyncio.Lock()
        self._token: Optional[str] = None
        self._expires_at: float = 0.0

    async def apply(self, request: httpx.Request) -> None:
        token = await self._ensure_token()
        if token:
            request.headers[self._spec.key or "Authorization"] = (
                f"{self._spec.prefix or 'Bearer '}{token}"
            )

    async def _ensure_token(self) -> Optional[str]:
        if self._token and time.time() < self._expires_at:
            return self._token

        async with self._lock:
            # double-check after acquiring lock
            if self._token and time.time() < self._expires_at:
                return self._token
            return await self._refresh()

    async def _refresh(self) -> Optional[str]:
        params = self._spec.params
        token_url = params.get("token_url")
        client_id_env = params.get("client_id_env")
        client_secret_env = params.get("client_secret_env")
        if not token_url or not client_id_env or not client_secret_env:
            logger.warning(
                "oauth2 misconfigured: token_url=%s client_id_env=%s client_secret_env=%s",
                token_url,
                client_id_env,
                client_secret_env,
            )
            return None

        client_id = os.environ.get(str(client_id_env), "")
        client_secret = os.environ.get(str(client_secret_env), "")
        if not client_id or not client_secret:
            logger.warning("oauth2 client credentials not present in env")
            return None

        body = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        scope = params.get("scope")
        if scope:
            body["scope"] = str(scope)
        audience = params.get("audience")
        if audience:
            body["audience"] = str(audience)

        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
            response = await client.post(str(token_url), data=body)
            response.raise_for_status()
            payload = response.json()

        token = payload.get("access_token")
        if not isinstance(token, str):
            logger.warning("oauth2 token response missing access_token")
            return None

        expires_in = int(payload.get("expires_in", 3600))
        self._token = token
        # refresh 60s early to avoid races with upstream clock skew
        self._expires_at = time.time() + max(expires_in - 60, 1)
        return self._token
