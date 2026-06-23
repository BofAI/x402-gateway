"""Generate listing.md from provider.yml.

`provider.yml` is the single source of truth a seller maintains. The skills
repo CI calls this generator to render the public-facing `listing.md` from
the provider's `display` + `discovery` blocks. The generated file is what
`catalog check` / `catalog build` consume — but it never has to be hand-edited
or committed back.

Rules (gateway.md §3):
- frontmatter mirrors `ListingSpec` exactly; `name` becomes the FQN leaf.
- `service_url` falls back to a deterministic gateway path if not declared.
- `## Spend-aware usage` and `## When to use` are mandatory; the generator
  emits placeholders when the discovery block omits them, so the static check
  fails loudly rather than producing a misleading silent listing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from bankofai.x402_gateway.config.spec import ProviderSpec

REQUIRED_SECTIONS = ("Spend-aware usage", "When to use")
ADVISORY_SECTIONS = ("When NOT to use", "Request examples", "Response examples")


def _yaml_list(items: Iterable[str], indent: str = "") -> str:
    """Render a YAML inline list `[a, b]` (frontmatter-friendly)."""
    cleaned = [str(s).replace('"', '\\"') for s in items]
    return "[" + ", ".join(cleaned) + "]"


def _yaml_value(value: str) -> str:
    """Quote a scalar for YAML frontmatter."""
    return '"' + str(value).replace('"', '\\"') + '"'


def _frontmatter(spec: ProviderSpec, service_url: str) -> str:
    lines = [
        "---",
        f"name: {spec.name}",
        f"title: {_yaml_value(spec.title)}",
        f"description: {_yaml_value(spec.description)}",
        f"use_case: {_yaml_value(spec.discovery.use_case)}",
        f"category: {spec.category}",
        f"service_url: {service_url}",
    ]
    if spec.display.logo:
        lines.append(f"logo: {spec.display.logo}")
    if spec.openapi_url:
        lines.append("openapi:")
        lines.append(f"  url: {spec.openapi_url}")
    if spec.display.tags:
        lines.append(f"tags: {_yaml_list(spec.display.tags)}")
    if spec.display.banner:
        lines.append(f"banner: {spec.display.banner}")
    if spec.display.screenshots:
        lines.append("screenshots:")
        for url in spec.display.screenshots:
            lines.append(f"  - {url}")
    lines.append("---")
    return "\n".join(lines)


def _bulleted_section(title: str, items: list[str]) -> list[str]:
    out = [f"## {title}", ""]
    if items:
        for item in items:
            out.append(f"- {item}")
    else:
        out.append("- TODO")
    out.append("")
    return out


def _resolve_service_url(spec: ProviderSpec, fallback_gateway_base: str | None) -> str:
    if spec.display.service_url:
        return spec.display.service_url
    if fallback_gateway_base:
        return f"{fallback_gateway_base.rstrip('/')}/providers/{spec.name}"
    return f"TODO_SERVICE_URL/providers/{spec.name}"


def generate_listing_text(
    spec: ProviderSpec, *, fallback_gateway_base: str | None = None
) -> str:
    """Render a complete listing.md (frontmatter + body) from a ProviderSpec."""
    service_url = _resolve_service_url(spec, fallback_gateway_base)
    sections: list[str] = []
    sections.extend(_bulleted_section("Spend-aware usage", spec.discovery.spend_aware_usage))
    sections.extend(_bulleted_section("When to use", spec.discovery.when_to_use))
    if spec.discovery.when_not_to_use:
        sections.extend(_bulleted_section("When NOT to use", spec.discovery.when_not_to_use))
    if spec.discovery.request_examples:
        sections.extend(_bulleted_section("Request examples", spec.discovery.request_examples))
    if spec.discovery.response_examples:
        sections.extend(_bulleted_section("Response examples", spec.discovery.response_examples))

    return "\n".join([_frontmatter(spec, service_url), "", *sections]).rstrip() + "\n"


def generate_listing_file(
    spec: ProviderSpec,
    target: Path,
    *,
    fallback_gateway_base: str | None = None,
    overwrite: bool = True,
) -> Path:
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target} already exists; pass overwrite=True to replace")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(generate_listing_text(spec, fallback_gateway_base=fallback_gateway_base))
    return target


def is_skills_ready(spec: ProviderSpec) -> list[str]:
    """Return a list of issues that would block this provider.yml from passing
    catalog static check after generation. Empty list means ready.

    Used by CI to fail PRs early with actionable messages, before running the
    full discover() pipeline.
    """
    issues: list[str] = []
    if not spec.discovery.use_case.strip():
        issues.append("discovery.use_case is empty (one-line pitch required)")
    if not spec.discovery.spend_aware_usage:
        issues.append("discovery.spend_aware_usage is empty (required body section)")
    if not spec.discovery.when_to_use:
        issues.append("discovery.when_to_use is empty (required body section)")
    if not spec.display.service_url:
        issues.append("display.service_url is empty (public gateway URL required)")
    if not spec.display.logo:
        issues.append("display.logo is empty (square image URL required)")
    return issues
