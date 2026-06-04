"""Shared pytest fixtures + path helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_PYTHON = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_PYTHON / "examples"


@pytest.fixture(autouse=True)
def _provider_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X402_GATEWAY_PUBLIC_BASE_URL", "https://gateway.example.com")
    monkeypatch.setenv("X402_FACILITATOR_URL", "https://facilitator.example.com")
    monkeypatch.setenv("ACME_API_TOKEN", "test-upstream-token")


@pytest.fixture
def examples_dir() -> Path:
    return EXAMPLES_DIR


@pytest.fixture
def provider_yml_path() -> Path:
    return EXAMPLES_DIR / "provider.yml"


@pytest.fixture
def listing_md_path() -> Path:
    return EXAMPLES_DIR / "listing.md"
