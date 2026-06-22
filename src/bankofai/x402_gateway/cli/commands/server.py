"""`x402-gateway server` command group."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer

from bankofai.x402_gateway.cli.templates import write_provider_template
from bankofai.x402_gateway.server.startup import StartupError, build_app
from bankofai.x402_gateway.telemetry.logging import configure_logging

app = typer.Typer(help="Run the gateway server")


@app.command()
def start(
    provider_yml: Optional[Path] = typer.Argument(
        None,
        help="Single provider.yml file to serve.",
    ),
    providers_dir: Optional[Path] = typer.Option(
        None,
        "--providers-dir",
        help="Directory containing providers/**/provider.yml files.",
    ),
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(4020, help="Bind port."),
    sandbox: bool = typer.Option(
        False,
        "--sandbox",
        help="Force the sandbox signer (ephemeral, file-backed).",
    ),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        help="agent-wallet profile name used as the level-3 signer fallback.",
    ),
    openapi_url: Optional[str] = typer.Option(
        None,
        "--openapi",
        help="Upstream OpenAPI URL; when set, the gateway exposes a filtered /openapi.json.",
    ),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress the startup banner."),
) -> None:
    """Start the gateway."""

    if provider_yml is None and providers_dir is None:
        raise typer.BadParameter("provide provider.yml or --providers-dir")
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise typer.BadParameter("uvicorn is required to run the gateway server") from exc

    log_level = os.environ.get("X402_GATEWAY_LOG_LEVEL", "info")
    configure_logging(log_level)

    try:
        app_obj = build_app(
            provider_yml,
            providers_dir,
            sandbox=sandbox,
            profile=profile,
            host=host,
            port=port,
            openapi_url=openapi_url,
            print_banners=not quiet,
        )
    except StartupError as exc:
        typer.secho(f"x402-gateway: startup failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    uvicorn.run(app_obj, host=host, port=port, log_level=log_level.lower())


@app.command()
def check(
    provider_yml: Path = typer.Argument(..., help="provider.yml file to validate."),
) -> None:
    """Parse a provider.yml and exit non-zero on validation errors."""

    from bankofai.x402_gateway.config.loader import load_provider_file

    try:
        spec = load_provider_file(provider_yml)
    except Exception as exc:
        typer.secho(f"x402-gateway: invalid provider.yml: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"ok: {spec.name} ({len(spec.endpoints)} endpoints)")


@app.command()
def scaffold(
    name: str = typer.Argument(..., help="Provider name (also output directory)."),
    output_dir: Path = typer.Option(
        Path("."),
        "--output-dir",
        help="Where to drop the provider.yml file.",
    ),
    forward_url: str = typer.Option(
        "https://upstream.example",
        "--forward-url",
        help="Upstream URL to forward requests to.",
    ),
    network: str = typer.Option(
        "tron-shasta",
        "--network",
        help="Initial network (use a testnet for first-run sandbox).",
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing file."),
) -> None:
    """Write a starter provider.yml file (5-minute getting-started template)."""

    target = write_provider_template(
        output_dir=output_dir,
        name=name,
        forward_url=forward_url,
        network=network,
        overwrite=overwrite,
    )
    typer.echo(str(target))
