from __future__ import annotations

import hashlib
import hmac

import httpx
import pytest

from bankofai.x402_gateway.config.spec import RoutingAuthSpec
from bankofai.x402_gateway.server.auth import build_auth_strategy
from bankofai.x402_gateway.server.auth.access_token import AccessTokenAuthStrategy
from bankofai.x402_gateway.server.auth.hmac import HmacAuthStrategy
from bankofai.x402_gateway.server.auth.oauth2 import OAuth2AuthStrategy


def _request(method: str = "POST", body: bytes = b'{"k":"v"}') -> httpx.Request:
    return httpx.Request(method, "https://upstream.example/v1/foo?x=1", content=body)


@pytest.mark.asyncio
async def test_header_strategy_injects_value_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TOKEN", "tok123")
    spec = RoutingAuthSpec(
        method="header",
        key="Authorization",
        prefix="Bearer ",
        value_from_env="MY_TOKEN",
    )
    strat = build_auth_strategy(spec)
    assert strat is not None
    req = _request()
    await strat.apply(req)
    assert req.headers["Authorization"] == "Bearer tok123"


@pytest.mark.asyncio
async def test_query_param_strategy_appends_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACME_KEY", "abc")
    spec = RoutingAuthSpec(method="query_param", key="apikey", value_from_env="ACME_KEY")
    strat = build_auth_strategy(spec)
    assert strat is not None
    req = _request("GET")
    await strat.apply(req)
    assert "apikey=abc" in str(req.url)


@pytest.mark.asyncio
async def test_hmac_strategy_signs_body_and_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HMAC_SECRET", "supersecret")
    spec = RoutingAuthSpec(
        method="hmac",
        key="X-Signature",
        value_from_env="HMAC_SECRET",
        params={"in": "header"},
    )
    strat = HmacAuthStrategy(spec)
    body = b'{"hello":"world"}'
    req = _request(body=body)
    await strat.apply(req)

    expected_body_hash = hashlib.sha256(body).hexdigest()
    canonical = "\n".join(["POST", "/v1/foo", expected_body_hash])
    expected = hmac.new(b"supersecret", canonical.encode(), hashlib.sha256).hexdigest()
    assert req.headers["X-Signature"] == expected


@pytest.mark.asyncio
async def test_oauth2_caches_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CID", "id")
    monkeypatch.setenv("CSECRET", "secret")

    call_count = {"n": 0}

    def token_handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"access_token": "tok_xyz", "expires_in": 3600})

    transport = httpx.MockTransport(token_handler)

    # patch the AsyncClient used in oauth2 to use our transport
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    spec = RoutingAuthSpec(
        method="oauth2",
        params={
            "token_url": "https://idp.example/token",
            "client_id_env": "CID",
            "client_secret_env": "CSECRET",
        },
    )
    strat = OAuth2AuthStrategy(spec)

    req1 = _request()
    await strat.apply(req1)
    req2 = _request()
    await strat.apply(req2)

    assert req1.headers["Authorization"] == "Bearer tok_xyz"
    assert req2.headers["Authorization"] == "Bearer tok_xyz"
    assert call_count["n"] == 1  # cached on second call


@pytest.mark.asyncio
async def test_access_token_dsl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATK_PASS", "pw")

    def token_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"token": "DSL_TOKEN"}})

    transport = httpx.MockTransport(token_handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    spec = RoutingAuthSpec(
        method="access_token",
        key="X-Auth-Token",
        prefix="",
        params={
            "fetch_url": "https://login.example/api",
            "fetch_body": {"username": "alice"},
            "fetch_body_from_env": {"password": "ATK_PASS"},
            "token_jsonpath": "data.token",
            "refresh_seconds": 600,
        },
    )
    strat = AccessTokenAuthStrategy(spec)
    req = _request()
    await strat.apply(req)
    assert req.headers["X-Auth-Token"] == "DSL_TOKEN"
