"""Watchfiles-driven hot reload (gateway.md §4).

We re-validate on every file change before mutating the registry: if the new
file is invalid, the old registry stays in place and inflight requests keep
working. This matches the gateway.md guarantee that "解析成功就 atomic
替换 ... 失败保留旧版,inflight 请求不掉".

The watcher runs as a background asyncio task — uvicorn's event loop owns it.
On reload errors we log loudly but never crash the server.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Iterable, Optional

from watchfiles import awatch

from bankofai.x402_gateway.config.loader import load_provider_dir, load_provider_file
from bankofai.x402_gateway.config.spec import ProviderSpec
from bankofai.x402_gateway.server.recipient import resolve_recipient
from bankofai.x402_gateway.server.registry import ProviderRegistry
from bankofai.x402_gateway.server.signer import SignerHandle, resolve_signer

logger = logging.getLogger(__name__)


async def _reload_once(
    paths: list[Path],
    registry: ProviderRegistry,
    *,
    sandbox: bool,
    profile: Optional[str],
) -> None:
    new_specs: list[ProviderSpec] = []
    for path in paths:
        if path.is_dir():
            new_specs.extend(load_provider_dir(path))
        else:
            new_specs.append(load_provider_file(path))

    seen = set()
    for spec in new_specs:
        if spec.name in seen:
            raise ValueError(f"duplicate providers in reload: {spec.name}")
        seen.add(spec.name)

    signers: dict[str, SignerHandle] = {}
    for spec in new_specs:
        handle = resolve_signer(spec, sandbox=sandbox, profile=profile)
        # confirm recipient resolves so we don't ship a bad spec into the
        # registry; we don't store the resolution here (registry owns specs)
        resolve_recipient(spec, handle)
        signers[spec.name] = handle

    await registry.replace_all(new_specs, signers)
    logger.info("hot reload: applied %d provider(s)", len(new_specs))


def start_reload_watcher(
    paths: Iterable[Path],
    registry: ProviderRegistry,
    *,
    sandbox: bool = False,
    profile: Optional[str] = None,
    host: str = "127.0.0.1",  # noqa: ARG001
    port: int = 4020,  # noqa: ARG001
) -> asyncio.Task[None]:
    """Spawn the watchfiles task. Returns the asyncio.Task for tests / shutdown."""
    paths_list = [Path(p) for p in paths]
    watch_args = [str(p) for p in paths_list]

    async def _runner() -> None:
        async for _changes in awatch(*watch_args):
            try:
                await _reload_once(
                    paths_list, registry, sandbox=sandbox, profile=profile
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("hot reload failed; keeping previous config: %s", exc)

    return asyncio.create_task(_runner(), name="x402-gateway-reload")
