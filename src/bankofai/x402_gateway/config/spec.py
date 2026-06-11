"""Pydantic models for provider.yml.

Wire format follows docs/gateway.md §2.2. Keep this 1:1 with the YAML schema:
the loader does not rewrite keys, only expands ${VAR} placeholders.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CATEGORIES = (
    "ai_ml",
    "cloud",
    "compute",
    "data",
    "devtools",
    "finance",
    "identity",
    "media",
    "messaging",
    "other",
    "productivity",
    "search",
    "security",
    "shopping",
    "storage",
    "translation",
)

Category = Literal[
    "ai_ml",
    "cloud",
    "compute",
    "data",
    "devtools",
    "finance",
    "identity",
    "media",
    "messaging",
    "other",
    "productivity",
    "search",
    "security",
    "shopping",
    "storage",
    "translation",
]

AuthMethod = Literal["header", "query_param", "hmac", "oauth2", "access_token"]
SignerBackend = Literal["privy", "local_secure", "raw_secret"]
RoutingType = Literal["proxy", "respond"]
MeteringDirection = Literal["usage", "input", "output"]
MeteringUnit = Literal["requests", "tokens", "characters", "seconds", "bytes"]


class RoutingAuthSpec(BaseModel):
    """Upstream auth strategy declaration.

    The shape is intentionally tolerant: every strategy reads its own subset of
    fields. See server/auth/<method>.py for the concrete contracts.
    """

    method: AuthMethod
    key: str = "Authorization"
    prefix: str = ""
    value: Optional[str] = None
    value_from_env: Optional[str] = None
    # generic strategy parameters; each strategy reads its own subset
    params: dict[str, object] = Field(default_factory=dict)


class RoutingSpec(BaseModel):
    type: RoutingType = "proxy"
    url: Optional[str] = None
    auth: Optional[RoutingAuthSpec] = None


class SignerSpec(BaseModel):
    backend: SignerBackend
    profile: Optional[str] = None


class OperatorSpec(BaseModel):
    network: str
    currencies: dict[str, list[str]] = Field(default_factory=dict)
    recipient: str
    scheme: str = "exact_permit"
    valid_for_seconds: int = Field(default=300, ge=10, le=3600)
    facilitator_url: Optional[str] = None
    signer: Optional[SignerSpec] = None

    @field_validator("network")
    @classmethod
    def normalize_network(cls, value: str) -> str:
        aliases = {
            "tron-mainnet": "tron:mainnet",
            "tron-shasta": "tron:shasta",
            "tron-nile": "tron:nile",
            "bsc-mainnet": "eip155:56",
            "bsc-testnet": "eip155:97",
        }
        return aliases.get(value, value)


class RecipientSpec(BaseModel):
    account: str
    label: Optional[str] = None


class SplitSpec(BaseModel):
    recipient: str  # alias declared in `recipients` block
    percent: float = Field(ge=0.0, le=100.0)


class TierSpec(BaseModel):
    price_usd: float = Field(ge=0.0)
    up_to: Optional[int] = Field(default=None, ge=1)
    splits: list[SplitSpec] = Field(default_factory=list)


class MeteringDimensionSpec(BaseModel):
    direction: MeteringDirection = "usage"
    unit: MeteringUnit = "requests"
    scale: int = Field(default=1, ge=1)
    tiers: list[TierSpec]

    @field_validator("tiers")
    @classmethod
    def validate_tiers(cls, value: list[TierSpec]) -> list[TierSpec]:
        if not value:
            raise ValueError("dimension must declare at least one tier")
        bounded = [t for t in value if t.up_to is not None]
        sorted_bounded = sorted(bounded, key=lambda t: t.up_to or 0)
        if bounded != sorted_bounded:
            raise ValueError("tiers with up_to must be sorted ascending")
        # at most one final unbounded tier
        unbounded = [t for t in value if t.up_to is None]
        if len(unbounded) > 1:
            raise ValueError("at most one tier may omit up_to (final fallback)")
        if unbounded and value[-1].up_to is not None:
            raise ValueError("unbounded tier must be the last tier")
        return value


class MeteringVariantSpec(BaseModel):
    param: str  # query/body parameter name
    value: str  # literal match
    dimensions: list[MeteringDimensionSpec]


class MeteringSpec(BaseModel):
    dimensions: list[MeteringDimensionSpec] = Field(default_factory=list)
    variants: list[MeteringVariantSpec] = Field(default_factory=list)
    splits: list[SplitSpec] = Field(default_factory=list)


class EndpointSpec(BaseModel):
    method: str
    path: str
    description: Optional[str] = None
    metering: Optional[MeteringSpec] = None

    @field_validator("method")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        upper = value.upper()
        if upper not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            raise ValueError(f"unsupported HTTP method: {value}")
        return upper

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("endpoint path must start with /")
        return value


class DisplaySpec(BaseModel):
    """Public-facing presentation metadata for the catalog / skills index.

    Used at `catalog generate` time to render `listing.md`. The gateway runtime
    does not read these fields — they are advisory and shape the agent-facing
    page only.
    """

    model_config = ConfigDict(extra="forbid")

    service_url: Optional[str] = None  # public gateway URL clients address
    logo: Optional[str] = None  # square image URL (recommended 256x256+)
    banner: Optional[str] = None  # wide header image, optional
    screenshots: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class DiscoverySpec(BaseModel):
    """Agent-facing copy that renders into the markdown body of listing.md.

    Required `use_case` is a one-liner. The other lists become `## H2` sections;
    `spend_aware_usage` + `when_to_use` are required in catalog static check
    (gateway.md §3.4), the rest are advisory.
    """

    model_config = ConfigDict(extra="forbid")

    use_case: str = ""
    spend_aware_usage: list[str] = Field(default_factory=list)
    when_to_use: list[str] = Field(default_factory=list)
    when_not_to_use: list[str] = Field(default_factory=list)
    request_examples: list[str] = Field(default_factory=list)
    response_examples: list[str] = Field(default_factory=list)


class ProviderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    title: str
    description: str
    category: Category
    version: str
    forward_url: Optional[str] = None
    env: dict[str, str] = Field(default_factory=dict)
    routing: RoutingSpec
    operator: OperatorSpec
    recipients: dict[str, RecipientSpec] = Field(default_factory=dict)
    endpoints: list[EndpointSpec]

    # Optional pointer to upstream OpenAPI; used by catalog scaffold +
    # build-time endpoint derivation. Has no runtime effect.
    openapi_url: Optional[str] = None

    # Catalog-only blocks. Required when this provider.yml is submitted to a
    # skills repo (CI enforces); optional for purely-internal gateways.
    display: DisplaySpec = Field(default_factory=DisplaySpec)
    discovery: DiscoverySpec = Field(default_factory=DiscoverySpec)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value:
            raise ValueError("name must not be empty")
        if "/" in value:
            raise ValueError("name must not contain '/'")
        return value

    @model_validator(mode="after")
    def merge_forward_url(self) -> "ProviderSpec":
        """Top-level `forward_url:` is a shorthand for `routing.url:`.

        When both are set, the explicit `routing.url` wins; otherwise we copy.
        """
        if self.routing.url is None and self.forward_url is not None:
            self.routing = self.routing.model_copy(update={"url": self.forward_url})
        if self.routing.type == "proxy" and not self.routing.url:
            raise ValueError("routing.url (or top-level forward_url) is required for proxy routing")
        return self

    @model_validator(mode="after")
    def validate_splits_reference_recipients(self) -> "ProviderSpec":
        for endpoint in self.endpoints:
            metering = endpoint.metering
            if metering is None:
                continue
            self._check_splits(metering.splits, label=f"{endpoint.method} {endpoint.path}")
            for dim in metering.dimensions:
                for tier in dim.tiers:
                    self._check_splits(
                        tier.splits,
                        label=f"{endpoint.method} {endpoint.path} tier",
                    )
            for variant in metering.variants:
                for dim in variant.dimensions:
                    for tier in dim.tiers:
                        self._check_splits(
                            tier.splits,
                            label=(
                                f"{endpoint.method} {endpoint.path} "
                                f"variant {variant.param}={variant.value}"
                            ),
                        )
        return self

    def _check_splits(self, splits: list[SplitSpec], *, label: str) -> None:
        if not splits:
            return
        total = sum(s.percent for s in splits)
        if total > 100.0 + 1e-6:
            raise ValueError(f"{label}: splits sum {total}% exceeds 100%")
        for split in splits:
            if split.recipient not in self.recipients:
                raise ValueError(
                    f"{label}: split recipient '{split.recipient}' "
                    f"not declared in `recipients`"
                )
