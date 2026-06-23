"""Upstream auth strategies."""

from __future__ import annotations

from bankofai.x402_gateway.config.spec import RoutingAuthSpec
from bankofai.x402_gateway.server.auth.access_token import AccessTokenAuthStrategy
from bankofai.x402_gateway.server.auth.base import AuthStrategy
from bankofai.x402_gateway.server.auth.header import HeaderAuthStrategy
from bankofai.x402_gateway.server.auth.hmac import HmacAuthStrategy
from bankofai.x402_gateway.server.auth.oauth2 import OAuth2AuthStrategy
from bankofai.x402_gateway.server.auth.query_param import QueryParamAuthStrategy


def build_auth_strategy(spec: RoutingAuthSpec | None) -> AuthStrategy | None:
    if spec is None:
        return None
    if spec.method == "header":
        return HeaderAuthStrategy(spec)
    if spec.method == "query_param":
        return QueryParamAuthStrategy(spec)
    if spec.method == "hmac":
        return HmacAuthStrategy(spec)
    if spec.method == "oauth2":
        return OAuth2AuthStrategy(spec)
    if spec.method == "access_token":
        return AccessTokenAuthStrategy(spec)
    raise ValueError(f"unsupported auth method: {spec.method}")


__all__ = [
    "AccessTokenAuthStrategy",
    "AuthStrategy",
    "HeaderAuthStrategy",
    "HmacAuthStrategy",
    "OAuth2AuthStrategy",
    "QueryParamAuthStrategy",
    "build_auth_strategy",
]
