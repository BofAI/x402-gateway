"""Top-level CLI router."""

import typer

from bankofai.x402_gateway.catalog.cli import app as catalog_app
from bankofai.x402_gateway.cli.commands.server import app as server_app

app = typer.Typer(help="x402 gateway")
app.add_typer(server_app, name="server")
app.add_typer(catalog_app, name="catalog")


def main() -> None:
    app()
