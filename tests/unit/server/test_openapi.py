from __future__ import annotations

from bankofai.x402_gateway.server.openapi import filter_openapi


def test_filter_drops_paths_not_in_allowlist() -> None:
    upstream = {
        "openapi": "3.0.0",
        "info": {"title": "upstream", "version": "1"},
        "servers": [{"url": "https://upstream.example"}],
        "paths": {
            "/v1/keep": {"get": {"summary": "kept"}},
            "/v1/drop": {"post": {"summary": "dropped"}},
        },
    }
    out = filter_openapi(
        upstream,
        provider_name="acme",
        allowlist={("GET", "/v1/keep")},
        gateway_base="https://gw.example.com",
    )
    assert list(out["paths"].keys()) == ["/providers/acme/v1/keep"]
    assert out["servers"] == [{"url": "https://gw.example.com"}]


def test_filter_drops_methods_not_in_allowlist() -> None:
    upstream = {
        "openapi": "3.0.0",
        "paths": {
            "/v1/foo": {
                "get": {"summary": "ok"},
                "post": {"summary": "block this method"},
            }
        },
    }
    out = filter_openapi(
        upstream,
        provider_name="acme",
        allowlist={("GET", "/v1/foo")},
        gateway_base="https://gw.example.com",
    )
    methods = out["paths"]["/providers/acme/v1/foo"]
    assert "get" in methods
    assert "post" not in methods
