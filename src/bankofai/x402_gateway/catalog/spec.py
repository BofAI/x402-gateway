"""Pydantic models for listing.md frontmatter."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from bankofai.x402_gateway.config.spec import Category


class OpenAPISpec(BaseModel):
    url: str


class ListingSpec(BaseModel):
    """`listing.md` YAML frontmatter contract.

    The Markdown body below the frontmatter is opaque to validation — it's
    advisory text for agents (Spend-aware usage / When to use / etc.).
    Image fields (logo, banner, screenshots) and tags are sourced from
    `provider.yml.display` when listing.md is generated.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    title: str
    description: str
    use_case: str
    category: Category
    service_url: str
    logo: Optional[str] = None
    banner: Optional[str] = None
    screenshots: list[str] = Field(default_factory=list)
    openapi: Optional[OpenAPISpec] = None
    tags: list[str] = Field(default_factory=list)
