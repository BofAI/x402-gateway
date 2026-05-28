"""Shared pytest fixtures + path helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_PYTHON = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_PYTHON / "examples"


@pytest.fixture
def examples_dir() -> Path:
    return EXAMPLES_DIR


@pytest.fixture
def provider_yml_path() -> Path:
    return EXAMPLES_DIR / "provider.yml"


@pytest.fixture
def listing_md_path() -> Path:
    return EXAMPLES_DIR / "listing.md"
