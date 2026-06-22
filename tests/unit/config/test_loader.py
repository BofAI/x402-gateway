from __future__ import annotations

from pathlib import Path

import pytest

from bankofai.x402_gateway.config.loader import (
    EnvNotSet,
    expandvars_strict,
    load_provider_file,
    load_provider_text,
)


def test_load_provider_file_normalizes_network(provider_yml_path: Path) -> None:
    spec = load_provider_file(provider_yml_path)

    assert spec.name == "acme-weather"
    assert spec.operator.network == "tron:mainnet"
    assert spec.endpoints[0].method == "GET"
    # forward_url shorthand should populate routing.url
    assert spec.routing.url == "https://api.example.com"


def test_expandvars_strict_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TOKEN", "abc123")
    expanded = expandvars_strict("token: ${MY_TOKEN}")
    assert expanded == "token: abc123"


def test_expandvars_strict_fails_on_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ABSOLUTELY_NOT_SET", raising=False)
    with pytest.raises(EnvNotSet):
        expandvars_strict("x: ${ABSOLUTELY_NOT_SET}")


def test_load_provider_text_expands_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REC", "TRecipientFromEnv")
    text = """
name: example
title: "Example"
description: "Example"
category: data
version: v1
forward_url: https://upstream.example
routing:
  type: proxy
operator:
  network: tron-mainnet
  currencies:
    usd: ["USDT"]
  recipient: "${REC}"
endpoints:
  - method: GET
    path: /healthz
"""
    spec = load_provider_text(text)
    assert spec.operator.recipient == "TRecipientFromEnv"
