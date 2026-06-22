"""In-memory runtime snapshot of loaded providers + their facilitator clients."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from bankofai.x402_gateway.config.spec import EndpointSpec, ProviderSpec
from bankofai.x402_gateway.facilitator.client import FacilitatorAPI, build_facilitator
from bankofai.x402_gateway.server.signer import SignerHandle


@dataclass
class ProviderRuntimeState:
    name: str
    config_status: str
    payment_status: str = "unknown"
    upstream_status: str = "unknown"
    last_loaded_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    last_error: Optional[str] = None
    signer: Optional[SignerHandle] = None


@dataclass
class ProviderEntry:
    spec: ProviderSpec
    facilitator: FacilitatorAPI
    state: ProviderRuntimeState = field(
        default_factory=lambda: ProviderRuntimeState(name="", config_status="unknown")
    )


def _endpoint_path_matches(template: str, path: str) -> bool:
    """Match endpoint paths with segment-level `{param}` placeholders."""
    template_parts = template.strip("/").split("/") if template.strip("/") else []
    path_parts = path.strip("/").split("/") if path.strip("/") else []
    if len(template_parts) != len(path_parts):
        return False
    for template_part, path_part in zip(template_parts, path_parts):
        if (
            template_part.startswith("{")
            and template_part.endswith("}")
            and len(template_part) > 2
        ):
            if not path_part:
                return False
            continue
        if template_part != path_part:
            return False
    return True


class ProviderRegistry:
    """Thread-safe in-memory provider snapshot.

    Atomicity: `replace_all` swaps the entire map under a single asyncio Lock.
    Inflight requests holding the old `entry` reference continue to use the
    old provider spec.
    """

    def __init__(self) -> None:
        self._entries: dict[str, ProviderEntry] = {}
        self._lock = asyncio.Lock()

    # --- Read helpers -------------------------------------------------------

    def get(self, name: str) -> Optional[ProviderSpec]:
        entry = self._entries.get(name)
        return entry.spec if entry else None

    def get_entry(self, name: str) -> Optional[ProviderEntry]:
        return self._entries.get(name)

    def snapshot(self) -> dict[str, ProviderSpec]:
        return {name: entry.spec for name, entry in self._entries.items()}

    def state_snapshot(self) -> dict[str, ProviderRuntimeState]:
        return {name: entry.state for name, entry in self._entries.items()}

    def match_endpoint(self, provider_name: str, method: str, path: str) -> Optional[EndpointSpec]:
        provider = self.get(provider_name)
        if provider is None:
            return None
        normalized_method = method.upper()
        normalized_path = "/" + path.lstrip("/")
        template_candidates: list[EndpointSpec] = []
        for endpoint in provider.endpoints:
            if endpoint.method != normalized_method:
                continue
            if endpoint.path == normalized_path:
                return endpoint
            if _endpoint_path_matches(endpoint.path, normalized_path):
                template_candidates.append(endpoint)
        if template_candidates:
            return template_candidates[0]
        return None

    # --- Write helpers ------------------------------------------------------

    @staticmethod
    def _build_entry(
        provider: ProviderSpec,
        signer: Optional[SignerHandle] = None,
    ) -> ProviderEntry:
        facilitator = build_facilitator(provider.operator.facilitator_url)
        now = datetime.now(timezone.utc)
        state = ProviderRuntimeState(
            name=provider.name,
            config_status="loaded",
            last_loaded_at=now,
            signer=signer,
        )
        return ProviderEntry(spec=provider, facilitator=facilitator, state=state)

    async def replace_all(
        self,
        providers: list[ProviderSpec],
        signers: dict[str, SignerHandle] | None = None,
    ) -> None:
        signers = signers or {}
        entries = {
            provider.name: self._build_entry(provider, signers.get(provider.name))
            for provider in providers
        }
        async with self._lock:
            self._entries = entries

    async def replace(
        self,
        name: str,
        spec: ProviderSpec,
        signer: Optional[SignerHandle] = None,
    ) -> None:
        async with self._lock:
            self._entries = {
                **self._entries,
                name: self._build_entry(spec, signer),
            }

    async def remove(self, name: str) -> None:
        async with self._lock:
            self._entries = {k: v for k, v in self._entries.items() if k != name}
