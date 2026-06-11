"""Query-parameter auth strategy: inject `?<key>=<env_value>` on upstream requests."""

from __future__ import annotations

import httpx

from bankofai.x402_gateway.config.spec import RoutingAuthSpec
from bankofai.x402_gateway.server.auth.base import configured_value


class QueryParamAuthStrategy:
    def __init__(self, spec: RoutingAuthSpec) -> None:
        self._spec = spec

    async def apply(self, request: httpx.Request) -> None:
        value = configured_value(self._spec.value, self._spec.value_from_env)
        if not value:
            return
        request.url = request.url.copy_merge_params({self._spec.key: value})
