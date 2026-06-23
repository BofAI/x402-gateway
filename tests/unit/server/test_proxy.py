"""Integration tests for the proxy router (gateway + upstream + facilitator)."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest
from bankofai.x402.encoding import encode_payment_payload
from bankofai.x402.types import (
    FeeInfo,
    FeeQuoteResponse,
    PaymentPayload,
    PaymentPayloadData,
    SettleResponse,
    SupportedResponse,
    VerifyResponse,
)
from fastapi.testclient import TestClient

from bankofai.x402_gateway.config.loader import load_provider_file
from bankofai.x402_gateway.facilitator.client import FacilitatorAPI
from bankofai.x402_gateway.server.app import create_app
from bankofai.x402_gateway.server.payment import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    build_payment_requirements,
)
from bankofai.x402_gateway.server.registry import ProviderRegistry


class _FakeFacilitator:
    """Records every call and returns scripted responses."""

    def __init__(
        self,
        *,
        verify_result: bool = True,
        settle_result: bool = True,
        tx_hash: str = "0xdeadbeef",
        fee_to: str | None = None,
        fee_amount: str = "0",
        raise_verify: bool = False,
        raise_settle: bool = False,
    ) -> None:
        self.fee_quote_calls: list = []
        self.verify_calls: list = []
        self.settle_calls: list = []
        self._verify_result = verify_result
        self._settle_result = settle_result
        self._tx_hash = tx_hash
        self._fee_to = fee_to
        self._fee_amount = fee_amount
        self._raise_verify = raise_verify
        self._raise_settle = raise_settle

    async def supported(self) -> SupportedResponse:
        return SupportedResponse(kinds=[])

    async def fee_quote(self, accepts, context=None) -> list[FeeQuoteResponse]:
        self.fee_quote_calls.append((accepts, context))
        if self._fee_to is None:
            return []
        requirement = accepts[0]
        return [
            FeeQuoteResponse(
                scheme=requirement.scheme,
                network=requirement.network,
                asset=requirement.asset,
                pricing="fixed",
                fee=FeeInfo(
                    feeTo=self._fee_to,
                    feeAmount=self._fee_amount,
                    caller=self._fee_to,
                ),
            )
        ]

    async def verify(self, payload, requirements) -> VerifyResponse:
        self.verify_calls.append((payload, requirements))
        if self._raise_verify:
            raise RuntimeError("verify unavailable")
        if self._verify_result:
            return VerifyResponse(isValid=True)
        return VerifyResponse(isValid=False, invalidReason="forced_fail")

    async def settle(self, payload, requirements) -> SettleResponse:
        self.settle_calls.append((payload, requirements))
        if self._raise_settle:
            raise RuntimeError("settle unavailable")
        if self._settle_result:
            return SettleResponse(
                success=True, transaction=self._tx_hash, network=requirements.network
            )
        return SettleResponse(success=False, errorReason="forced_fail")


def _patch_provider_facilitator(
    registry: ProviderRegistry, name: str, facilitator: FacilitatorAPI
) -> None:
    entry = registry.get_entry(name)
    assert entry is not None
    entry.facilitator = facilitator


def _build_test_client(
    provider_yml_path,
    facilitator: FacilitatorAPI | None = None,
    *,
    transport: httpx.MockTransport | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> tuple[TestClient, ProviderRegistry, _FakeFacilitator]:
    registry = ProviderRegistry()
    app = create_app(registry)

    async def setup() -> None:
        spec = load_provider_file(provider_yml_path)
        await registry.replace_all([spec])

    asyncio.run(setup())

    fac = facilitator or _FakeFacilitator()
    _patch_provider_facilitator(registry, "acme-weather", fac)

    if transport is not None:
        assert monkeypatch is not None
        # monkey-patch httpx.AsyncClient default to inject the upstream mock
        original_init = httpx.AsyncClient.__init__

        def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs.setdefault("transport", transport)
            return original_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    return TestClient(app), registry, fac  # type: ignore[return-value]


def test_management_endpoints(provider_yml_path) -> None:
    client, _, _ = _build_test_client(provider_yml_path)
    try:
        assert client.get("/__402/health").json() == "ok"
        ready = client.get("/__402/ready")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        metrics = client.get("/__402/metrics")
        assert metrics.status_code == 200
        assert "x402_gateway_http_requests_total" in metrics.text
        default_metrics = client.get("/metrics")
        assert default_metrics.status_code == 200
        assert "x402_gateway_http_requests_total" in default_metrics.text
        providers = client.get("/__402/providers").json()
        assert providers[0]["name"] == "acme-weather"

        endpoints = client.get("/__402/endpoints").json()
        assert endpoints[0]["gatewayPath"] == "/providers/acme-weather/v1/current"
        assert endpoints[0]["metered"] is True
        assert endpoints[1]["metered"] is False
    finally:
        client.close()


def test_management_endpoints_require_admin_token_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("X402_GATEWAY_ADMIN_TOKEN", "admin-secret")
    client = TestClient(create_app(ProviderRegistry()))
    try:
        assert client.get("/__402/health").status_code == 200
        assert client.get("/__402/ready").status_code == 503

        for path in ("/__402/providers", "/__402/endpoints", "/__402/metrics", "/metrics"):
            response = client.get(path)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"

        assert (
            client.get(
                "/__402/providers",
                headers={"Authorization": "Bearer admin-secret"},
            ).status_code
            == 200
        )
        assert (
            client.get("/metrics", headers={"X-Admin-Token": "admin-secret"}).status_code
            == 200
        )
    finally:
        client.close()


def test_management_endpoints_reject_public_clients_without_token() -> None:
    client = TestClient(create_app(ProviderRegistry()))
    try:
        response = client.get(
            "/__402/providers",
            headers={"X-Forwarded-For": "8.8.8.8"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "admin endpoint is not exposed publicly"
    finally:
        client.close()


def test_readiness_fails_when_no_providers_loaded() -> None:
    client = TestClient(create_app(ProviderRegistry()))
    try:
        response = client.get("/__402/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert body["issues"][0]["reason"] == "no providers loaded"
    finally:
        client.close()


def test_readiness_fails_when_facilitator_unreachable(provider_yml_path) -> None:
    registry = ProviderRegistry()

    async def setup() -> None:
        spec = load_provider_file(provider_yml_path)
        await registry.replace_all(
            [spec],
            payment_statuses={spec.name: "unreachable"},
        )

    asyncio.run(setup())
    client = TestClient(create_app(registry))
    try:
        response = client.get("/__402/ready")
        assert response.status_code == 503
        assert response.json()["issues"][0]["reason"] == "facilitator unreachable"
    finally:
        client.close()


def test_catalog_endpoints_expose_public_payloads(provider_yml_path) -> None:
    client, _, _ = _build_test_client(provider_yml_path)
    try:
        catalog = client.get("/__402/catalog").json()
        assert catalog["provider_count"] == 1
        assert catalog["providers"][0]["fqn"] == "acme-weather"
        assert "routing" not in catalog["providers"][0]

        detail = client.get("/__402/catalog/providers/acme-weather.json").json()
        assert detail["service_url"].endswith("/providers/acme-weather")
        assert detail["endpoints"][0]["url"].endswith(
            "/providers/acme-weather/v1/current"
        )
        assert detail["status"]["gateway"] == "loaded"

        pay = client.get("/__402/catalog/pay/acme-weather.json").json()
        assert pay["provider"]["name"] == "acme-weather"
        assert pay["paidEndpoints"][0]["path"] == "/v1/current"
        assert "routing" not in pay
    finally:
        client.close()


def test_metered_endpoint_returns_402_when_unpaid(provider_yml_path) -> None:
    client, _, _ = _build_test_client(provider_yml_path)
    try:
        response = client.get("/providers/acme-weather/v1/current")
        assert response.status_code == 402

        body = response.json()
        assert body["accepts"][0]["network"] == "tron:mainnet"

        header = response.headers.get(PAYMENT_REQUIRED_HEADER)
        assert header is not None
        decoded = json.loads(base64.b64decode(header).decode())
        assert decoded["x402Version"] == 2

        metrics = client.get("/__402/metrics").text
        assert (
            'x402_gateway_payment_challenges_total{endpoint="/v1/current",'
            'method="GET",provider="acme-weather"} 1'
        ) in metrics
    finally:
        client.close()


def test_metered_endpoint_attaches_facilitator_fee_quote(provider_yml_path) -> None:
    facilitator = _FakeFacilitator(fee_to="TFeeCollector", fee_amount="123")
    client, _, fac = _build_test_client(provider_yml_path, facilitator=facilitator)
    try:
        response = client.get("/providers/acme-weather/v1/current")
        assert response.status_code == 402
        assert len(fac.fee_quote_calls) == 1

        body = response.json()
        fee = body["accepts"][0]["extra"]["fee"]
        assert fee["feeTo"] == "TFeeCollector"
        assert fee["feeAmount"] == "123"

        header = response.headers.get(PAYMENT_REQUIRED_HEADER)
        assert header is not None
        decoded = json.loads(base64.b64decode(header).decode())
        assert decoded["accepts"][0]["extra"]["fee"]["feeTo"] == "TFeeCollector"
    finally:
        client.close()


def test_metered_variant_can_match_json_body_param(
    provider_yml_path, tmp_path
) -> None:
    provider_text = provider_yml_path.read_text()
    provider_text += """
  - method: POST
    path: /v1/chat
    metering:
      dimensions:
        - unit: requests
          tiers:
            - price_usd: 0.01
      variants:
        - param: model
          value: pro
          dimensions:
            - unit: requests
              tiers:
                - price_usd: 0.10
"""
    provider_path = tmp_path / "provider.yml"
    provider_path.write_text(provider_text)
    client, _, _ = _build_test_client(provider_path)
    try:
        response = client.post(
            "/providers/acme-weather/v1/chat",
            json={"model": "pro", "messages": []},
        )
        assert response.status_code == 402
        assert response.json()["accepts"][0]["amount"] == "100000"
    finally:
        client.close()


def test_unknown_path_returns_404(provider_yml_path) -> None:
    client, _, _ = _build_test_client(provider_yml_path)
    try:
        response = client.get("/providers/acme-weather/favicon.ico")
        assert response.status_code == 404
        assert response.json()["detail"] == "endpoint not in allowlist"
    finally:
        client.close()


def test_free_endpoint_proxies_upstream(
    provider_yml_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hit the /health endpoint: no metering, should forward to upstream."""

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        # We do not require the URL match exactly; just simulate upstream 200.
        return httpx.Response(200, json={"healthy": True})

    transport = httpx.MockTransport(upstream_handler)
    client, _, _ = _build_test_client(
        provider_yml_path, transport=transport, monkeypatch=monkeypatch
    )
    try:
        response = client.get("/providers/acme-weather/health")
        assert response.status_code == 200
        assert response.json() == {"healthy": True}
    finally:
        client.close()


def test_upstream_exception_returns_502(
    provider_yml_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def upstream_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("upstream down", request=request)

    transport = httpx.MockTransport(upstream_handler)
    client, _, _ = _build_test_client(
        provider_yml_path, transport=transport, monkeypatch=monkeypatch
    )
    try:
        response = client.get("/providers/acme-weather/health")
        assert response.status_code == 502
        assert response.json()["detail"] == "upstream request failed"
        metrics = client.get("/__402/metrics").text
        assert (
            'x402_gateway_upstream_requests_total{method="GET",'
            'provider="acme-weather",result="error"} 1'
        ) in metrics
    finally:
        client.close()


def test_template_endpoint_forwards_requested_path(
    provider_yml_path, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream_calls: list[httpx.Request] = []

    provider_text = provider_yml_path.read_text()
    provider_text += """
  - method: GET
    path: /v1/quotation/{symbol}
"""
    provider_path = tmp_path / "provider.yml"
    provider_path.write_text(provider_text)

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        upstream_calls.append(request)
        return httpx.Response(200, json={"symbol": "BTC"})

    transport = httpx.MockTransport(upstream_handler)
    client, _, _ = _build_test_client(
        provider_path, transport=transport, monkeypatch=monkeypatch
    )
    try:
        response = client.get("/providers/acme-weather/v1/quotation/BTC")
        assert response.status_code == 200
        assert len(upstream_calls) == 1
        assert upstream_calls[0].url.path == "/v1/quotation/BTC"
    finally:
        client.close()


def test_proxy_forwards_cloudflare_client_ip(
    provider_yml_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream_calls: list[httpx.Request] = []

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        upstream_calls.append(request)
        return httpx.Response(200, json={"healthy": True})

    transport = httpx.MockTransport(upstream_handler)
    client, _, _ = _build_test_client(
        provider_yml_path, transport=transport, monkeypatch=monkeypatch
    )
    try:
        response = client.get(
            "/providers/acme-weather/health",
            headers={
                "CF-Connecting-IP": "203.0.113.10",
                "X-Forwarded-For": "198.51.100.20",
                "X-Real-IP": "198.51.100.30",
            },
        )
        assert response.status_code == 200
        assert len(upstream_calls) == 1
        headers = upstream_calls[0].headers
        assert headers["x-real-ip"] == "203.0.113.10"
        assert headers["x-client-ip"] == "203.0.113.10"
        assert headers["x-forwarded-for"] == "203.0.113.10"
    finally:
        client.close()


def test_proxy_requests_identity_encoding_from_upstream(
    provider_yml_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream_calls: list[httpx.Request] = []

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        upstream_calls.append(request)
        return httpx.Response(200, json={"healthy": True})

    transport = httpx.MockTransport(upstream_handler)
    client, _, _ = _build_test_client(
        provider_yml_path, transport=transport, monkeypatch=monkeypatch
    )
    try:
        response = client.get(
            "/providers/acme-weather/health",
            headers={"Accept-Encoding": "gzip, deflate, br"},
        )
        assert response.status_code == 200
        assert len(upstream_calls) == 1
        assert upstream_calls[0].headers["accept-encoding"] == "identity"
    finally:
        client.close()


def test_proxy_forwards_x_forwarded_for_without_cloudflare(
    provider_yml_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream_calls: list[httpx.Request] = []

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        upstream_calls.append(request)
        return httpx.Response(200, json={"healthy": True})

    transport = httpx.MockTransport(upstream_handler)
    client, _, _ = _build_test_client(
        provider_yml_path, transport=transport, monkeypatch=monkeypatch
    )
    try:
        response = client.get(
            "/providers/acme-weather/health",
            headers={"X-Forwarded-For": "198.51.100.20, 198.51.100.21"},
        )
        assert response.status_code == 200
        assert len(upstream_calls) == 1
        headers = upstream_calls[0].headers
        assert headers["x-real-ip"] == "198.51.100.20"
        assert headers["x-client-ip"] == "198.51.100.20"
        assert headers["x-forwarded-for"] == "198.51.100.20, 198.51.100.21"
    finally:
        client.close()


def test_metered_endpoint_settles_and_forwards(
    provider_yml_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Send a valid PAYMENT-SIGNATURE -> facilitator verify+settle -> upstream call."""

    upstream_calls: list[httpx.Request] = []

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        upstream_calls.append(request)
        return httpx.Response(200, json={"current": "sunny"})

    transport = httpx.MockTransport(upstream_handler)
    facilitator = _FakeFacilitator()
    client, _, fac = _build_test_client(
        provider_yml_path,
        facilitator=facilitator,
        transport=transport,
        monkeypatch=monkeypatch,
    )

    try:
        # build a payload matching the gateway's requirements
        from bankofai.x402_gateway.config.loader import load_provider_file

        spec = load_provider_file(provider_yml_path)
        _, requirements = build_payment_requirements(spec, spec.endpoints[0])

        payload = PaymentPayload(
            x402Version=2,
            accepted=requirements[0],
            payload=PaymentPayloadData(signature="0xdeadbeef"),
        )
        header = encode_payment_payload(payload)

        response = client.get(
            "/providers/acme-weather/v1/current",
            headers={PAYMENT_SIGNATURE_HEADER: header},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"current": "sunny"}
        assert response.headers.get(PAYMENT_RESPONSE_HEADER) is not None

        assert len(fac.verify_calls) == 1
        assert len(fac.settle_calls) == 1
        assert len(upstream_calls) == 1
    finally:
        client.close()


def test_metered_endpoint_400s_when_verify_fails(
    provider_yml_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facilitator = _FakeFacilitator(verify_result=False)

    upstream_calls: list[httpx.Request] = []

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        upstream_calls.append(request)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(upstream_handler)
    client, _, fac = _build_test_client(
        provider_yml_path,
        facilitator=facilitator,
        transport=transport,
        monkeypatch=monkeypatch,
    )

    try:
        from bankofai.x402_gateway.config.loader import load_provider_file

        spec = load_provider_file(provider_yml_path)
        _, requirements = build_payment_requirements(spec, spec.endpoints[0])

        payload = PaymentPayload(
            x402Version=2,
            accepted=requirements[0],
            payload=PaymentPayloadData(signature="0xdeadbeef"),
        )
        header = encode_payment_payload(payload)

        response = client.get(
            "/providers/acme-weather/v1/current",
            headers={PAYMENT_SIGNATURE_HEADER: header},
        )

        assert response.status_code == 400
        assert response.json()["invalidReason"] == "forced_fail"
        assert len(fac.settle_calls) == 0
        assert len(upstream_calls) == 0
    finally:
        client.close()


def test_metered_endpoint_502s_when_verify_raises(
    provider_yml_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facilitator = _FakeFacilitator(raise_verify=True)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    client, _, _ = _build_test_client(
        provider_yml_path,
        facilitator=facilitator,
        transport=transport,
        monkeypatch=monkeypatch,
    )

    try:
        spec = load_provider_file(provider_yml_path)
        _, requirements = build_payment_requirements(spec, spec.endpoints[0])
        payload = PaymentPayload(
            x402Version=2,
            accepted=requirements[0],
            payload=PaymentPayloadData(signature="0xdeadbeef"),
        )

        response = client.get(
            "/providers/acme-weather/v1/current",
            headers={PAYMENT_SIGNATURE_HEADER: encode_payment_payload(payload)},
        )

        assert response.status_code == 502
        assert response.json()["detail"] == "facilitator verify failed"
        metrics = client.get("/__402/metrics").text
        assert (
            'x402_gateway_payment_verify_total{endpoint="/v1/current",'
            'method="GET",provider="acme-weather",result="error"} 1'
        ) in metrics
    finally:
        client.close()


def test_metered_endpoint_502s_when_settle_raises(
    provider_yml_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facilitator = _FakeFacilitator(raise_settle=True)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    client, _, _ = _build_test_client(
        provider_yml_path,
        facilitator=facilitator,
        transport=transport,
        monkeypatch=monkeypatch,
    )

    try:
        spec = load_provider_file(provider_yml_path)
        _, requirements = build_payment_requirements(spec, spec.endpoints[0])
        payload = PaymentPayload(
            x402Version=2,
            accepted=requirements[0],
            payload=PaymentPayloadData(signature="0xdeadbeef"),
        )

        response = client.get(
            "/providers/acme-weather/v1/current",
            headers={PAYMENT_SIGNATURE_HEADER: encode_payment_payload(payload)},
        )

        assert response.status_code == 502
        assert response.json()["detail"] == "facilitator settle failed"
        metrics = client.get("/__402/metrics").text
        assert (
            'x402_gateway_payment_settle_total{endpoint="/v1/current",'
            'method="GET",provider="acme-weather",result="error"} 1'
        ) in metrics
    finally:
        client.close()
