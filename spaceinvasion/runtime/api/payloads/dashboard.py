"""Typed API payload dataclasses for `/health`, `/state`, `/dashboard`, and exported settings schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal

from spaceinvasion.runtime.api.payloads._meta import OPT, JsonDict
from spaceinvasion.runtime.api.payloads.intel import UniverseResearchMaxRow
from spaceinvasion.runtime.api.payloads.planners import FieldConflictItem, PlannerJobsStatusPayload
from spaceinvasion.runtime.api.payloads.state import (
    BuildingResourceRequestItem,
    HourlyProductionPoint,
    LootRecycleBucket,
    LootRecycleSummary,
    NotificationsState,
    PlanetData,
    PlannedActionData,
    QueueJobData,
    StrategyConfigData,
)


@dataclass(frozen=True)
class HealthPayload:
    """Read-only bridge health & pacing status."""

    _ts_open_record: ClassVar[bool] = True

    ok: bool = field(default=True, metadata=OPT)
    in_game: bool = field(default=False, metadata=OPT)
    debug_port_open: bool = field(default=False, metadata=OPT)
    debug_port: int = field(default=9222, metadata=OPT)
    rate_limited: bool = field(default=False, metadata=OPT)
    retry_after: float = field(default=0.0, metadata=OPT)
    action_batch_interval_seconds: int = field(default=120, metadata=OPT)
    remaining_hourly_actions: int = field(default=60, metadata=OPT)


@dataclass(frozen=True)
class BotControlPayload:
    """Automation pause, mode override, and CAPTCHA gate state."""

    _ts_open_record: ClassVar[bool] = True

    paused: bool = field(default=False, metadata=OPT)
    mode_override: str | None = field(default=None, metadata=OPT)
    captcha_blocked: bool = field(default=False, metadata=OPT)
    captcha_message: str | None = field(default=None, metadata=OPT)


@dataclass(frozen=True)
class FleetAlertItem:
    """Incoming or active fleet alert indicator."""

    _ts_open_record: ClassVar[bool] = True

    hostile: bool = field(default=False, metadata=OPT)


@dataclass(frozen=True)
class TutorialTaskItem:
    """Single tutorial objective row."""

    building_name: str = field(default="", metadata=OPT)
    description: str = field(default="", metadata=OPT)
    current: int = field(default=0, metadata=OPT)
    target: int = field(default=0, metadata=OPT)


@dataclass(frozen=True)
class TutorialState:
    """Active tutorial quest progress."""

    step: int = field(default=0, metadata=OPT)
    total_steps: int = field(default=0, metadata=OPT)
    reward_text: str = field(default="", metadata=OPT)
    can_complete: bool = field(default=False, metadata=OPT)
    tasks: list[TutorialTaskItem] = field(default_factory=list, metadata=OPT)


@dataclass(frozen=True)
class GameStatePayload:
    """Serialized `GameState` embedded inside `/state` and `/dashboard`."""

    _ts_open_record: ClassVar[bool] = True

    universe_id: int = field(default=0, metadata=OPT)
    user_id: int = field(default=0, metadata=OPT)
    nickname: str = field(default="", metadata=OPT)
    timestamp: float = field(default=0.0, metadata=OPT)
    speedup_factor: float = field(default=1.0, metadata=OPT)
    active_planet_id: int = field(default=0, metadata=OPT)
    planets: list[PlanetData] = field(default_factory=list, metadata=OPT)
    researches: dict[str, int] = field(default_factory=dict, metadata=OPT)
    research_names: dict[str, str] = field(default_factory=dict, metadata=OPT)
    active_queues: list[QueueJobData] = field(default_factory=list, metadata=OPT)
    fleet_alerts: list[FleetAlertItem] = field(default_factory=list, metadata=OPT)
    tutorial: TutorialState | None = field(default=None, metadata=OPT)


@dataclass(frozen=True)
class PlanetGoalEntry:
    """Target building levels and specialization for a single colony."""

    planet_id: int = field(default=0, metadata=OPT)
    coords: str = field(default="", metadata=OPT)
    specialization: str = field(default="", metadata=OPT)
    goals: dict[str, int] = field(default_factory=dict, metadata=OPT)


@dataclass(frozen=True)
class ConfigOptimizerGoalsPayload:
    """Indexed per-planet building goals from the active Config Optimizer run."""

    run_id: int | None = field(default=None, metadata=OPT)
    updated_ts: float = field(default=0.0, metadata=OPT)
    by_planet_id: dict[str, PlanetGoalEntry] = field(default_factory=dict, metadata=OPT)
    by_coords: dict[str, PlanetGoalEntry] = field(default_factory=dict, metadata=OPT)


@dataclass(frozen=True)
class PodmanStatusPayload:
    """Optional container runtime status for PostgreSQL backend."""

    runtime: str = field(default="", metadata=OPT)
    container_name: str = field(default="", metadata=OPT)
    container_running: bool = field(default=False, metadata=OPT)
    container_status: str = field(default="", metadata=OPT)


@dataclass(frozen=True)
class DbBackendPayload:
    """Database backend health and target diagnostic payload."""

    _ts_open_record: ClassVar[bool] = True

    backend: Literal["postgres", "sqlite"] | str = field(default="postgres", metadata=OPT)
    target: str = field(default="", metadata=OPT)
    selection_source: str = field(default="", metadata=OPT)
    container_mode: bool = field(default=False, metadata=OPT)
    postgres_target: str = field(default="", metadata=OPT)
    sqlite_target: str = field(default="", metadata=OPT)
    sqlite_exists: bool = field(default=False, metadata=OPT)
    sqlite_size_bytes: int = field(default=0, metadata=OPT)
    postgres_reachable: bool = field(default=False, metadata=OPT)
    postgres_latency_ms: float = field(default=0.0, metadata=OPT)
    postgres_server: str = field(default="", metadata=OPT)
    podman: PodmanStatusPayload = field(default_factory=PodmanStatusPayload, metadata=OPT)


@dataclass(frozen=True)
class GalaxySweepPayload:
    """Live 2-phase galaxy sweep cursor and progress counters."""

    _ts_open_record: ClassVar[bool] = True

    cursor_galaxy: int = field(default=1, metadata=OPT)
    cursor_system: int = field(default=1, metadata=OPT)
    scan_phase: str = field(default="", metadata=OPT)
    selection_mode: str = field(default="", metadata=OPT)
    explored_systems: int = field(default=0, metadata=OPT)
    unscanned_systems: int = field(default=0, metadata=OPT)
    total_systems: int = field(default=0, metadata=OPT)
    coverage_pct: float = field(default=0.0, metadata=OPT)
    universe_explored_systems: int = field(default=0, metadata=OPT)
    universe_total_systems: int = field(default=0, metadata=OPT)
    universe_coverage_pct: float = field(default=0.0, metadata=OPT)
    refresh_pct: int = field(default=0, metadata=OPT)
    discovery_pct: int = field(default=0, metadata=OPT)
    exploratory_count: int = field(default=0, metadata=OPT)
    refresh_count: int = field(default=0, metadata=OPT)
    scan_galaxies: list[int] = field(default_factory=list, metadata=OPT)
    in_progress: bool = field(default=False, metadata=OPT)


@dataclass(frozen=True)
class DashboardPayload:
    """Primary `/dashboard` aggregate envelope served to the Operator Console."""

    _ts_open_record: ClassVar[bool] = True

    ok: bool = field(default=True, metadata=OPT)
    code_version: str | None = field(default=None, metadata=OPT)
    css_version: str | None = field(default=None, metadata=OPT)
    server_boot_id: str | None = field(default=None, metadata=OPT)
    planner_mode: str = field(default="priority", metadata=OPT)
    effective_planner_mode: str = field(default="priority", metadata=OPT)
    fallback_reason: str | None = field(default=None, metadata=OPT)
    colonies_owned: int = field(default=0, metadata=OPT)
    max_planets: int = field(default=11, metadata=OPT)
    live_ai_unlocked: bool = field(default=False, metadata=OPT)
    pending_field_conflicts: list[FieldConflictItem] = field(default_factory=list, metadata=OPT)
    planner_jobs_status: PlannerJobsStatusPayload | None = field(default=None, metadata=OPT)
    planner_status: PlannerJobsStatusPayload | None = field(default=None, metadata=OPT)
    ai_planner_summary: JsonDict | None = field(default=None, metadata=OPT)
    health: HealthPayload = field(default_factory=HealthPayload, metadata=OPT)
    bot_control: BotControlPayload = field(default_factory=BotControlPayload, metadata=OPT)
    state: GameStatePayload = field(default_factory=GameStatePayload, metadata=OPT)
    config: StrategyConfigData = field(default_factory=StrategyConfigData, metadata=OPT)
    plan: list[PlannedActionData] = field(default_factory=list, metadata=OPT)
    building_resource_requests: list[BuildingResourceRequestItem] = field(default_factory=list, metadata=OPT)
    custom_planet_names: dict[str, str] = field(default_factory=dict, metadata=OPT)
    hourly_production_history: list[HourlyProductionPoint] = field(default_factory=list, metadata=OPT)
    current_hourly_production: HourlyProductionPoint | None = field(default=None, metadata=OPT)
    loot_recycle_history: list[LootRecycleBucket] = field(default_factory=list, metadata=OPT)
    loot_recycle_summary: LootRecycleSummary | None = field(default=None, metadata=OPT)
    manual_queue: list[JsonDict] = field(default_factory=list, metadata=OPT)
    buildings_eta: JsonDict | None = field(default=None, metadata=OPT)
    planet_economies: dict[str, JsonDict] | None = field(default=None, metadata=OPT)
    combat: JsonDict | None = field(default=None, metadata=OPT)
    ranking: JsonDict | None = field(default=None, metadata=OPT)
    action_graph: JsonDict | None = field(default=None, metadata=OPT)
    optimizer_summary: JsonDict | None = field(default=None, metadata=OPT)
    config_optimizer_goals: ConfigOptimizerGoalsPayload | None = field(default=None, metadata=OPT)
    novnc_url: str | None = field(default=None, metadata=OPT)
    db_summary: JsonDict | None = field(default=None, metadata=OPT)
    db_backend: DbBackendPayload | None = field(default=None, metadata=OPT)
    galaxy_sweep: GalaxySweepPayload | None = field(default=None, metadata=OPT)
    universe_research_max: list[UniverseResearchMaxRow] = field(default_factory=list, metadata=OPT)
    notifications: NotificationsState = field(
        default_factory=lambda: NotificationsState(unread_count=0, items=[]),
        metadata=OPT,
    )
    delta: bool = field(default=False, metadata=OPT)
    snapshot_generation: int | None = field(default=None, metadata=OPT)
    published_at: float | None = field(default=None, metadata=OPT)
    section_versions: dict[str, str] = field(default_factory=dict, metadata=OPT)
    unchanged_sections: list[str] = field(default_factory=list, metadata=OPT)
    hourly_production_mode: Literal["full", "delta"] | str | None = field(default=None, metadata=OPT)
    loot_recycle_mode: Literal["full", "delta"] | str | None = field(default=None, metadata=OPT)
    loot_recycle_hourly: list[LootRecycleBucket] = field(default_factory=list, metadata=OPT)
    latest_minute_bucket: int | None = field(default=None, metadata=OPT)
    latest_loot_hour: int | None = field(default=None, metadata=OPT)
    ui_meta: JsonDict | None = field(default=None, metadata=OPT)


@dataclass(frozen=True)
class SettingOptionPayload:
    """Exported select-option metadata for a single enum setting."""

    value: str
    label: str


@dataclass(frozen=True)
class SettingItemSchemaPayload:
    """Exported Console `ConfigItemSpec` metadata for a single setting."""

    key: str
    id: str
    title: str
    boundsLabel: str
    description: str
    control: Literal["select", "number", "checkbox", "planet-select", "map"] | str
    defaultValue: str | float | bool | dict[str, int] | None
    options: list[SettingOptionPayload] = field(default_factory=list, metadata=OPT)
    min: float = field(default=0.0, metadata=OPT)
    max: float = field(default=0.0, metadata=OPT)
    step: float = field(default=1.0, metadata=OPT)
    unit: str = field(default="", metadata=OPT)


@dataclass(frozen=True)
class SettingGroupSchemaPayload:
    """Exported Console `ConfigGroupSpec` metadata."""

    id: str
    headingId: str
    title: str
    subtitle: str
    items: list[SettingItemSchemaPayload]


@dataclass(frozen=True)
class SettingsSchemaPayload:
    """Canonical Python-exported settings schema envelope."""

    schema_version: int
    planner_modes: list[str]
    removed_keys: list[str]
    groups: list[SettingGroupSchemaPayload]
    all_keys: list[str]
    bool_keys: list[str]
    number_defaults: dict[str, float]
