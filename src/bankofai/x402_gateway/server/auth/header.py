"""Header auth strategy: inject `<key>: <prefix><env_value>` on upstream requests."""

from __future__ import annotations

import httpx

from bankofai.x402_gateway.config.spec import RoutingAuthSpec
from bankofai.x402_gateway.server.auth.base import configured_value


class HeaderAuthStrategy:
    def __init__(self, spec: RoutingAuthSpec) -> None:
        self._spec = spec

    async def apply(self, request: httpx.Request) -> None:
        value = configured_value(self._spec.value, self._spec.value_from_env)
        if not value:
            return
        request.headers[self._spec.key] = f"{self._spec.prefix}{value}"
