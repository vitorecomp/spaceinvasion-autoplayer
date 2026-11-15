"""Typed API payload dataclasses for colonies, universe research, and scanned galaxy sectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal

from backend.runtime.api.payloads._meta import OPT, JsonDict


@dataclass(frozen=True)
class ColonyAttemptItem:
    """One append-only `colony_attempts` row recording a colonization attempt."""

    _ts_open_record: ClassVar[bool] = True

    id: int
    ts: float | None = field(default=None, metadata=OPT)
    event_type: str = field(default="", metadata=OPT)
    target_coords: str = field(default="", metadata=OPT)
    origin_coords: str = field(default="", metadata=OPT)
    planet_id: int | None = field(default=None, metadata=OPT)
    fields_rolled: int | None = field(default=None, metadata=OPT)
    min_fields_target: int | None = field(default=None, metadata=OPT)
    expected_min_fields: int | None = field(default=None, metadata=OPT)
    expected_max_fields: int | None = field(default=None, metadata=OPT)
    temperature: int | None = field(default=None, metadata=OPT)
    grade: str = field(default="", metadata=OPT)
    score: float | None = field(default=None, metadata=OPT)
    status_message: str = field(default="", metadata=OPT)
    details: JsonDict | None = field(default=None, metadata=OPT)


@dataclass(frozen=True)
class ColonyAttemptCoordStats:
    """Per-coordinate rollup from `get_colony_attempts_summary().by_coords`."""

    _ts_open_record: ClassVar[bool] = True

    attempts_count: int = field(default=0, metadata=OPT)
    deleted_count: int = field(default=0, metadata=OPT)
    last_event_type: str = field(default="", metadata=OPT)
    last_fields_rolled: int | None = field(default=None, metadata=OPT)
    last_ts: float | None = field(default=None, metadata=OPT)


@dataclass(frozen=True)
class ColonyAttemptsSummary:
    """Aggregate summary of colonization attempt outcomes."""

    _ts_open_record: ClassVar[bool] = True

    total_events: int = field(default=0, metadata=OPT)
    unique_coords_tried: int = field(default=0, metadata=OPT)
    dispatched_count: int = field(default=0, metadata=OPT)
    deleted_undersized_count: int = field(default=0, metadata=OPT)
    kept_count: int = field(default=0, metadata=OPT)
    failed_count: int = field(default=0, metadata=OPT)
    by_coords: dict[str, ColonyAttemptCoordStats] = field(default_factory=dict, metadata=OPT)


@dataclass(frozen=True)
class UniverseResearchMaxRow:
    """Per-research universe maximum comparison row."""

    _ts_open_record: ClassVar[bool] = True

    research_id: int
    slug: str
    name: str
    own_level: int
    universe_max_level: int
    key: str = field(default="", metadata=OPT)
    research_key: str = field(default="", metadata=OPT)
    spied_max_level: int = field(default=0, metadata=OPT)
    server_record_level: int | None = field(default=None, metadata=OPT)
    catalog_max_level: int | None = field(default=None, metadata=OPT)
    gap_to_max: int = field(default=0, metadata=OPT)
    holder: str = field(default="", metadata=OPT)
    holder_coords: str = field(default="", metadata=OPT)
    updated_ts: float = field(default=0.0, metadata=OPT)


@dataclass(frozen=True)
class GalaxyStatsItem:
    """Per-galaxy coverage and target summary."""

    _ts_open_record: ClassVar[bool] = True

    galaxy: int
    scanned_count: int
    unscanned_count: int
    total_systems: int
    coverage_pct: float
    occupied_count: int
    debris_count: int
    target_count: int
    prime_free_count: int
    queued_count: int
    is_owned: bool = field(default=False, metadata=OPT)
    in_sweep_scope: bool = field(default=False, metadata=OPT)


@dataclass(frozen=True)
class ScanQueueItem:
    """Single queued solar system in the smart 2-phase galaxy scanner queue."""

    _ts_open_record: ClassVar[bool] = True

    queue_pos: int
    sector: str
    galaxy: int
    system: int
    phase: Literal["exploratory", "targeted_refresh"] | str
    scanned: bool
    last_scanned_ts: float
    age_seconds: float | None
    scan_count: int
    occupied_count: int
    debris_count: int
    target_count: int
    free_count: int
    prime_colony_free_count: int
    direct_raid_count: int
    owner_affinity_raid_count: int
    system_distance: int | None
    galaxy_delta: int | None
    system_delta: int | None
    score: float
    frequent_owners: list[str] = field(default_factory=list, metadata=OPT)
    circular_distance: int | None = field(default=None, metadata=OPT)
    importance: float = field(default=0.0, metadata=OPT)
    reason: str = field(default="", metadata=OPT)


@dataclass(frozen=True)
class ScannedSectorItem:
    """Detailed scanned sector telemetry row."""

    _ts_open_record: ClassVar[bool] = True

    sector: str
    galaxy: int
    system: int
    occupied_count: int
    free_count: int
    prime_colony_free_count: int
    debris_count: int
    target_count: int
    inactive_count: int
    spacia_count: int
    scan_count: int
    last_scanned_ts: float
    age_seconds: float | None
    system_distance: int | None
    galaxy_delta: int | None
    system_delta: int | None
    direct_raid_count: int
    owner_affinity_raid_count: int
    score: float
    scanned: bool = field(default=False, metadata=OPT)
    queue_pos: int | None = field(default=None, metadata=OPT)
    queue_phase: str | None = field(default=None, metadata=OPT)
    max_slots: int = field(default=0, metadata=OPT)
    active_occupied_count: int = field(default=0, metadata=OPT)
    circular_distance: int | None = field(default=None, metadata=OPT)
    direct_raid_24h_count: int = field(default=0, metadata=OPT)
    direct_loot_spice_eq: float = field(default=0.0, metadata=OPT)
    frequent_owners: list[str] = field(default_factory=list, metadata=OPT)
    importance: float = field(default=0.0, metadata=OPT)
    reason: str = field(default="", metadata=OPT)


@dataclass(frozen=True)
class ScannedSectorsSummary:
    """Aggregate coverage and queue-planning summary for `list_scanned_sectors`."""

    _ts_open_record: ClassVar[bool] = True

    scanned_sectors: int = field(default=0, metadata=OPT)
    unscanned_sectors_in_view: int = field(default=0, metadata=OPT)
    unscanned_total_all_galaxies: int = field(default=0, metadata=OPT)
    total_scanned_all_galaxies: int = field(default=0, metadata=OPT)
    explored_systems: int = field(default=0, metadata=OPT)
    unscanned_systems: int = field(default=0, metadata=OPT)
    total_systems: int = field(default=0, metadata=OPT)
    coverage_pct: float = field(default=0.0, metadata=OPT)
    universe_explored_systems: int = field(default=0, metadata=OPT)
    universe_total_systems: int = field(default=0, metadata=OPT)
    universe_coverage_pct: float = field(default=0.0, metadata=OPT)
    total_occupied: int = field(default=0, metadata=OPT)
    avg_occupied: float = field(default=0.0, metadata=OPT)
    total_free: int = field(default=0, metadata=OPT)
    total_prime_free: int = field(default=0, metadata=OPT)
    total_debris_fields: int = field(default=0, metadata=OPT)
    sectors_with_debris: int = field(default=0, metadata=OPT)
    total_targets: int = field(default=0, metadata=OPT)
    total_spacia: int = field(default=0, metadata=OPT)
    total_inactives: int = field(default=0, metadata=OPT)
    stale_over_1h_count: int = field(default=0, metadata=OPT)
    total_raids: int = field(default=0, metadata=OPT)
    sweep_pages: int = field(default=0, metadata=OPT)
    sweep_interval_seconds: int = field(default=0, metadata=OPT)
    allow_galaxy_scan: bool = field(default=False, metadata=OPT)
    explore_first: bool = field(default=False, metadata=OPT)
    scan_all_galaxies: bool = field(default=False, metadata=OPT)
    refresh_pct: int = field(default=0, metadata=OPT)
    discovery_pct: int = field(default=0, metadata=OPT)
    effective_refresh_pct: int = field(default=0, metadata=OPT)
    effective_discovery_pct: int = field(default=0, metadata=OPT)
    saved_refresh_pct: int = field(default=0, metadata=OPT)
    saved_sweep_pages: int = field(default=0, metadata=OPT)
    saved_scan_all_galaxies: bool = field(default=False, metadata=OPT)
    planned_refresh_pages: int = field(default=0, metadata=OPT)
    planned_discovery_pages: int = field(default=0, metadata=OPT)
    next_batch_refresh_count: int = field(default=0, metadata=OPT)
    next_batch_discovery_count: int = field(default=0, metadata=OPT)
    queue_count: int = field(default=0, metadata=OPT)
    queue_discovery_count: int = field(default=0, metadata=OPT)
    queue_refresh_count: int = field(default=0, metadata=OPT)
    queue_galaxies: list[int] = field(default_factory=list, metadata=OPT)
    scan_phase: str = field(default="", metadata=OPT)


@dataclass(frozen=True)
class ScannedSectorsPayload:
    """Response envelope for `list_scanned_sectors`."""

    _ts_open_record: ClassVar[bool] = True

    ok: bool = field(default=True, metadata=OPT)
    command: str = field(default="", metadata=OPT)
    preview: bool = field(default=False, metadata=OPT)
    origin_coords: str = field(default="", metadata=OPT)
    galaxy_filter: int = field(default=0, metadata=OPT)
    include_unscanned: bool = field(default=False, metadata=OPT)
    galaxies_available: list[int] = field(default_factory=list, metadata=OPT)
    galaxies_with_scans: list[int] = field(default_factory=list, metadata=OPT)
    galaxy_stats: list[GalaxyStatsItem] = field(default_factory=list, metadata=OPT)
    scan_queue: list[ScanQueueItem] = field(default_factory=list, metadata=OPT)
    summary: ScannedSectorsSummary = field(default_factory=ScannedSectorsSummary, metadata=OPT)
    sectors: list[ScannedSectorItem] = field(default_factory=list, metadata=OPT)
