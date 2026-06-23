"""Load provider.yml files from disk.

Loader pipeline:
    1. read file -> string
    2. expand ${VAR} placeholders against os.environ (missing var -> EnvNotSet)
    3. yaml.safe_load -> dict
    4. ProviderSpec.model_validate -> typed spec
    5. validate_provider_spec -> business rules

Step 2 happens *before* YAML parsing so we expand inside quoted strings without
inventing per-field validators. This matches the gateway.md §2.2 contract.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Union

import yaml

from bankofai.x402_gateway.config.spec import ProviderSpec
from bankofai.x402_gateway.config.validator import validate_provider_spec

PathLike = Union[str, Path]


class EnvNotSet(ValueError):
    """A `${VAR}` placeholder in provider.yml referenced an unset env variable."""


_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expandvars_strict(text: str, env: dict[str, str] | None = None) -> str:
    """Expand `${VAR}` against env, raising on missing.

    `os.path.expandvars` is too permissive (silently keeps `${MISSING}`).
    Gateway config must fail fast so a typo doesn't ship as a payment recipient.
    """
    source = env if env is not None else os.environ

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in source:
            raise EnvNotSet(f"environment variable ${{{name}}} is not set")
        return source[name]

    return _PLACEHOLDER_RE.sub(_sub, text)


def load_provider_text(text: str, env: dict[str, str] | None = None) -> ProviderSpec:
    expanded = expandvars_strict(text, env=env)
    data = yaml.safe_load(expanded) or {}
    if not isinstance(data, dict):
        raise ValueError("provider.yml must be a YAML mapping at the top level")
    spec = ProviderSpec.model_validate(data)
    validate_provider_spec(spec)
    return spec


def load_provider_file(path: PathLike, env: dict[str, str] | None = None) -> ProviderSpec:
    path = Path(path)
    return load_provider_text(path.read_text(), env=env)


def find_provider_files(providers_dir: PathLike) -> list[Path]:
    providers_dir = Path(providers_dir)
    if not providers_dir.exists():
        raise FileNotFoundError(f"providers directory does not exist: {providers_dir}")
    if not providers_dir.is_dir():
        raise NotADirectoryError(f"providers path is not a directory: {providers_dir}")
    return sorted(providers_dir.glob("**/provider.yml"))


def load_provider_dir(
    providers_dir: PathLike, env: dict[str, str] | None = None
) -> list[ProviderSpec]:
    return [load_provider_file(path, env=env) for path in find_provider_files(providers_dir)]
