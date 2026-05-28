"""Common upstream auth strategy protocol."""

from __future__ import annotations

import os
from typing import Protocol

import httpx


class AuthStrategy(Protocol):
    async def apply(self, request: httpx.Request) -> None:
        """Mutate the upstream request with provider auth credentials."""


def env_value(name: str | None, *, default: str = "") -> str:
    """Read an env var defensively. Returns `default` (empty) when name is None."""
    if not name:
        return default
    return os.environ.get(name, default)
