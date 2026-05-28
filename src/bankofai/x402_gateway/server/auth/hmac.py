"""HMAC auth strategy.

Signs `<HTTP_METHOD>\n<canonical_path>\n<body_sha256_hex>` with HMAC-SHA256 keyed
by an env-stored secret. The signature is injected either as a header or a
query param. Canonical-path strips the query string by default (`include_query=False`)
to match the most common provider variant; flip it via `params.include_query: true`.

Expected RoutingAuthSpec.params:
  algorithm: "sha256" (default)
  in: "header" | "query"            (default "header")
  include_query: bool               (default False)
  body_hash_algo: "sha256" (default)
"""

from __future__ import annotations

import hashlib
import hmac

import httpx

from bankofai.x402_gateway.config.spec import RoutingAuthSpec
from bankofai.x402_gateway.server.auth.base import env_value


class HmacAuthStrategy:
    def __init__(self, spec: RoutingAuthSpec) -> None:
        self._spec = spec

    async def apply(self, request: httpx.Request) -> None:
        secret = env_value(self._spec.value_from_env)
        if not secret:
            return

        algo = str(self._spec.params.get("algorithm", "sha256")).lower()
        digestmod = getattr(hashlib, algo, hashlib.sha256)

        include_query = bool(self._spec.params.get("include_query", False))
        path = request.url.path
        if include_query and request.url.query:
            query = (
                request.url.query.decode()
                if isinstance(request.url.query, bytes)
                else str(request.url.query)
            )
            path = f"{path}?{query}"

        body_bytes = request.read() if request.content is not None else b""
        body_hash_hex = hashlib.sha256(body_bytes).hexdigest()

        canonical = "\n".join([request.method.upper(), path, body_hash_hex])
        signature = hmac.new(secret.encode(), canonical.encode(), digestmod).hexdigest()

        destination = str(self._spec.params.get("in", "header")).lower()
        if destination == "query":
            request.url = request.url.copy_merge_params({self._spec.key: signature})
        else:
            request.headers[self._spec.key] = f"{self._spec.prefix}{signature}"
