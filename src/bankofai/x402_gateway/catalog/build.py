"""`catalog build` orchestrator: discover -> probe (selected) -> render.

Incremental mode (gateway.md §3.4):

  build --only fqn1,fqn2 --previous-dist /tmp/prev-dist

The selected FQNs go through the full discover → probe → render path;
all other listings have their `providers/<fqn>.json` copied from
`previous_dist/providers/`. `skills.json` is always regenerated so the
index reflects the on-disk providers/ tree.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Iterable, Optional

from bankofai.x402_gateway.catalog.check import probe_all
from bankofai.x402_gateway.catalog.discover import DiscoveredListing, discover
from bankofai.x402_gateway.catalog.render import (
    render_skills_index,
    write_dist,
)

logger = logging.getLogger(__name__)


def _filter_listings(
    listings: list[DiscoveredListing], only_fqns: Optional[Iterable[str]]
) -> list[DiscoveredListing]:
    if only_fqns is None:
        return listings
    allowed = set(only_fqns)
    missing = allowed - {listing.fqn for listing in listings}
    if missing:
        raise ValueError(f"--only references unknown fqn(s): {sorted(missing)}")
    return [listing for listing in listings if listing.fqn in allowed]


def _dist_provider_path(dist_dir: Path, fqn: str) -> Path:
    return dist_dir / "providers" / f"{fqn.replace('/', '__')}.json"


def _copy_previous_provider(previous_dist: Path, dist_dir: Path, fqn: str) -> dict | None:
    """Copy a single provider JSON from a previous dist; return parsed contents (or None)."""
    source = _dist_provider_path(previous_dist, fqn)
    if not source.exists():
        logger.warning("incremental build: %s not in previous-dist; skipping", fqn)
        return None
    target = _dist_provider_path(dist_dir, fqn)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    try:
        return json.loads(source.read_text())
    except json.JSONDecodeError as exc:
        logger.warning("incremental build: %s in previous-dist is invalid (%s)", fqn, exc)
        return None


async def build_catalog(
    providers_root: Path,
    dist_dir: Path,
    *,
    only: Optional[Iterable[str]] = None,
    previous_dist: Optional[Path] = None,
) -> None:
    """Re-probe `only` (or all) listings and write `dist_dir`.

    When `--only` is set without `--previous-dist`, we still re-probe only
    those FQNs but the merged skills.json index will be partial (missing
    rows). Pass `--previous-dist` to keep the unchanged rows.
    """
    listings = discover(providers_root)
    selected = _filter_listings(listings, only)

    # Probe + render only the selected ones
    result = await probe_all(selected)
    write_dist(dist_dir, selected, result)

    # Carry over unchanged rows from previous dist
    carried_entries: list[dict] = []
    if previous_dist is not None and only is not None:
        unchanged = [listing for listing in listings if listing.fqn not in set(only)]
        for listing in unchanged:
            entry = _copy_previous_provider(previous_dist, dist_dir, listing.fqn)
            if entry is not None:
                carried_entries.append(entry)
            else:
                logger.warning(
                    "incremental build: %s missing from previous-dist; "
                    "will be absent from skills.json",
                    listing.fqn,
                )

    # Re-render skills.json index covering re-probed + carried entries
    fresh_entries: list[dict] = []
    for check in result.listings:
        path = _dist_provider_path(dist_dir, check.fqn)
        if path.exists():
            fresh_entries.append(json.loads(path.read_text()))

    combined = fresh_entries + carried_entries
    if combined:
        skills_index = render_skills_index(combined)
        (dist_dir / "skills.json").write_text(
            json.dumps(skills_index, indent=2, sort_keys=True) + "\n"
        )
