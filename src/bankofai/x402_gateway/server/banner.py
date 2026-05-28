"""Startup banner (gateway.md §2.1).

Pretty-prints the gateway state on stdout via rich. Wraps optional checks so
the banner shows up even when one probe fails — seller debugging value beats
strict consistency here.
"""

from __future__ import annotations

from typing import Iterable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from bankofai.x402_gateway.config.spec import ProviderSpec
from bankofai.x402_gateway.server.health import (
    BalanceReport,
    FacilitatorSupportedReport,
)
from bankofai.x402_gateway.server.recipient import RecipientResolution
from bankofai.x402_gateway.server.signer import SignerHandle

_SEVERITY_STYLE = {
    "ok": "green",
    "low": "yellow",
    "zero": "bold red",
    "unknown": "dim",
}


def print_banner(
    console: Console,
    *,
    provider: ProviderSpec,
    signer: SignerHandle,
    recipient: RecipientResolution,
    facilitator: FacilitatorSupportedReport,
    balance: BalanceReport | None,
    host: str,
    port: int,
) -> None:
    summary = _summary_table(
        provider=provider,
        signer=signer,
        recipient=recipient,
        facilitator=facilitator,
        balance=balance,
        host=host,
        port=port,
    )
    console.print(Panel(summary, title=f"x402-gateway · {provider.name}", border_style="cyan"))

    if facilitator and not facilitator.reachable:
        console.print(
            f"[yellow]warn[/yellow] facilitator probe failed: {facilitator.detail or 'unknown'}; "
            "payments won't settle until the URL is reachable",
        )

    if balance and balance.severity == "zero":
        console.print(
            Panel(
                f"[bold red]Recipient {recipient.address} has 0 native balance on "
                f"{balance.network}.[/bold red]\n"
                f"Settlement transactions will revert until the wallet has gas. "
                f"Send a small amount of TRX / BNB to {recipient.address}.",
                title="WARNING",
                border_style="red",
            )
        )
    elif balance and balance.severity == "low":
        console.print(
            f"[yellow]note[/yellow] recipient {recipient.address} balance is low "
            f"({balance.display}); top up before mainnet traffic.",
        )

    _print_endpoints(console, provider)


def _summary_table(
    *,
    provider: ProviderSpec,
    signer: SignerHandle,
    recipient: RecipientResolution,
    facilitator: FacilitatorSupportedReport,
    balance: BalanceReport | None,
    host: str,
    port: int,
) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", style="dim")
    table.add_column()

    table.add_row("listen", f"http://{host}:{port}")
    table.add_row("network", provider.operator.network)
    table.add_row("scheme", provider.operator.scheme)
    table.add_row(
        "signer",
        f"{signer.origin}"
        + (f" / {signer.backend}" if signer.backend else "")
        + (f" / {signer.profile}" if signer.profile else ""),
    )
    table.add_row(
        "recipient",
        f"{recipient.address} [dim]({recipient.origin}"
        + (f", alias={recipient.alias}" if recipient.alias else "")
        + ")[/dim]",
    )
    if balance is not None:
        style = _SEVERITY_STYLE.get(balance.severity, "dim")
        table.add_row("balance", f"[{style}]{balance.display}[/{style}]")
    table.add_row("facilitator", _format_facilitator(facilitator))
    return table


def _format_facilitator(report: FacilitatorSupportedReport) -> str:
    if not report.reachable:
        return f"[red]unreachable[/red] · {report.detail or '-'}"
    schemes = ",".join(report.schemes) if report.schemes else "(none)"
    networks = ",".join(report.networks) if report.networks else "(none)"
    return f"[green]ok[/green] · schemes={schemes} networks={networks}"


def _print_endpoints(console: Console, provider: ProviderSpec) -> None:
    table = Table(title="endpoints", title_style="bold")
    table.add_column("method", style="cyan", no_wrap=True)
    table.add_column("path")
    table.add_column("metered", justify="center")
    table.add_column("description", style="dim")

    for endpoint in provider.endpoints:
        metered = "[green]yes[/green]" if endpoint.metering is not None else "[dim]no[/dim]"
        table.add_row(endpoint.method, endpoint.path, metered, endpoint.description or "")

    console.print(table)


def banner_lines(provider: ProviderSpec, endpoints: Iterable[str]) -> list[str]:
    """Plain-text banner used by tests / no-tty contexts."""
    out = [f"x402-gateway: {provider.name} ({provider.operator.network})"]
    out.extend(f"  - {line}" for line in endpoints)
    return out
