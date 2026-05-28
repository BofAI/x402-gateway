"""Scaffold a new listing.md from an OpenAPI URL (gateway.md §3.4).

The scaffold tries to dereference the OpenAPI doc and embed a comment listing
the operations it found, so the seller can see exactly which paths will be
probed at `catalog check` time. If the fetch fails, we still write the
listing.md skeleton — the seller can fill it in by hand.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


LISTING_TEMPLATE = """\
---
name: {name}
title: "TODO: human-readable title"
description: "TODO: one-sentence pitch"
use_case: "TODO: use for ..."
category: other
service_url: TODO_SERVICE_URL
openapi:
  url: {openapi_url}
tags: []
---

## Spend-aware usage
- TODO: how should agents decide when to spend on this API?

## When to use
- TODO

## When NOT to use
- TODO

## Request examples
- TODO

## Response examples
- TODO
{operations_comment}"""


@dataclass(frozen=True)
class OpenAPIOperation:
    method: str
    path: str
    summary: Optional[str] = None


def parse_openapi_operations(document: dict[str, Any]) -> list[OpenAPIOperation]:
    operations: list[OpenAPIOperation] = []
    paths = document.get("paths") or {}
    if not isinstance(paths, dict):
        return operations
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            summary = None
            if isinstance(operation, dict):
                summary = operation.get("summary") or operation.get("operationId")
            operations.append(
                OpenAPIOperation(method=method.upper(), path=str(path), summary=summary)
            )
    return operations


async def fetch_openapi(openapi_url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
        response = await client.get(openapi_url)
        response.raise_for_status()
        return response.json()


async def _try_fetch_operations(openapi_url: str) -> tuple[list[OpenAPIOperation], Optional[str]]:
    try:
        document = await fetch_openapi(openapi_url)
    except httpx.HTTPError as exc:
        return [], f"openapi fetch failed: {exc}"
    return parse_openapi_operations(document), None


def _format_operations_comment(
    operations: list[OpenAPIOperation], failure: Optional[str]
) -> str:
    if failure:
        return (
            "\n<!-- catalog scaffold: could not fetch OpenAPI; fill the endpoints by hand. "
            f"reason: {failure} -->\n"
        )
    if not operations:
        return "\n<!-- catalog scaffold: OpenAPI dereferenced 0 operations -->\n"
    lines = [
        "",
        "<!-- catalog scaffold: discovered operations from OpenAPI",
        "     these will be probed by `catalog check` once service_url is filled in",
    ]
    for op in operations:
        summary = f" - {op.summary}" if op.summary else ""
        lines.append(f"     {op.method:7s} {op.path}{summary}")
    lines.append("-->")
    return "\n".join(lines) + "\n"


def scaffold_listing(
    providers_root: Path,
    fqn: str,
    openapi_url: str,
    *,
    overwrite: bool = False,
    operations: Optional[list[OpenAPIOperation]] = None,
    fetch_failure: Optional[str] = None,
) -> Path:
    """Write `providers_root/<fqn>/listing.md` from a template."""
    target_dir = providers_root.joinpath(*fqn.split("/"))
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "listing.md"
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target} already exists; pass overwrite=True to replace")

    name = fqn.rsplit("/", 1)[-1]
    comment = _format_operations_comment(operations or [], fetch_failure)
    target.write_text(
        LISTING_TEMPLATE.format(name=name, openapi_url=openapi_url, operations_comment=comment)
    )
    return target


async def scaffold_listing_with_fetch(
    providers_root: Path,
    fqn: str,
    openapi_url: str,
    *,
    overwrite: bool = False,
) -> Path:
    """Scaffold + fetch OpenAPI in one call (used by the CLI)."""
    operations, failure = await _try_fetch_operations(openapi_url)
    return scaffold_listing(
        providers_root,
        fqn,
        openapi_url,
        overwrite=overwrite,
        operations=operations,
        fetch_failure=failure,
    )
