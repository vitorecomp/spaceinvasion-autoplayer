"""Frozen DTO dataclasses for telemetry, colonies, and empire snapshots."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionHistoryRecord:
    """Typed commander action audit row (`action_history`)."""

    id: int
    ts: float
    source: str
    command: str
    priority: str | None
    kind: str
    planet_id: int | None
    planet_coords: str | None
    target_id: int | None
    target_name: str | None
    target_level: int | None
    executed: bool
    dry_run: bool
    reason: str | None
    message: str | None


@dataclass(frozen=True)
class EconomyRatesRecord:
    """Typed per-planet economy throttle cache row (`economy_rates_cache`)."""

    planet_id: int
    coords: str
    rates: dict[str, int]
    updated_ts: float


@dataclass(frozen=True)
class HourlyProductionRecord:
    """Typed minute-bucketed empire production snapshot (`hourly_production_history`)."""

    id: int
    minute_bucket: int
    recorded_at: float
    pig_iron_per_hour: float
    metal_per_hour: float
    kryptonite_per_hour: float
    spice_per_hour: float
    total_per_hour: float
    speq_per_hour: float
    planet_count: int
    active_builders: int


@dataclass(frozen=True)
class LootRecycleHourlyRecord:
    """Typed hourly stolen-loot and recycled-debris bucket (`loot_recycle_hourly`)."""

    hour_bucket: int
    stolen_pig_iron: float
    stolen_metal: float
    stolen_kryptonite: float
    stolen_spice: float
    stolen_total: float
    recycled_pig_iron: float
    recycled_metal: float
    recycled_kryptonite: float
    recycled_spice: float
    recycled_total: float
    attack_count: int
    recycle_count: int
    updated_at: float


@dataclass(frozen=True)
class LootRecycleWindowSummary:
    """Aggregated totals across a continuous hourly loot/recycle window."""

    total_stolen: float
    total_recycled: float
    grand_total: float
    attack_count: int
    recycle_count: int
    window_hours: int
    stolen_pig_iron: float
    stolen_metal: float
    stolen_kryptonite: float
    stolen_spice: float
    recycled_pig_iron: float
    recycled_metal: float
    recycled_kryptonite: float
    recycled_spice: float


@dataclass(frozen=True)
class TutorialProgressRecord:
    """Typed tutorial milestone row (`tutorial_progress`)."""

    step: int
    total_steps: int
    tasks_json: str
    reward_text: str
    can_complete: bool
    first_seen_ts: float
    last_seen_ts: float
    completed_ts: float | None


@dataclass(frozen=True)
class UniverseResearchMaxRecord:
    """Typed monotonic universe research maximum row (`universe_research_max`)."""

    research_key: str
    research_id: int
    slug: str
    name: str
    own_level: int
    spied_max_level: int
    server_record_level: int | None
    universe_max_level: int
    catalog_max_level: int | None
    gap_to_max: int
    holder: str
    holder_coords: str
    updated_ts: float


@dataclass(frozen=True)
class ColonyAttemptRecord:
    """Typed append-only colonization attempt row (`colony_attempts`)."""

    id: int
    ts: float
    event_type: str
    target_coords: str
    origin_coords: str
    planet_id: int | None
    fields_rolled: int | None
    min_fields_target: int
    expected_min_fields: int | None
    expected_max_fields: int | None
    temperature: int | None
    grade: str
    score: int | None
    status_message: str
    details_json: str


@dataclass(frozen=True)
class ColonyCandidateRecord:
    """Ranked colony candidate row returned by `query_colony_candidates`."""

    coords: str
    galaxy: int
    system: int
    position: int
    uninhabited_fields: int | None
    colonize_min_fields: int | None
    colonize_max_fields: int | None
    max_fields_est: int
    temperature: int | None
    is_probed: bool
    last_spied_ts: float
    last_scanned_ts: float
    spy_count: int
    blocked_until_ts: float
    block_reason: str
    is_discarded: bool
    is_active: bool
    updated_ts: float
    distance: float
    meets_target: bool
    tier: int


@dataclass(frozen=True)
class StateSnapshotRecord:
    """Typed full state and plan snapshot row (`state_snapshots`)."""

    id: int
    ts: float
    source: str
    universe_id: int
    user_id: int
    nickname: str
    active_planet_id: int
    planet_count: int
    planned_action_count: int
    state_json: str | None
    plan_json: str | None


@dataclass(frozen=True)
class LatestStateHotRecord:
    """Typed hot operational snapshot row (`latest_state_hot`)."""

    source_snapshot_id: int
    ts: float
    source: str
    universe_id: int
    user_id: int
    nickname: str
    active_planet_id: int
    planet_count: int
    planned_action_count: int
    state_json: str | None
    plan_json: str | None
    updated_ts: float


@dataclass(frozen=True)
class PlanetSnapshotRecord:
    """Typed per-planet telemetry snapshot row (`planet_snapshots`)."""

    id: int
    ts: float
    universe_id: int
    planet_id: int
    coords: str
    name: str
    planet_type: str
    temperature: int
    fields_used: int
    fields_max: int
    pig_iron: int
    metal: int
    kryptonite: int
    spice: int
    energy_produced: int
    energy_used: int
    buildings_json: str
    ships_json: str
    defense_json: str
    economy_rates_json: str
    researches_json: str


@dataclass(frozen=True)
class EmpirePlanetHotRecord:
    """Typed materialized owned planet hot row (`empire_planets_hot`)."""

    planet_id: int
    universe_id: int
    coords: str
    name: str
    planet_type: str
    temperature: int
    fields_used: int
    fields_max: int
    pig_iron: int
    metal: int
    kryptonite: int
    spice: int
    energy_produced: int
    energy_used: int
    is_active: bool
    updated_ts: float


@dataclass(frozen=True)
class RankSnapshotRecord:
    """Typed account rank and score evolution sample (`rank_snapshots`)."""

    id: int
    ts: float
    universe_id: int
    nickname: str
    server_points: int | None
    server_rank: int | None
    estimated_points: float
    pts_per_hr: float
    spice_value: float
    planets: int


__all__ = [
    "ActionHistoryRecord",
    "ColonyAttemptRecord",
    "ColonyCandidateRecord",
    "EconomyRatesRecord",
    "EmpirePlanetHotRecord",
    "HourlyProductionRecord",
    "LatestStateHotRecord",
    "LootRecycleHourlyRecord",
    "LootRecycleWindowSummary",
    "PlanetSnapshotRecord",
    "RankSnapshotRecord",
    "StateSnapshotRecord",
    "TutorialProgressRecord",
    "UniverseResearchMaxRecord",
]
