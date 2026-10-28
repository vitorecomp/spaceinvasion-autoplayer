"""Typed parameter dataclasses for galaxy intel and background sweep `@command` handlers."""

from __future__ import annotations

from dataclasses import dataclass, field

from spaceinvasion.runtime.api.validation import FieldBounds


@dataclass(frozen=True)
class EspionageIntelParams:
    """Parameters for querying enriched espionage targets and reports."""

    limit: int = field(default=150, metadata={"bounds": FieldBounds(min_val=1, max_val=1000)})
    coords: object = None


@dataclass(frozen=True)
class ScannedSectorsParams:
    """Parameters for querying longitudinal scanned sector coverage and smart scan queue."""

    galaxy: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=0, max_val=14)})
    include_unscanned: object = None
    limit: int = field(default=5600, metadata={"bounds": FieldBounds(min_val=1, max_val=5600)})
    planet_id: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=1, max_val=2**63 - 1)})
    preview_refresh_pct: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=0, max_val=100)})
    preview_sweep_pages: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=1, max_val=400)})
    preview_all_galaxies: object = None


@dataclass(frozen=True)
class UniverseResearchMaxParams:
    """Parameters for reading or refreshing universe-wide max research records."""

    sync: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class AttackResultsParams:
    """Parameters for querying battle reports, raid telemetry, and active bash/target blocks."""

    limit: int = field(default=150, metadata={"bounds": FieldBounds(min_val=1, max_val=1000)})
    coords: object = None


@dataclass(frozen=True)
class RecyclerResultsParams:
    """Parameters for querying recycle reports, active debris targets, and inbox emails."""

    limit: int = field(default=150, metadata={"bounds": FieldBounds(min_val=1, max_val=1000)})
    category: object = None


@dataclass(frozen=True)
class GalaxySweepParams:
    """Parameters for starting, stopping, resetting, inspecting, or dry-running a galaxy sweep."""

    action: str | None = field(default=None, metadata={"bounds": FieldBounds(strip=True)})
    pages: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=1, max_val=400)})
    galaxy: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=1, max_val=14)})
    start_galaxy: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=1, max_val=14)})
    start_system: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=1, max_val=400)})
    system: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=1, max_val=400)})
    delay_min_ms: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=0, max_val=60_000)})
    delay_max_ms: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=0, max_val=60_000)})
    dry_run: bool = False


__all__ = [
    "AttackResultsParams",
    "EspionageIntelParams",
    "GalaxySweepParams",
    "RecyclerResultsParams",
    "ScannedSectorsParams",
    "UniverseResearchMaxParams",
]
