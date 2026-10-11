"""Frozen DTO dataclasses for espionage intel, galaxy scans, slots, and sector analytics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpyTargetRecord:
    """Typed espionage dossier row (`spy_targets`)."""

    coords: str
    owner: str
    planet_name: str
    status_color: str
    is_inactive: bool
    is_spacia: bool
    has_debris: bool
    points: int | None
    pig_iron: int
    metal: int
    kryptonite: int
    spice: int
    fleet_json: str
    defense_json: str
    dug_in: bool
    last_scanned_ts: float
    last_spied_ts: float
    blocked_until_ts: float
    block_reason: str
    bash_hits: int
    last_blocked_ts: float
    uninhabited_fields: int | None
    spy_count: int
    is_own_planet: bool
    merged_into: str
    colonize_min_fields: int | None
    colonize_max_fields: int | None
    temperature: int | None


@dataclass(frozen=True)
class SpyReportDTO:
    """Typed append-only espionage report row (`spy_reports`)."""

    id: int
    ts: float
    target_coords: str
    owner: str
    planet_name: str
    is_spacia: bool
    pig_iron: int
    metal: int
    kryptonite: int
    spice: int
    loot_spice_eq: float
    buildings_json: str
    fleet_json: str
    defense_json: str
    researches_json: str
    dug_in: bool | None
    uninhabited_fields: int | None
    server_msg_id: str
    msg_date_text: str
    raw_text: str


@dataclass(frozen=True)
class GalaxyScanRecord:
    """Typed latest galaxy system scan row (`galaxy_scans`)."""

    galaxy: int
    system: int
    sector_key: str
    last_scanned_ts: float
    occupied_count: int
    target_count: int
    free_count: int


@dataclass(frozen=True)
class GalaxyScanHistoryRecord:
    """Typed append-only galaxy scan observation (`galaxy_scan_history`)."""

    id: int
    ts: float
    galaxy: int
    system: int
    occupied_count: int
    target_count: int
    free_count: int
    debris_count: int
    asteroid_count: int


@dataclass(frozen=True)
class GalaxySlotRecord:
    """Typed current coordinate slot state (`galaxy_slots`)."""

    coords: str
    galaxy: int
    system: int
    position: int
    planet_name: str
    owner: str
    status_color: str
    is_inactive: bool
    is_spacia: bool
    has_debris: bool
    last_scanned_ts: float
    colonize_min_fields: int | None
    colonize_max_fields: int | None
    temperature: int | None


@dataclass(frozen=True)
class GalaxySlotHistoryRecord:
    """Typed append-only galaxy slot observation (`galaxy_slot_history`)."""

    id: int
    ts: float
    coords: str
    galaxy: int
    system: int
    position: int
    planet_name: str
    owner: str
    status_color: str
    is_inactive: bool
    is_spacia: bool
    has_debris: bool
    has_asteroid: bool
    colonize_min_fields: int | None
    colonize_max_fields: int | None
    temperature: int | None


@dataclass(frozen=True)
class GalaxySectorHotRecord:
    """Typed materialized per-sector hot summary (`galaxy_sector_hot`)."""

    galaxy: int
    system: int
    sector_key: str
    last_scanned_ts: float
    scan_count: int
    occupied_count: int
    target_count: int
    inactive_count: int
    spacia_count: int
    debris_count: int
    free_count: int
    prime_colony_free_count: int
    hist_debris_max: int
    owners_json: str
    owner_planet_counts_json: str
    coord_owners_json: str
    spacia_coords_json: str
    updated_ts: float


@dataclass(frozen=True)
class GalaxyCoverageSummary:
    """Per-galaxy (`G1`-`G14`) scan coverage rollup returned by `query_galaxy_coverage`."""

    galaxy: int
    scanned_systems: int
    total_systems: int
    coverage_pct: float
    total_scans: int
    occupied_count: int
    target_count: int
    inactive_count: int
    spacia_count: int
    debris_count: int
    free_count: int
    prime_colony_free_count: int
    latest_scanned_ts: float | None


@dataclass(frozen=True)
class GalaxySectorIntelRecord:
    """Enriched per-sector tactical intel row returned by `query_galaxy_sector_intel`."""

    galaxy: int
    system: int
    sector_key: str
    last_scanned_ts: float
    scan_count: int
    occupied_count: int
    target_count: int
    inactive_count: int
    spacia_count: int
    debris_count: int
    free_count: int
    prime_colony_free_count: int
    raid_count: int
    battle_loot_spice_eq: float
    owners: tuple[str, ...]
    spacia_coords: tuple[str, ...]


@dataclass(frozen=True)
class GalaxyIntelSummary:
    """Universe-wide and per-galaxy sector intel returned by `query_galaxy_sector_intel`."""

    active_galaxies: tuple[int, ...]
    universe_scanned_systems: int
    universe_total_systems: int
    coverage_by_galaxy: tuple[GalaxyCoverageSummary, ...]
    sectors: tuple[GalaxySectorIntelRecord, ...]


__all__ = [
    "GalaxyCoverageSummary",
    "GalaxyIntelSummary",
    "GalaxyScanHistoryRecord",
    "GalaxyScanRecord",
    "GalaxySectorHotRecord",
    "GalaxySectorIntelRecord",
    "GalaxySlotHistoryRecord",
    "GalaxySlotRecord",
    "SpyReportDTO",
    "SpyTargetRecord",
]
