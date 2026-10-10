"""Typed API payload dataclasses for planner jobs, field conflicts, Config Optimizer, and Fleet Planner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal

from spaceinvasion.runtime.api.payloads._meta import OPT, JsonDict
from spaceinvasion.runtime.api.payloads.state import StrategyConfigData


@dataclass(frozen=True)
class PlannerJobState:
    """Background planner job progress state."""

    _ts_open_record: ClassVar[bool] = True

    job_id: str | None = field(default=None, metadata=OPT)
    kind: str = field(default="", metadata=OPT)
    status: Literal["idle", "running", "completed", "error"] | str = field(default="", metadata=OPT)
    phase: str = field(default="", metadata=OPT)
    progress_pct: float = field(default=0.0, metadata=OPT)
    elapsed_ms: float = field(default=0.0, metadata=OPT)
    started_ts: float | None = field(default=None, metadata=OPT)
    finished_ts: float | None = field(default=None, metadata=OPT)
    error: str | None = field(default=None, metadata=OPT)
    trigger_source: str = field(default="", metadata=OPT)


@dataclass(frozen=True)
class FieldConflictCandidate:
    """Candidate building eligible for demolition to resolve a colony field conflict."""

    _ts_open_record: ClassVar[bool] = True

    building_key: str
    building_id: int = field(default=0, metadata=OPT)
    name: str = field(default="", metadata=OPT)
    level: int = field(default=0, metadata=OPT)
    energy_delta: float = field(default=0.0, metadata=OPT)
    safe_to_demolish: bool = field(default=False, metadata=OPT)


@dataclass(frozen=True)
class FieldConflictItem:
    """Pending or resolved colony field-capacity conflict requiring operator decision."""

    _ts_open_record: ClassVar[bool] = True

    conflict_key: str
    planet_id: int
    planet_coords: str = field(default="", metadata=OPT)
    planet_name: str = field(default="", metadata=OPT)
    blocked_building_key: str = field(default="", metadata=OPT)
    blocked_building_name: str = field(default="", metadata=OPT)
    blocked_target_level: int = field(default=0, metadata=OPT)
    candidate_building_key: str = field(default="", metadata=OPT)
    candidate_level: int = field(default=0, metadata=OPT)
    candidates: list[FieldConflictCandidate] = field(default_factory=list, metadata=OPT)
    status: Literal["pending", "resolved"] | str = field(default="", metadata=OPT)
    decision: str = field(default="", metadata=OPT)
    created_ts: float = field(default=0.0, metadata=OPT)
    resolved_ts: float | None = field(default=None, metadata=OPT)
    detail: JsonDict = field(default_factory=dict, metadata=OPT)


@dataclass(frozen=True)
class PlannerJobsStatusPayload:
    """Status envelope for background Config Optimizer and AI-Planner jobs."""

    _ts_open_record: ClassVar[bool] = True

    ok: bool = field(default=True, metadata=OPT)
    config_optimizer_job: PlannerJobState | None = field(default=None, metadata=OPT)
    ai_planner_job: PlannerJobState | None = field(default=None, metadata=OPT)
    colonies_owned: int = field(default=0, metadata=OPT)
    max_planets: int = field(default=0, metadata=OPT)
    live_ai_unlocked: bool = field(default=False, metadata=OPT)
    active_planner_mode: str = field(default="", metadata=OPT)
    effective_planner_mode: str = field(default="", metadata=OPT)
    fallback_reason: str | None = field(default=None, metadata=OPT)
    pending_field_conflicts: list[FieldConflictItem] = field(default_factory=list, metadata=OPT)


@dataclass(frozen=True)
class ConfigOptimizerPlanetRecord:
    """Per-planet steady-state optimization record in Config Optimizer."""

    _ts_open_record: ClassVar[bool] = True

    slot: int = field(default=0, metadata=OPT)
    slot_index: int = field(default=0, metadata=OPT)
    planet_id: int = field(default=0, metadata=OPT)
    coords: str = field(default="", metadata=OPT)
    name: str = field(default="", metadata=OPT)
    planet_status: str = field(default="", metadata=OPT)
    is_placeholder: bool = field(default=False, metadata=OPT)
    is_central_planet: bool = field(default=False, metadata=OPT)
    pays_for_research: bool = field(default=False, metadata=OPT)
    temperature: int = field(default=0, metadata=OPT)
    temperature_c: int = field(default=0, metadata=OPT)
    fields_max: int = field(default=0, metadata=OPT)
    fields_used: int = field(default=0, metadata=OPT)
    fields_used_target: int = field(default=0, metadata=OPT)
    fields_free: int = field(default=0, metadata=OPT)
    built_floors: dict[str, int] = field(default_factory=dict, metadata=OPT)
    reserved_floors: dict[str, int] = field(default_factory=dict, metadata=OPT)
    detected_specialization: str = field(default="", metadata=OPT)
    locked_specialization: str = field(default="", metadata=OPT)
    previous_specialization: str | None = field(default=None, metadata=OPT)
    specialization_changed: bool = field(default=False, metadata=OPT)
    production: JsonDict = field(default_factory=dict, metadata=OPT)
    warehouses: list[JsonDict] | JsonDict = field(default_factory=dict, metadata=OPT)
    buildings: list[JsonDict] | JsonDict = field(default_factory=dict, metadata=OPT)


@dataclass(frozen=True)
class ConfigOptimizerResultData:
    """Serialized steady-state Config Optimizer run result."""

    _ts_open_record: ClassVar[bool] = True

    schema_version: int = field(default=0, metadata=OPT)
    generated_at: str = field(default="", metadata=OPT)
    elapsed_ms: float = field(default=0.0, metadata=OPT)
    meta_parameters: JsonDict = field(default_factory=dict, metadata=OPT)
    inputs: JsonDict = field(default_factory=dict, metadata=OPT)
    planned_research_levels: dict[str, int] | list[JsonDict] = field(default_factory=dict, metadata=OPT)
    summary: JsonDict = field(default_factory=dict, metadata=OPT)
    search: JsonDict = field(default_factory=dict, metadata=OPT)
    limits: list[JsonDict] = field(default_factory=list, metadata=OPT)
    planets: list[ConfigOptimizerPlanetRecord] = field(default_factory=list, metadata=OPT)


@dataclass(frozen=True)
class YearlyShipsWeekPoint:
    """Weekly point inside `ConfigOptimizerYearlyProjection`."""

    week: int
    year: int
    week_in_year: int
    ships_per_week: float
    resource_cap_per_week: float
    shipyard_cap_per_week: float
    bottleneck: str
    completion_pct: float


@dataclass(frozen=True)
class YearlyShipsSummaryRow:
    """Annual summary row inside `ConfigOptimizerYearlyProjection`."""

    year: int
    label: str
    start_ships_per_week: float
    avg_ships_per_week: float
    end_ships_per_week: float
    peak_ships_per_week: float
    delta_ships_per_week: float
    total_ships_produced: float
    cumulative_ships_produced: float
    bottleneck: str
    completion_pct: float


@dataclass(frozen=True)
class ConfigOptimizerYearlyProjection:
    """Multi-year target ship production trajectory."""

    target_ship_key: str
    target_ship: str
    horizon_years: int
    total_weeks: int
    current_ships_per_week: float
    target_ships_per_week: float
    target_ships_per_month: float
    total_upgrade_levels: int
    years: list[YearlyShipsSummaryRow]
    weeks: list[YearlyShipsWeekPoint]
    weeks_to_90_pct: int | None = field(default=None, metadata=OPT)
    weeks_to_full_target: int | None = field(default=None, metadata=OPT)
    years_to_full_target: float | None = field(default=None, metadata=OPT)


@dataclass(frozen=True)
class ConfigOptimizerActiveRun:
    """Active saved Config Optimizer run envelope."""

    _ts_open_record: ClassVar[bool] = True

    run_id: int = field(default=0, metadata=OPT)
    updated_ts: float = field(default=0.0, metadata=OPT)
    params: JsonDict = field(default_factory=dict, metadata=OPT)
    reserved_floors: dict[str, dict[str, int]] = field(default_factory=dict, metadata=OPT)
    result: ConfigOptimizerResultData = field(default_factory=ConfigOptimizerResultData, metadata=OPT)


@dataclass(frozen=True)
class ConfigOptimizerGetPayload:
    """Response envelope for `config_optimizer_get`."""

    _ts_open_record: ClassVar[bool] = True

    ok: bool = field(default=True, metadata=OPT)
    active: ConfigOptimizerActiveRun | None = field(default=None, metadata=OPT)
    job: PlannerJobState | None = field(default=None, metadata=OPT)
    runs: list[JsonDict] = field(default_factory=list, metadata=OPT)
    reserved_floors: dict[str, dict[str, int]] = field(default_factory=dict, metadata=OPT)
    planet_specializations: dict[str, str] = field(default_factory=dict, metadata=OPT)
    custom_planet_names: dict[str, str] = field(default_factory=dict, metadata=OPT)
    specialization_options: list[str] = field(default_factory=list, metadata=OPT)
    default_params: JsonDict = field(default_factory=dict, metadata=OPT)
    colonies_owned: int = field(default=0, metadata=OPT)
    max_planets: int = field(default=0, metadata=OPT)


@dataclass(frozen=True)
class FleetPlannerGetPayload:
    """Response envelope for `fleet_planner_get`."""

    _ts_open_record: ClassVar[bool] = True

    ok: bool = field(default=True, metadata=OPT)
    graph: JsonDict | None = field(default=None, metadata=OPT)
    slot_budgets: list[JsonDict] | JsonDict = field(default_factory=dict, metadata=OPT)
    attack_telemetry: JsonDict | None = field(default=None, metadata=OPT)
    spare_budget: JsonDict | None = field(default=None, metadata=OPT)
    config: StrategyConfigData = field(default_factory=StrategyConfigData, metadata=OPT)
