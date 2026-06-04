from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bankofai.x402_gateway.catalog.pay_assets import (
    generate_pay_json,
    write_pay_assets,
)
from bankofai.x402_gateway.cli.main import app
from bankofai.x402_gateway.config.loader import load_provider_text

PROVIDER = """\
name: weather
title: "Acme Weather API"
description: "Current weather API"
category: data
version: v1

forward_url: https://internal.example

routing:
  type: proxy

operator:
  network: tron-mainnet
  currencies:
    usd: ["USDT"]
  recipient: "TProviderWalletBase58"
  scheme: exact_permit

display:
  service_url: https://gateway.example.com/providers/weather
  logo: https://example.com/logo.png

discovery:
  use_case: "Look up current weather"
  spend_aware_usage:
    - "Use health checks before paid calls."
  when_to_use:
    - "Use for live weather lookup."

endpoints:
  - method: GET
    path: /v1/current
    description: "Current weather for a city"
    metering:
      dimensions:
        - unit: requests
          tiers:
            - price_usd: 0.002

  - method: GET
    path: /health
"""


def test_generate_pay_json_contains_paid_endpoint_requirements() -> None:
    spec = load_provider_text(PROVIDER)

    payload = generate_pay_json(spec)

    assert payload["provider"]["serviceUrl"] == "https://gateway.example.com/providers/weather"
    assert payload["operator"]["network"] == "tron:mainnet"
    assert len(payload["paidEndpoints"]) == 1

    endpoint = payload["paidEndpoints"][0]
    assert endpoint["method"] == "GET"
    assert endpoint["path"] == "/v1/current"
    assert endpoint["url"] == "https://gateway.example.com/providers/weather/v1/current"
    assert endpoint["priceUsd"] == 0.002
    assert endpoint["paymentRequired"]["x402Version"] == 2
    assert endpoint["accepts"][0]["network"] == "tron:mainnet"
    assert endpoint["accepts"][0]["scheme"] == "exact_permit"
    assert endpoint["accepts"][0]["payTo"] == "TProviderWalletBase58"


def test_write_pay_assets_outputs_md_and_json(tmp_path: Path) -> None:
    spec = load_provider_text(PROVIDER)

    md_path, json_path = write_pay_assets(spec, tmp_path)

    assert md_path.name == "pay.md"
    assert json_path.name == "pay.json"
    assert "x402-cli pay 'https://gateway.example.com/providers/weather/v1/current'" in (
        md_path.read_text()
    )
    payload = json.loads(json_path.read_text())
    assert payload["paidEndpoints"][0]["path"] == "/v1/current"


def test_write_pay_assets_respects_no_overwrite(tmp_path: Path) -> None:
    spec = load_provider_text(PROVIDER)
    write_pay_assets(spec, tmp_path)

    with pytest.raises(FileExistsError):
        write_pay_assets(spec, tmp_path, overwrite=False)


def test_catalog_pay_assets_cli_writes_files(tmp_path: Path) -> None:
    provider_yml = tmp_path / "provider.yml"
    provider_yml.write_text(PROVIDER)
    out_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        [
            "catalog",
            "pay-assets",
            str(provider_yml),
            "--output-dir",
            str(out_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (out_dir / "pay.md").exists()
    assert (out_dir / "pay.json").exists()
