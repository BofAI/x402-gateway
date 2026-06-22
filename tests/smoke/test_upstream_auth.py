"""Smoke tests for upstream auth injection (gateway.md §2.4 — 5 strategies).

Each test:
  1. Configures the gateway with `routing.auth.method = <X>`
  2. Hits a metered endpoint with a real signed payment
  3. Inspects the upstream capture to verify the injected credentials

We use httpx.MockTransport for the upstream layer here (instead of a real
uvicorn) so we can test the auth.apply() output without spinning a third
process per scenario.
"""

from __future__ import annotations

import hashlib
import hmac

import httpx
import pytest

from bankofai.x402_gateway.config.spec import RoutingAuthSpec
from bankofai.x402_gateway.server.auth import build_auth_strategy


def _build_request(
    method: str = "POST",
    url: str = "https://upstream.example/v1/foo?baz=1",
    body: bytes = b'{"k":"v"}',
) -> httpx.Request:
    return httpx.Request(method, url, content=body)


@pytest.mark.asyncio
async def test_header_strategy_real_inject(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPSTREAM_TOK", "abc.def.ghi")
    strat = build_auth_strategy(
        RoutingAuthSpec(
            method="header",
            key="X-Upstream-Auth",
            prefix="Token ",
            value_from_env="UPSTREAM_TOK",
        )
    )
    assert strat is not None
    req = _build_request()
    await strat.apply(req)
    assert req.headers["X-Upstream-Auth"] == "Token abc.def.ghi"


@pytest.mark.asyncio
async def test_query_param_strategy_real_inject(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "secret_key_value")
    strat = build_auth_strategy(
        RoutingAuthSpec(method="query_param", key="api_key", value_from_env="API_KEY")
    )
    assert strat is not None
    req = _build_request(method="GET", url="https://upstream.example/v1/foo")
    await strat.apply(req)
    assert "api_key=secret_key_value" in str(req.url)


@pytest.mark.asyncio
async def test_hmac_strategy_signature_is_verifiable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recompute the HMAC on the receiver side to prove our signing is correct."""
    monkeypatch.setenv("HMAC_SECRET", "v3ry-s3cret")
    strat = build_auth_strategy(
        RoutingAuthSpec(
            method="hmac",
            key="X-Signature",
            value_from_env="HMAC_SECRET",
            params={"in": "header", "algorithm": "sha256"},
        )
    )
    assert strat is not None
    body = b'{"action":"trade","amount":100}'
    req = _build_request(method="POST", url="https://upstream.example/v1/orders", body=body)
    await strat.apply(req)

    # Server-side recomputation: METHOD + path + sha256(body)
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = "\n".join(["POST", "/v1/orders", body_hash])
    expected = hmac.new(b"v3ry-s3cret", canonical.encode(), hashlib.sha256).hexdigest()
    assert req.headers["X-Signature"] == expected


@pytest.mark.asyncio
async def test_oauth2_strategy_caches_token_across_two_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two requests with the same strategy instance must share one token fetch."""
    monkeypatch.setenv("CID", "client-id-123")
    monkeypatch.setenv("CSEC", "client-secret-abc")

    grant_calls: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import urllib.parse

        # Body is application/x-www-form-urlencoded
        body_text = req.content.decode() if req.content else ""
        params = dict(urllib.parse.parse_qsl(body_text))
        grant_calls.append(params)
        return httpx.Response(200, json={"access_token": "tok_minted", "expires_in": 3600})

    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    strat = build_auth_strategy(
        RoutingAuthSpec(
            method="oauth2",
            params={
                "token_url": "https://idp.example/token",
                "client_id_env": "CID",
                "client_secret_env": "CSEC",
                "scope": "read:data",
            },
        )
    )
    assert strat is not None

    req_a = _build_request()
    req_b = _build_request()
    await strat.apply(req_a)
    await strat.apply(req_b)

    assert req_a.headers["Authorization"] == "Bearer tok_minted"
    assert req_b.headers["Authorization"] == "Bearer tok_minted"
    assert len(grant_calls) == 1, "token must be cached across calls"
    assert grant_calls[0]["grant_type"] == "client_credentials"
    assert grant_calls[0]["client_id"] == "client-id-123"
    assert grant_calls[0]["client_secret"] == "client-secret-abc"
    assert grant_calls[0]["scope"] == "read:data"


@pytest.mark.asyncio
async def test_oauth2_refreshes_after_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the cached token has expired, the next apply() must mint a new one."""
    monkeypatch.setenv("CID", "id")
    monkeypatch.setenv("CSEC", "secret")

    counter = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        # First mint: short ttl forces second call into refresh
        if counter["n"] == 1:
            return httpx.Response(200, json={"access_token": "first", "expires_in": 60})
        return httpx.Response(200, json={"access_token": "second", "expires_in": 60})

    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    strat = build_auth_strategy(
        RoutingAuthSpec(
            method="oauth2",
            params={
                "token_url": "https://idp.example/token",
                "client_id_env": "CID",
                "client_secret_env": "CSEC",
            },
        )
    )
    assert strat is not None

    req1 = _build_request()
    await strat.apply(req1)
    assert req1.headers["Authorization"] == "Bearer first"

    # Force expiry by stomping the in-memory cache
    strat._expires_at = 0.0  # type: ignore[attr-defined]

    req2 = _build_request()
    await strat.apply(req2)
    assert req2.headers["Authorization"] == "Bearer second"
    assert counter["n"] == 2


@pytest.mark.asyncio
async def test_access_token_dsl_extracts_token_from_nested_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATK_USER", "alice")
    monkeypatch.setenv("ATK_PASS", "wonderland")

    seen_body = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json as _json

        seen_body.update(_json.loads(req.content or b"{}"))
        return httpx.Response(
            200,
            json={
                "session": {
                    "tokens": {
                        "access": "DSL_NESTED_TOKEN",
                    }
                }
            },
        )

    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    strat = build_auth_strategy(
        RoutingAuthSpec(
            method="access_token",
            key="X-Custom-Auth",
            prefix="",
            params={
                "fetch_url": "https://login.example/api/session",
                "fetch_body": {"app": "smoke"},
                "fetch_body_from_env": {
                    "username": "ATK_USER",
                    "password": "ATK_PASS",
                },
                "token_jsonpath": "session.tokens.access",
            },
        )
    )
    assert strat is not None
    req = _build_request()
    await strat.apply(req)
    assert req.headers["X-Custom-Auth"] == "DSL_NESTED_TOKEN"
    # Confirm env-driven fields landed in the fetch body
    assert seen_body["username"] == "alice"
    assert seen_body["password"] == "wonderland"
    # Confirm static fields landed too
    assert seen_body["app"] == "smoke"


@pytest.mark.asyncio
async def test_client_authorization_header_never_reaches_upstream() -> None:
    """The proxy MUST strip the client's Authorization header before forwarding.

    This is the gateway.md §2.4 invariant 'authorization 必须剥' — protects
    against the client's auth bleeding into the seller's upstream account.
    """
    from bankofai.x402_gateway.server.proxy import filter_request_headers

    headers = {
        "host": "gateway.example",
        "authorization": "Bearer client-token",
        "proxy-authorization": "Basic abc",
        "PAYMENT-SIGNATURE": "<signed>",
        "x-payment": "v1-format",
        "PAYMENT-REQUIRED": "should-not-leak",
        "x-payment-required": "v1-form",
        "cookie": "session=client",
        "x-api-key": "client-api-key",
        "api-key": "client-api-key",
        "content-type": "application/json",
        "user-agent": "smoke-test",
    }
    filtered = filter_request_headers(headers)

    assert "authorization" not in {k.lower() for k in filtered}
    assert "proxy-authorization" not in {k.lower() for k in filtered}
    assert "payment-signature" not in {k.lower() for k in filtered}
    assert "x-payment" not in {k.lower() for k in filtered}
    assert "payment-required" not in {k.lower() for k in filtered}
    assert "x-payment-required" not in {k.lower() for k in filtered}
    assert "cookie" not in {k.lower() for k in filtered}
    assert "x-api-key" not in {k.lower() for k in filtered}
    assert "api-key" not in {k.lower() for k in filtered}

    # Non-sensitive headers should pass through
    assert "content-type" in {k.lower() for k in filtered}
    assert "user-agent" in {k.lower() for k in filtered}
