"""Allow `python -m bankofai.x402_gateway ...` as an alias for the CLI."""

from bankofai.x402_gateway.cli.main import main

if __name__ == "__main__":
    main()
