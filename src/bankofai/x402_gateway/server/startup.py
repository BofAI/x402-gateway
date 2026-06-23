"""Gateway startup orchestration (gateway.md §2.1).

`build_app` returns a FastAPI app whose startup hook:

  1. Loads provider.yml file(s)
  2. Resolves signer per provider (4-level fallback)
  3. Resolves recipient per provider (3-level fallback)
  4. Probes facilitator `/supported` (advisory)
  5. Probes recipient native-token balance (advisory)
  6. Prints the rich banner
  7. Optionally mounts a filtered `/openapi.json`
Any of steps 1-3 failing aborts startup; steps 4-5 are advisory and degrade
to a yellow/red banner. The CLI maps thrown errors to `typer.Exit(code=1)`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import FastAPI

from bankofai.x402_gateway.config.loader import load_provider_dir, load_provider_file
from bankofai.x402_gateway.config.spec import ProviderSpec
from bankofai.x402_gateway.server.app import create_app
from bankofai.x402_gateway.server.banner import print_banner
from bankofai.x402_gateway.server.health import (
    BalanceReport,
    FacilitatorSupportedReport,
    probe_balance,
    probe_facilitator_supported,
)
from bankofai.x402_gateway.server.recipient import (
    RecipientNotConfigured,
    RecipientResolution,
    resolve_recipient,
)
from bankofai.x402_gateway.server.registry import ProviderRegistry
from bankofai.x402_gateway.server.signer import SignerHandle, resolve_signer
from bankofai.x402_gateway.telemetry.logging import log_event

logger = logging.getLogger(__name__)


class StartupError(Exception):
    """Raised when a startup step fails irrecoverably.

    The CLI catches this and maps it to a typer.Exit with the original message
    so seller sees a clean error line rather than a stack trace.
    """


@dataclass
class StartupReport:
    """All resolved per-provider state — used by the banner + admin endpoints."""

    spec: ProviderSpec
    signer: SignerHandle
    recipient: RecipientResolution
    facilitator: FacilitatorSupportedReport
    balance: Optional[BalanceReport] = None
    errors: list[str] = field(default_factory=list)


def collect_providers(
    provider_yml: Optional[Path] = None,
    providers_dir: Optional[Path] = None,
) -> list[ProviderSpec]:
    providers: list[ProviderSpec] = []
    if provider_yml is not None:
        try:
            providers.append(load_provider_file(provider_yml))
        except Exception as exc:
            raise StartupError(f"failed to load {provider_yml}: {exc}") from exc
    if providers_dir is not None:
        try:
            providers.extend(load_provider_dir(providers_dir))
        except Exception as exc:
            raise StartupError(f"failed to load {providers_dir}: {exc}") from exc
    if not providers:
        raise StartupError("provide provider.yml or --providers-dir")

    seen: set[str] = set()
    duplicate_names: set[str] = set()
    for provider in providers:
        if provider.name in seen:
            duplicate_names.add(provider.name)
        seen.add(provider.name)
    if duplicate_names:
        raise StartupError(f"duplicate providers: {', '.join(sorted(duplicate_names))}")
    log_event(
        logger,
        logging.INFO,
        "gateway.startup.providers_collected",
        provider_count=len(providers),
        provider_names=[provider.name for provider in providers],
    )
    return providers


def validate_provider_runtime_config(
    providers: list[ProviderSpec],
    *,
    sandbox: bool,
    profile: Optional[str],
) -> None:
    for spec in providers:
        try:
            signer = resolve_signer(spec, sandbox=sandbox, profile=profile)
            resolve_recipient(spec, signer)
        except RecipientNotConfigured as exc:
            raise StartupError(f"{spec.name}: {exc}") from exc
        except Exception as exc:
            raise StartupError(f"{spec.name}: failed to resolve signer/recipient: {exc}") from exc


async def _build_report(
    spec: ProviderSpec,
    *,
    sandbox: bool,
    profile: Optional[str],
) -> StartupReport:
    signer = resolve_signer(spec, sandbox=sandbox, profile=profile)
    try:
        recipient = resolve_recipient(spec, signer)
    except RecipientNotConfigured as exc:
        raise StartupError(str(exc)) from exc
    resolved_spec = spec.model_copy(
        update={
            "operator": spec.operator.model_copy(
                update={"recipient": recipient.address}
            )
        }
    )
    facilitator_url = spec.operator.facilitator_url
    facilitator_report, balance_report = await asyncio.gather(
        probe_facilitator_supported(facilitator_url),
        probe_balance(spec.operator.network, recipient.address),
    )
    log_event(
        logger,
        logging.INFO if facilitator_report.reachable else logging.WARNING,
        "gateway.startup.provider_resolved",
        provider=resolved_spec.name,
        network=resolved_spec.operator.network,
        signer_origin=signer.origin,
        signer_backend=signer.backend,
        facilitator_reachable=facilitator_report.reachable,
        facilitator_detail=facilitator_report.detail,
        balance_severity=balance_report.severity if balance_report else None,
        balance_display=balance_report.display if balance_report else None,
    )
    return StartupReport(
        spec=resolved_spec,
        signer=signer,
        recipient=recipient,
        facilitator=facilitator_report,
        balance=balance_report,
    )


async def load_registry(
    registry: ProviderRegistry,
    provider_yml: Optional[Path] = None,
    providers_dir: Optional[Path] = None,
    *,
    sandbox: bool = False,
    profile: Optional[str] = None,
    print_banners: bool = True,
    host: str = "127.0.0.1",
    port: int = 4020,
) -> list[StartupReport]:
    providers = collect_providers(provider_yml, providers_dir)
    reports: list[StartupReport] = []
    for spec in providers:
        report = await _build_report(spec, sandbox=sandbox, profile=profile)
        reports.append(report)

    signers = {report.spec.name: report.signer for report in reports}
    payment_statuses = {
        report.spec.name: "ok" if report.facilitator.reachable else "unreachable"
        for report in reports
    }
    await registry.replace_all(
        [report.spec for report in reports],
        signers,
        payment_statuses=payment_statuses,
    )
    log_event(
        logger,
        logging.INFO,
        "gateway.startup.registry_loaded",
        provider_count=len(reports),
        facilitator_unreachable_count=sum(
            1 for report in reports if not report.facilitator.reachable
        ),
    )

    if print_banners:
        from rich.console import Console

        console = Console()
        for report in reports:
            print_banner(
                console,
                provider=report.spec,
                signer=report.signer,
                recipient=report.recipient,
                facilitator=report.facilitator,
                balance=report.balance,
                host=host,
                port=port,
            )

    return reports


def build_app(
    provider_yml: Optional[Path] = None,
    providers_dir: Optional[Path] = None,
    *,
    sandbox: bool = False,
    profile: Optional[str] = None,
    print_banners: bool = True,
    host: str = "127.0.0.1",
    port: int = 4020,
    openapi_url: Optional[str] = None,
) -> FastAPI:
    """Build a FastAPI app whose startup hook seeds the provider registry.

    Raises `StartupError` synchronously when the resolved config is bad — the
    CLI converts that to `typer.Exit(code=1)`.
    """

    # Validate eagerly: we want the CLI to fail before uvicorn even binds the
    # socket, so a typo in provider.yml doesn't leave a half-running gateway.
    providers = collect_providers(provider_yml, providers_dir)
    validate_provider_runtime_config(providers, sandbox=sandbox, profile=profile)

    registry = ProviderRegistry()
    app = create_app(registry)

    @app.on_event("startup")
    async def startup() -> None:
        try:
            await load_registry(
                registry,
                provider_yml,
                providers_dir,
                sandbox=sandbox,
                profile=profile,
                print_banners=print_banners,
                host=host,
                port=port,
            )
        except StartupError:
            # Surface to the caller; uvicorn logs and we exit.
            raise

        if openapi_url:
            from bankofai.x402_gateway.server.openapi import mount_openapi

            mount_openapi(app, registry, openapi_url)

    return app
