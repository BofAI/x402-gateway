"""`x402-gateway catalog` command group: scaffold, check, build."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer

from bankofai.x402_gateway.catalog.build import build_catalog
from bankofai.x402_gateway.catalog.check import check_catalog
from bankofai.x402_gateway.catalog.generate import (
    generate_listing_file,
    generate_listing_text,
    is_skills_ready,
)
from bankofai.x402_gateway.catalog.scaffold import (
    scaffold_listing,
    scaffold_listing_with_fetch,
)
from bankofai.x402_gateway.catalog.search import search_catalog
from bankofai.x402_gateway.config.loader import load_provider_file

app = typer.Typer(help="Catalog tools")


@app.command()
def generate(
    provider_yml: Path = typer.Argument(..., help="provider.yml to render."),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        help="Write listing.md here (defaults to <provider.yml dir>/listing.md).",
    ),
    stdout: bool = typer.Option(
        False, "--stdout", help="Emit the listing.md content to stdout instead of writing a file."
    ),
    overwrite: bool = typer.Option(True, "--overwrite/--no-overwrite"),
) -> None:
    """Render listing.md from provider.yml (display + discovery blocks).

    Used by skills-repo CI: seller submits only provider.yml; CI runs
    `catalog generate` then `catalog check` to validate the rendered markdown.
    """
    spec = load_provider_file(provider_yml)
    issues = is_skills_ready(spec)
    if issues:
        for issue in issues:
            typer.secho(f"  - {issue}", fg=typer.colors.RED, err=True)
        typer.secho(
            "provider.yml is not skills-ready; fix the issues above and rerun.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    if stdout:
        typer.echo(generate_listing_text(spec))
        return

    target = output if output is not None else provider_yml.parent / "listing.md"
    generate_listing_file(spec, target, overwrite=overwrite)
    typer.echo(str(target))


@app.command()
def scaffold(
    fqn: str = typer.Argument(..., help="FQN like sunio/perp-swap."),
    openapi_url: str = typer.Argument(..., help="Upstream OpenAPI URL."),
    providers_root: Path = typer.Option(
        Path("providers"),
        "--providers-root",
        help="Root of the providers/ directory tree.",
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing listing.md."),
    skip_fetch: bool = typer.Option(
        False,
        "--skip-fetch",
        help="Don't dereference the OpenAPI URL (useful when offline).",
    ),
) -> None:
    """Create providers/<fqn>/listing.md from a template.

    When the OpenAPI URL is reachable we embed a comment listing all
    discovered operations so the seller can see what `catalog check` will
    probe; if it's not, we still write the skeleton.
    """
    if skip_fetch:
        path = scaffold_listing(providers_root, fqn, openapi_url, overwrite=overwrite)
    else:
        path = asyncio.run(
            scaffold_listing_with_fetch(
                providers_root, fqn, openapi_url, overwrite=overwrite
            )
        )
    typer.echo(str(path))


@app.command()
def check(
    providers_root: Path = typer.Argument(..., help="Root of the providers/ tree."),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON instead of a human summary."
    ),
) -> None:
    """Static + live probe + verdict for every listing under providers_root."""
    result = asyncio.run(check_catalog(providers_root))

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "block": result.block,
                    "listings": [
                        {
                            "fqn": c.fqn,
                            "path": str(c.path),
                            "block": c.block,
                            "ok_count": c.ok_count,
                            "non_compat_count": c.non_compat_count,
                            "error_count": c.error_count,
                            "probes": [
                                {
                                    "method": p.method,
                                    "path": p.path,
                                    "status": p.status.value,
                                    "network": p.network,
                                    "currency": p.currency,
                                    "amount_raw": p.amount_raw,
                                    "detail": p.detail,
                                }
                                for p in c.probes
                            ],
                        }
                        for c in result.listings
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    for check_result in result.listings:
        typer.echo(
            f"{check_result.fqn:32s}  "
            f"ok={check_result.ok_count}  "
            f"non_compat={check_result.non_compat_count}  "
            f"err={check_result.error_count}  "
            f"block={check_result.block}"
        )

    if result.block:
        raise typer.Exit(code=2)


@app.command()
def build(
    providers_root: Path = typer.Argument(..., help="Root of the providers/ tree."),
    dist_dir: Path = typer.Option(Path("dist"), "--dist-dir", help="Where to write artifacts."),
    only: Optional[str] = typer.Option(
        None,
        "--only",
        help="Comma-separated FQN allowlist (incremental builds).",
    ),
    previous_dist: Optional[Path] = typer.Option(
        None,
        "--previous-dist",
        help="Previous dist directory (reserved for incremental copy; ignored for now).",
    ),
) -> None:
    """Build dist/skills.json and dist/providers/<fqn>.json."""
    only_fqns: Optional[list[str]] = None
    if only:
        only_fqns = [fragment.strip() for fragment in only.split(",") if fragment.strip()]

    asyncio.run(
        build_catalog(
            providers_root,
            dist_dir,
            only=only_fqns,
            previous_dist=previous_dist,
        )
    )
    typer.echo(str(dist_dir))


@app.command()
def search(
    providers_root: Path = typer.Argument(..., help="Root of the providers/ tree."),
    query: str = typer.Argument(..., help="Search text."),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum result count."),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON instead of a table."
    ),
) -> None:
    """Search local provider.yml / listing.md catalog metadata."""
    hits = search_catalog(providers_root, query, limit=limit)

    if json_output:
        typer.echo(
            json.dumps(
                {"query": query, "count": len(hits), "results": [hit.to_dict() for hit in hits]},
                indent=2,
                sort_keys=True,
            )
        )
        return

    if not hits:
        typer.echo("no matches")
        raise typer.Exit(code=1)

    for hit in hits:
        tags = ",".join(hit.tags) if hit.tags else "-"
        typer.echo(
            f"{hit.fqn:32s}  score={hit.score:<3d}  "
            f"category={hit.category:12s}  tags={tags}"
        )
        typer.echo(f"  {hit.title}")
        typer.echo(f"  {hit.description}")
        if hit.endpoints:
            for endpoint in hit.endpoints[:3]:
                typer.echo(f"  {endpoint.method:6s} {endpoint.gateway_path}")
        typer.echo("")
