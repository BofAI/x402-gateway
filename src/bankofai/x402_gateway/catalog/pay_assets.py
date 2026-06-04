"""Generate payment-facing assets from provider.yml."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bankofai.x402_gateway.config.spec import EndpointSpec, ProviderSpec
from bankofai.x402_gateway.server.payment import (
    build_payment_required,
    build_payment_requirements,
)


def _provider_base_url(spec: ProviderSpec, fallback_gateway_base: str | None) -> str:
    if spec.display.service_url:
        return spec.display.service_url.rstrip("/")
    if fallback_gateway_base:
        return f"{fallback_gateway_base.rstrip('/')}/providers/{spec.name}"
    return f"TODO_SERVICE_URL/providers/{spec.name}"


def _endpoint_url(provider_base_url: str, endpoint: EndpointSpec) -> str:
    return f"{provider_base_url}{endpoint.path}"


def _endpoint_payload(spec: ProviderSpec, endpoint: EndpointSpec, provider_base_url: str) -> dict:
    resolution, requirements = build_payment_requirements(spec, endpoint)
    payment_required = build_payment_required(spec, endpoint)
    return {
        "method": endpoint.method,
        "path": endpoint.path,
        "url": _endpoint_url(provider_base_url, endpoint),
        "description": endpoint.description or spec.description,
        "metered": not resolution.is_free,
        "priceUsd": resolution.price_usd,
        "paymentRequired": payment_required.model_dump(by_alias=True, exclude_none=True),
        "accepts": [
            requirement.model_dump(by_alias=True, exclude_none=True)
            for requirement in requirements
        ],
    }


def generate_pay_json(spec: ProviderSpec, *, fallback_gateway_base: str | None = None) -> dict:
    """Return the machine-readable payment asset for a provider."""
    provider_base_url = _provider_base_url(spec, fallback_gateway_base)
    endpoints = [
        _endpoint_payload(spec, endpoint, provider_base_url)
        for endpoint in spec.endpoints
        if endpoint.metering is not None
    ]
    return {
        "schemaVersion": 1,
        "provider": {
            "name": spec.name,
            "title": spec.title,
            "description": spec.description,
            "category": spec.category,
            "version": spec.version,
            "serviceUrl": provider_base_url,
        },
        "operator": {
            "network": spec.operator.network,
            "scheme": spec.operator.scheme,
            "recipient": spec.operator.recipient,
            "validForSeconds": spec.operator.valid_for_seconds,
            "facilitatorUrl": spec.operator.facilitator_url,
            "currencies": spec.operator.currencies,
        },
        "paidEndpoints": endpoints,
    }


def _money(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".")


def generate_pay_markdown(
    spec: ProviderSpec,
    pay_json: dict[str, Any],
) -> str:
    """Render a human/agent-readable payment guide from pay.json."""
    provider = pay_json["provider"]
    operator = pay_json["operator"]
    endpoints = pay_json["paidEndpoints"]

    lines = [
        f"# {provider['title']} Payment Guide",
        "",
        provider["description"],
        "",
        "## Provider",
        "",
        f"- Name: `{provider['name']}`",
        f"- Version: `{provider['version']}`",
        f"- Service URL: `{provider['serviceUrl']}`",
        f"- Network: `{operator['network']}`",
        f"- Scheme: `{operator['scheme']}`",
        f"- Recipient: `{operator['recipient']}`",
        "",
        "## Paid Endpoints",
        "",
    ]

    if not endpoints:
        lines.extend(["No paid endpoints are declared in provider.yml.", ""])
        return "\n".join(lines).rstrip() + "\n"

    for endpoint in endpoints:
        lines.extend(
            [
                f"### {endpoint['method']} {endpoint['path']}",
                "",
                endpoint["description"],
                "",
                f"- URL: `{endpoint['url']}`",
                f"- Price: `${_money(float(endpoint['priceUsd']))}`",
                "",
                "Accepted payment requirements:",
                "",
            ]
        )
        for requirement in endpoint["accepts"]:
            lines.append(
                "- "
                f"`{requirement['network']}` "
                f"`{requirement['scheme']}` "
                f"`{requirement['amount']}` "
                f"`{requirement['asset']}`"
            )
        lines.extend(
            [
                "",
                "Example:",
                "",
                "```bash",
                f"x402-cli pay '{endpoint['url']}'",
                "```",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def write_pay_assets(
    spec: ProviderSpec,
    output_dir: Path,
    *,
    fallback_gateway_base: str | None = None,
    overwrite: bool = True,
) -> tuple[Path, Path]:
    """Write pay.md and pay.json into output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "pay.md"
    json_path = output_dir / "pay.json"
    if not overwrite:
        existing = [path for path in (md_path, json_path) if path.exists()]
        if existing:
            names = ", ".join(str(path) for path in existing)
            raise FileExistsError(f"{names} already exists; pass overwrite=True to replace")

    payload = generate_pay_json(spec, fallback_gateway_base=fallback_gateway_base)
    md_path.write_text(generate_pay_markdown(spec, payload))
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return md_path, json_path
