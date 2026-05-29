"""Walk a providers-root and parse every catalog entry.

Source priority per provider directory:
  1. `provider.yml`  — generated listing.md from `display` + `discovery`
  2. `listing.md`    — hand-authored frontmatter (legacy / advanced use)

Sellers normally maintain only `provider.yml`; the skills repo CI calls
`generate_listing_text(spec)` at build time. The fallback to `listing.md`
exists for advanced users who want hand-authored markdown.

FQN derivation: path relative to providers_root, drop the filename, join the
remaining parts with `/`. Matches gateway.md §3.2.

Body validation: the markdown body must contain the required `##`-level
sections from gateway.md §3.4 (Spend-aware usage, When to use). Missing a
required section fails the listing during static check; advisory sections
(When NOT to use / Request examples / Response examples) only warn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import frontmatter

from bankofai.x402_gateway.catalog.generate import generate_listing_text
from bankofai.x402_gateway.catalog.spec import ListingSpec
from bankofai.x402_gateway.config.loader import load_provider_file

REQUIRED_SECTIONS = ("Spend-aware usage", "When to use")
ADVISORY_SECTIONS = ("When NOT to use", "Request examples", "Response examples")

_HEADING_RE = re.compile(r"^\s*##\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class DiscoveredListing:
    fqn: str
    path: Path
    spec: ListingSpec
    body_markdown: str
    section_titles: list[str] = field(default_factory=list)
    missing_required_sections: list[str] = field(default_factory=list)
    missing_advisory_sections: list[str] = field(default_factory=list)
    probe_targets: list[tuple[str, str]] = field(default_factory=list)
    source: str = "listing.md"  # "provider.yml" | "listing.md"


def fqn_from_dir(dir_path: Path, providers_root: Path) -> str:
    rel = dir_path.relative_to(providers_root)
    parts = rel.parts
    if not parts:
        raise ValueError(f"catalog entry at providers_root: cannot derive FQN ({dir_path})")
    return "/".join(parts)


def find_provider_dirs(providers_root: Path) -> Iterator[Path]:
    """Yield every directory under providers_root that holds a provider.yml or listing.md."""
    seen: set[Path] = set()
    for candidate in sorted(providers_root.glob("**/provider.yml")):
        seen.add(candidate.parent)
        yield candidate.parent
    for candidate in sorted(providers_root.glob("**/listing.md")):
        if candidate.parent not in seen:
            seen.add(candidate.parent)
            yield candidate.parent


# Kept for back-compat — emits listing.md paths only.
def find_listings(providers_root: Path) -> Iterator[Path]:
    yield from sorted(providers_root.glob("**/listing.md"))


def _extract_sections(body: str) -> list[str]:
    return [match.group(1).strip() for match in _HEADING_RE.finditer(body)]


def _check_sections(
    body: str, path: Path
) -> tuple[list[str], list[str], list[str]]:
    sections = _extract_sections(body)
    section_set = {s.lower() for s in sections}
    missing_required = [
        section for section in REQUIRED_SECTIONS if section.lower() not in section_set
    ]
    missing_advisory = [
        section for section in ADVISORY_SECTIONS if section.lower() not in section_set
    ]
    if missing_required:
        raise ValueError(
            f"{path}: missing required body section(s): {', '.join(missing_required)}"
        )
    return sections, missing_required, missing_advisory


def _discover_from_provider_yml(
    yml_path: Path, fqn: str
) -> DiscoveredListing:
    provider_spec = load_provider_file(yml_path)
    last_segment = fqn.rsplit("/", 1)[-1]
    if provider_spec.name != last_segment:
        raise ValueError(
            f"{yml_path}: provider.name ({provider_spec.name!r}) must match path tail "
            f"({last_segment!r})"
        )

    rendered = generate_listing_text(provider_spec)
    post = frontmatter.loads(rendered)
    spec = ListingSpec.model_validate(post.metadata)
    sections, missing_required, missing_advisory = _check_sections(post.content, yml_path)

    return DiscoveredListing(
        fqn=fqn,
        path=yml_path,
        spec=spec,
        body_markdown=post.content,
        section_titles=sections,
        missing_required_sections=missing_required,
        missing_advisory_sections=missing_advisory,
        probe_targets=[(endpoint.method, endpoint.path) for endpoint in provider_spec.endpoints],
        source="provider.yml",
    )


def _discover_from_listing_md(
    md_path: Path, fqn: str
) -> DiscoveredListing:
    post = frontmatter.load(md_path)
    if not post.metadata:
        raise ValueError(f"{md_path}: missing YAML frontmatter")
    spec = ListingSpec.model_validate(post.metadata)
    last_segment = fqn.rsplit("/", 1)[-1]
    if spec.name != last_segment:
        raise ValueError(
            f"{md_path}: frontmatter.name ({spec.name!r}) must match path tail "
            f"({last_segment!r})"
        )

    sections, missing_required, missing_advisory = _check_sections(post.content, md_path)

    return DiscoveredListing(
        fqn=fqn,
        path=md_path,
        spec=spec,
        body_markdown=post.content,
        section_titles=sections,
        missing_required_sections=missing_required,
        missing_advisory_sections=missing_advisory,
        source="listing.md",
    )


def discover(providers_root: Path) -> list[DiscoveredListing]:
    """Walk providers_root, picking provider.yml first then listing.md fallback."""
    results: list[DiscoveredListing] = []
    for dir_path in find_provider_dirs(providers_root):
        fqn = fqn_from_dir(dir_path, providers_root)
        provider_yml = dir_path / "provider.yml"
        listing_md = dir_path / "listing.md"
        if provider_yml.exists():
            results.append(_discover_from_provider_yml(provider_yml, fqn))
        elif listing_md.exists():
            results.append(_discover_from_listing_md(listing_md, fqn))
    return results


# Back-compat: legacy callers passing a listing.md path directly.
def fqn_from_listing(path: Path, providers_root: Path) -> str:
    rel = path.relative_to(providers_root)
    parts = rel.parent.parts
    if not parts:
        raise ValueError(f"listing.md at providers_root: cannot derive FQN ({path})")
    return "/".join(parts)
