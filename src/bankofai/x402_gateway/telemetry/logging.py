"""Logging setup for the gateway process."""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "info") -> None:
    normalized = level.upper()
    numeric_level = getattr(logging, normalized, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )
