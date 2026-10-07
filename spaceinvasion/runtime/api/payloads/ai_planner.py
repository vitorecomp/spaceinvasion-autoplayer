"""Typed API payload dataclasses for AI-Planner steps, smelting schedules, and adaptive diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal

from spaceinvasion.runtime.api.payloads._meta import OPT, JsonDict
from spaceinvasion.runtime.api.payloads.planners import FieldConflictItem, PlannerJobState
from spaceinvasion.runtime.api.payloads.state import ResourceBundle


@dataclass(frozen=True)
class AiPlannerStepItem:
    """Single chronological build or research step in an AI-Planner rollout."""

    _ts_open_record: ClassVar[bool] = True

    seq: int = field(default=0, metadata=OPT)
    order: int = field(default=0, metadata=OPT)
    start_sec: float = field(default=0.0, metadata=OPT)
    duration_sec: float = field(default=0.0, metadata=OPT)
    finish_sec: float = field(default=0.0, metadata=OPT)
    start: str = field(default="", metadata=OPT)
    duration_text: str = field(default="", metadata=OPT)
    week: int = field(default=0, metadata=OPT)
    kind: str = field(default="", metadata=OPT)
    planet_id: int = field(default=0, metadata=OPT)
    planet_coords: str = field(default="", metadata=OPT)
    planet_name: str = field(default="", metadata=OPT)
    entity_key: str = field(default="", metadata=OPT)
    entity_name: str = field(default="", metadata=OPT)
    target_level: int = field(default=0, metadata=OPT)
    cost: ResourceBundle = field(default_factory=ResourceBundle, metadata=OPT)
    status: str = field(default="", metadata=OPT)
    needs_transport: bool = field(default=False, metadata=OPT)
    transport_request_at_sec: float | None = field(default=None, metadata=OPT)
    transport_request_text: str = field(default="", metadata=OPT)
    transport_travel_sec: float = field(default=0.0, metadata=OPT)
    transport_dispatch_delay_sec: float = field(default=0.0, metadata=OPT)
    transport_lead_sec: float = field(default=0.0, metadata=OPT)
    transport_sources: list[JsonDict] = field(default_factory=list, metadata=OPT)


@dataclass(frozen=True)
class AiPlannerSmeltingPlanet:
    """Per-planet Blast furnace (b2) initial state in AI-Planner smelting schedule."""

    planet_id: int
    coords: str
    name: str
    b2_level: int
    initial_pct: int
    initial_enabled: bool
    planet_index: int = field(default=0, metadata=OPT)
    rate_known: bool = field(default=False, metadata=OPT)


@dataclass(frozen=True)
class AiPlannerSmeltingToggleTarget:
    """Single planet Blast furnace percentage change event."""

    planet_id: int
    coords: str
    name: str
    b2_level: int
    from_pct: int
    to_pct: int


@dataclass(frozen=True)
class AiPlannerSmeltingEvent:
    """Scheduled Blast furnace enable/disable transition in AI-Planner."""

    seq: int
    time_hours: float
    time_sec: float
    time_text: str
    week: int
    action: Literal["enable", "disable"] | str
    enabled: bool
    target_pct: int
    from_mode: str
    from_enabled: bool
    is_initial: bool
    affected_planets: list[AiPlannerSmeltingToggleTarget] = field(default_factory=list, metadata=OPT)
    step_label: str | None = field(default=None, metadata=OPT)
    step_planet_coords: str | None = field(default=None, metadata=OPT)
    reason: str = field(default="", metadata=OPT)


@dataclass(frozen=True)
class AiPlannerSmeltingSpan:
    """Contiguous active smelting interval in AI-Planner."""

    start_hours: float
    end_hours: float
    start_sec: float
    end_sec: float
    duration_sec: float
    start_text: str
    end_text: str
    duration_text: str


@dataclass(frozen=True)
class AiPlannerSmeltingSummary:
    """Summary of planned Blast furnace smelting spans and toggles."""

    initial_live_mode: Literal["on", "off", "mixed", "none"] | str = field(default="", metadata=OPT)
    initial_live_any_enabled: bool = field(default=False, metadata=OPT)
    initial_live_all_enabled: bool = field(default=False, metadata=OPT)
    initial_live_enabled_count: int = field(default=0, metadata=OPT)
    initial_live_disabled_count: int = field(default=0, metadata=OPT)
    total_b2_planets: int = field(default=0, metadata=OPT)
    initial_target_enabled: bool = field(default=False, metadata=OPT)
    initial_target_pct: int = field(default=0, metadata=OPT)
    initial_toggle_needed: bool = field(default=False, metadata=OPT)
    initial_planets_needing_toggle: list[AiPlannerSmeltingToggleTarget] = field(default_factory=list, metadata=OPT)
    metal_smelted: float = field(default=0.0, metadata=OPT)
    smelting_hours_total: float = field(default=0.0, metadata=OPT)
    smelting_time_text: str = field(default="", metadata=OPT)
    smelting_toggles_count: int = field(default=0, metadata=OPT)
    smelting_spans_count: int = field(default=0, metadata=OPT)
    smelting_spans: list[AiPlannerSmeltingSpan] = field(default_factory=list, metadata=OPT)
    smelting_events: list[AiPlannerSmeltingEvent] = field(default_factory=list, metadata=OPT)
    planets: list[AiPlannerSmeltingPlanet] = field(default_factory=list, metadata=OPT)


@dataclass(frozen=True)
class AiPlannerColonyDiag:
    """Per-colony adaptive execution diagnostic row."""

    planet_id: int
    coords: str
    name: str
    state: str
    reason: str
    idle_with_affordable_target_step: bool
    b2_level: int
    b2_pct: int
    energy_net: float
    b2_energy_margin_at_100: float
    next_label: str | None = field(default=None, metadata=OPT)


@dataclass(frozen=True)
class AiPlannerAdaptiveWindow24h:
    """Rolling 24-hour builder utilization summary."""

    bucket_count: int
    colony_samples: int
    idle_with_affordable_share_pct: float
    ready_not_started_share_pct: float
    state_shares_pct: dict[str, float]


@dataclass(frozen=True)
class AiPlannerTransportFleetStatus:
    """Plan-sized transport fleet utilization telemetry."""

    owned_parked_cargo: float
    in_flight_cargo: float
    total_fleet_cargo: float
    target_fleet_cargo: float
    planned_cargo_per_hour_24h: float
    realized_utilization: float
    delivered_cargo_24h: float = field(default=0.0, metadata=OPT)
    delivered_cargo_per_hour: float = field(default=0.0, metadata=OPT)
    delivered_cargo_per_hour_24h: float = field(default=0.0, metadata=OPT)
    delivered_flights_24h: int = field(default=0, metadata=OPT)
    delivered_vs_planned_pct: float | None = field(default=None, metadata=OPT)


@dataclass(frozen=True)
class AiPlannerAutoReplanStatus:
    """Drift-triggered automatic background replan status."""

    cooldown_seconds: float
    last_auto_replan_ts: float | None
    last_trigger_reason: str
    drift_ready: bool


@dataclass(frozen=True)
class AiPlannerAdaptiveStatus:
    """Plan v2 adaptive execution diagnostics payload."""

    enabled: bool
    active_builders: int
    total_colonies: int
    colonies: list[AiPlannerColonyDiag]
    counts: dict[str, int]
    idle_with_affordable_count: int
    ready_not_started_count: int
    window_24h: AiPlannerAdaptiveWindow24h | None = field(default=None, metadata=OPT)
    transport_fleet: AiPlannerTransportFleetStatus | None = field(default=None, metadata=OPT)
    auto_replan: AiPlannerAutoReplanStatus | None = field(default=None, metadata=OPT)


@dataclass(frozen=True)
class AiPlannerLiveBlastFurnace:
    """Live empire-wide Blast furnace toggle state."""

    mode: str = field(default="", metadata=OPT)
    any_enabled: bool = field(default=False, metadata=OPT)
    all_enabled: bool = field(default=False, metadata=OPT)
    enabled_count: int = field(default=0, metadata=OPT)
    disabled_count: int = field(default=0, metadata=OPT)
    total_b2_planets: int = field(default=0, metadata=OPT)
    planets: list[AiPlannerSmeltingPlanet] = field(default_factory=list, metadata=OPT)


@dataclass(frozen=True)
class AiPlannerStepsPage:
    """Paginated AI-Planner step slice."""

    items: list[AiPlannerStepItem] = field(default_factory=list, metadata=OPT)
    total: int = field(default=0, metadata=OPT)
    page: int = field(default=1, metadata=OPT)
    page_size: int = field(default=50, metadata=OPT)


@dataclass(frozen=True)
class AiPlannerGetPayload:
    """Response envelope for `ai_planner_get`."""

    _ts_open_record: ClassVar[bool] = True

    ok: bool = field(default=True, metadata=OPT)
    has_plan: bool = field(default=False, metadata=OPT)
    active_summary: JsonDict | None = field(default=None, metadata=OPT)
    meta_parameters: JsonDict | None = field(default=None, metadata=OPT)
    weekly_breakdown: list[JsonDict] = field(default_factory=list, metadata=OPT)
    attack_telemetry: JsonDict | None = field(default=None, metadata=OPT)
    in_progress: JsonDict | None = field(default=None, metadata=OPT)
    smelting: AiPlannerSmeltingSummary | None = field(default=None, metadata=OPT)
    live_blast_furnace: AiPlannerLiveBlastFurnace | None = field(default=None, metadata=OPT)
    transport_matrix: JsonDict | None = field(default=None, metadata=OPT)
    transport_demand: JsonDict | None = field(default=None, metadata=OPT)
    adaptive_status: AiPlannerAdaptiveStatus | None = field(default=None, metadata=OPT)
    search: JsonDict | None = field(default=None, metadata=OPT)
    graph: JsonDict | None = field(default=None, metadata=OPT)
    steps_page: AiPlannerStepsPage = field(default_factory=AiPlannerStepsPage, metadata=OPT)
    job: PlannerJobState | None = field(default=None, metadata=OPT)
    runs: list[JsonDict] = field(default_factory=list, metadata=OPT)
    colonies_owned: int = field(default=0, metadata=OPT)
    max_planets: int = field(default=0, metadata=OPT)
    live_ai_unlocked: bool = field(default=False, metadata=OPT)
    pending_field_conflicts: list[FieldConflictItem] = field(default_factory=list, metadata=OPT)
    default_params: JsonDict = field(default_factory=dict, metadata=OPT)
