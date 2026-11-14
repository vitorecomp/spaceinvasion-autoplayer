"""Typed API payload dataclasses for core empire state, queues, telemetry, and config."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Final, Literal

from spaceinvasion.runtime.api.payloads._meta import OPT, JsonDict

PageKey = Literal[
    "dashboard",
    "buildings",
    "research",
    "combat",
    "fleet-control",
    "sectors",
    "espionage",
    "colonies",
    "attacks",
    "recyclers",
    "operations",
    "action-graph",
    "ranking",
    "config",
]

ActionGraphTabKey = Literal[
    "heuristics",
    "config-optimizer",
    "ai-planner",
    "fleet-planner",
]

FeedbackKind = Literal["ok", "warn", "error", "info"]

PAGE_TITLES: Final[dict[PageKey, str]] = {
    "dashboard": "DASHBOARD // OVERVIEW",
    "buildings": "BUILDINGS // LEVEL ETAS",
    "research": "RESEARCH // LEVEL ETAS",
    "combat": "FLEET BUILD // SHIPYARD & SIM",
    "fleet-control": "FLEET CONTROL // CURRENT FLIGHTS",
    "sectors": "GALAXY // SCANNED SECTORS",
    "espionage": "ESPIONAGE // INTEL & ARCHIVE",
    "colonies": "COLONIES // 300+ GRADING",
    "attacks": "COMBAT // ATTACK RESULTS",
    "recyclers": "RECYCLERS // RUBBLE & INBOX",
    "operations": "OPERATIONS // PRIORITY QUEUE",
    "action-graph": "ACTION GRAPH // SEQUENCE & DAG",
    "ranking": "RANKING // EVOLUTION & AZ",
    "config": "STRATEGY // CONFIG BUCKETS",
}


@dataclass(frozen=True)
class ResourceBundle:
    """Four-resource bundle (`pig_iron`, `metal`, `kryptonite`, `spice`)."""

    _ts_open_record: ClassVar[bool] = True

    pig_iron: float = field(default=0.0, metadata=OPT)
    metal: float = field(default=0.0, metadata=OPT)
    kryptonite: float = field(default=0.0, metadata=OPT)
    spice: float = field(default=0.0, metadata=OPT)


@dataclass(frozen=True)
class PlanetEnergy:
    """Planetary energy balance snapshot."""

    produced: float | None = field(default=None, metadata=OPT)
    used: float | None = field(default=None, metadata=OPT)
    net: float | None = field(default=None, metadata=OPT)


@dataclass(frozen=True)
class PlanetData:
    """Serialized planet state inside `/state` and `/dashboard`."""

    _ts_open_record: ClassVar[bool] = True

    planet_id: int
    name: str = field(default="", metadata=OPT)
    game_name: str = field(default="", metadata=OPT)
    custom_name: str = field(default="", metadata=OPT)
    coords: str = field(default="", metadata=OPT)
    planet_type: str = field(default="", metadata=OPT)
    temperature: int | None = field(default=None, metadata=OPT)
    fields_used: int | None = field(default=None, metadata=OPT)
    fields_max: int | None = field(default=None, metadata=OPT)
    resources: ResourceBundle = field(default_factory=ResourceBundle, metadata=OPT)
    storage: ResourceBundle = field(default_factory=ResourceBundle, metadata=OPT)
    energy: PlanetEnergy = field(default_factory=PlanetEnergy, metadata=OPT)
    buildings: dict[str, int] = field(default_factory=dict, metadata=OPT)
    ships: dict[str, int] = field(default_factory=dict, metadata=OPT)
    defense: dict[str, int] = field(default_factory=dict, metadata=OPT)
    economy_rates: dict[str, float] = field(default_factory=dict, metadata=OPT)


@dataclass(frozen=True)
class QueueJobData:
    """Active building, research, shipyard, or fleet flight queue item."""

    _ts_open_record: ClassVar[bool] = True

    category: str = field(default="", metadata=OPT)
    name: str = field(default="", metadata=OPT)
    building_id: int | None = field(default=None, metadata=OPT)
    research_id: int | None = field(default=None, metadata=OPT)
    ship_id: int | None = field(default=None, metadata=OPT)
    target_level: int | None = field(default=None, metadata=OPT)
    quantity: int | None = field(default=None, metadata=OPT)
    remaining_text: str = field(default="", metadata=OPT)
    remaining_seconds: float | None = field(default=None, metadata=OPT)
    finish_ts: float | None = field(default=None, metadata=OPT)
    queue_slot: int | None = field(default=None, metadata=OPT)
    planet_id: int | None = field(default=None, metadata=OPT)
    planet_coords: str = field(default="", metadata=OPT)
    origin_coords: str = field(default="", metadata=OPT)
    detail: str = field(default="", metadata=OPT)
    returning: bool = field(default=False, metadata=OPT)
    cargo: ResourceBundle | None = field(default=None, metadata=OPT)
    expected_cargo: ResourceBundle | None = field(default=None, metadata=OPT)
    delivered_cargo: ResourceBundle | None = field(default=None, metadata=OPT)
    delivered: bool = field(default=False, metadata=OPT)


@dataclass(frozen=True)
class BuildingResourceRequestItem:
    """Inter-planet transport request funding an upcoming construction step."""

    _ts_open_record: ClassVar[bool] = True

    id: str = field(default="", metadata=OPT)
    planet_id: int | None = field(default=None, metadata=OPT)
    planet_coords: str = field(default="", metadata=OPT)
    planet_name: str = field(default="", metadata=OPT)
    target_building: str = field(default="", metadata=OPT)
    target_level: int | None = field(default=None, metadata=OPT)
    building_id: int | None = field(default=None, metadata=OPT)
    label: str = field(default="", metadata=OPT)
    status: Literal["requested", "planned", "in_flight"] | str = field(default="", metadata=OPT)
    resources: ResourceBundle | None = field(default=None, metadata=OPT)
    pig_iron: float = field(default=0.0, metadata=OPT)
    metal: float = field(default=0.0, metadata=OPT)
    kryptonite: float = field(default=0.0, metadata=OPT)
    spice: float = field(default=0.0, metadata=OPT)
    source_planet_id: int | None = field(default=None, metadata=OPT)
    source_coords: str = field(default="", metadata=OPT)
    source_name: str = field(default="", metadata=OPT)
    eta_seconds: float | None = field(default=None, metadata=OPT)
    finish_ts: float | None = field(default=None, metadata=OPT)
    reason: str = field(default="", metadata=OPT)


@dataclass(frozen=True)
class RenamePlanetTarget:
    """Nested planet summary inside `RenamePlanetResponse`."""

    _ts_open_record: ClassVar[bool] = True

    planet_id: int = field(default=0, metadata=OPT)
    coords: str = field(default="", metadata=OPT)
    name: str = field(default="", metadata=OPT)
    game_name: str = field(default="", metadata=OPT)
    custom_name: str = field(default="", metadata=OPT)


@dataclass(frozen=True)
class RenamePlanetResponse:
    """Response envelope for `rename_planet` command."""

    _ts_open_record: ClassVar[bool] = True

    ok: bool = field(default=True, metadata=OPT)
    command: str = field(default="", metadata=OPT)
    planet_id: int = field(default=0, metadata=OPT)
    name: str = field(default="", metadata=OPT)
    game_name: str = field(default="", metadata=OPT)
    cleared: bool = field(default=False, metadata=OPT)
    custom_planet_names: dict[str, str] = field(default_factory=dict, metadata=OPT)
    planet: RenamePlanetTarget = field(default_factory=RenamePlanetTarget, metadata=OPT)
    message: str = field(default="", metadata=OPT)


@dataclass(frozen=True)
class HourlyProductionPoint:
    """Single minute-bucketed empire hourly production telemetry sample."""

    _ts_open_record: ClassVar[bool] = True

    minute_bucket: int
    pig_iron_per_hour: float
    metal_per_hour: float
    kryptonite_per_hour: float
    spice_per_hour: float
    total_per_hour: float
    speq_per_hour: float
    recorded_at: float = field(default=0.0, metadata=OPT)
    planet_count: int = field(default=0, metadata=OPT)
    active_builders: int = field(default=0, metadata=OPT)


@dataclass(frozen=True)
class LootRecycleBucket:
    """Single hourly bucket of stolen raid loot and recycled debris."""

    _ts_open_record: ClassVar[bool] = True

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
    total: float = field(default=0.0, metadata=OPT)
    attack_count: int = field(default=0, metadata=OPT)
    recycle_count: int = field(default=0, metadata=OPT)
    updated_at: float = field(default=0.0, metadata=OPT)


@dataclass(frozen=True)
class LootRecycleSummary:
    """Window summary totals for raid plunder and recycler recovery."""

    _ts_open_record: ClassVar[bool] = True

    total_stolen: float
    total_recycled: float
    grand_total: float
    attack_count: int = field(default=0, metadata=OPT)
    recycle_count: int = field(default=0, metadata=OPT)
    window_hours: int = field(default=0, metadata=OPT)
    stolen_pig_iron: float = field(default=0.0, metadata=OPT)
    stolen_metal: float = field(default=0.0, metadata=OPT)
    stolen_kryptonite: float = field(default=0.0, metadata=OPT)
    stolen_spice: float = field(default=0.0, metadata=OPT)
    recycled_pig_iron: float = field(default=0.0, metadata=OPT)
    recycled_metal: float = field(default=0.0, metadata=OPT)
    recycled_kryptonite: float = field(default=0.0, metadata=OPT)
    recycled_spice: float = field(default=0.0, metadata=OPT)


@dataclass(frozen=True)
class PlannedActionData:
    """Single priority-ranked planned action emitted by the planner."""

    _ts_open_record: ClassVar[bool] = True

    priority: str = field(default="", metadata=OPT)
    kind: str = field(default="", metadata=OPT)
    planet_id: int = field(default=0, metadata=OPT)
    planet_coords: str = field(default="", metadata=OPT)
    planet_name: str = field(default="", metadata=OPT)
    target_id: int | None = field(default=None, metadata=OPT)
    target_name: str = field(default="", metadata=OPT)
    target_level: int | None = field(default=None, metadata=OPT)
    cost: ResourceBundle = field(default_factory=ResourceBundle, metadata=OPT)
    missing: ResourceBundle = field(default_factory=ResourceBundle, metadata=OPT)
    reason: str = field(default="", metadata=OPT)
    executable_now: bool = field(default=False, metadata=OPT)
    wait_hours: float | None = field(default=None, metadata=OPT)
    wait_seconds: float | None = field(default=None, metadata=OPT)
    wait_eta: str | None = field(default=None, metadata=OPT)
    duration_seconds: float | None = field(default=None, metadata=OPT)
    duration_text: str | None = field(default=None, metadata=OPT)
    total_hours: float | None = field(default=None, metadata=OPT)
    total_seconds: float | None = field(default=None, metadata=OPT)
    total_eta: str | None = field(default=None, metadata=OPT)
    eta_bottleneck: str | None = field(default=None, metadata=OPT)
    manual_queue_id: int | None = field(default=None, metadata=OPT)


@dataclass(frozen=True)
class StrategyConfigData:
    """Serialized `StrategyConfig` contract (50 surviving fields; excludes removed `ga_nn` keys)."""

    _ts_open_record: ClassVar[bool] = True

    planner_mode: Literal["priority", "ai_planner"] | str = field(default="priority", metadata=OPT)
    ai_adaptive_execution: bool = field(default=False, metadata=OPT)
    playstyle: Literal["fleeter", "hybrid", "miner"] | str = field(default="fleeter", metadata=OPT)
    merchant_mode: bool = field(default=False, metadata=OPT)
    auto_throttle_blast_furnace: bool = field(default=True, metadata=OPT)
    allow_research: bool = field(default=True, metadata=OPT)
    allow_fleet_research: bool = field(default=True, metadata=OPT)
    allow_shipyard: bool = field(default=True, metadata=OPT)
    allow_multi_planet: bool = field(default=True, metadata=OPT)
    allow_fleetsave: bool = field(default=False, metadata=OPT)
    allow_galaxy_scan: bool = field(default=False, metadata=OPT)
    galaxy_scan_block_other_actions: bool = field(default=True, metadata=OPT)
    allow_raids: bool = field(default=False, metadata=OPT)
    allow_pvp_in_az: bool = field(default=False, metadata=OPT)
    allow_colonize: bool = field(default=False, metadata=OPT)
    allow_transport: bool = field(default=False, metadata=OPT)
    allow_transmitter: bool = field(default=False, metadata=OPT)
    forbid_planetary_solar: bool = field(default=False, metadata=OPT)
    storage_warn_ratio: float = field(default=0.85, metadata=OPT)
    blast_pig_iron_max_share: float = field(default=0.70, metadata=OPT)
    combat_safety_margin: float = field(default=1.4, metadata=OPT)
    raid_min_loot_spice_eq: float = field(default=1500.0, metadata=OPT)
    attack_min_res_per_hour: float = field(default=0.0, metadata=OPT)
    recycle_min_res_per_hour: float = field(default=0.0, metadata=OPT)
    fleet_save_fuel_reserve_spice: int = field(default=500, metadata=OPT)
    action_batch_interval_seconds: int = field(default=120, metadata=OPT)
    max_actions_per_hour: int = field(default=60, metadata=OPT)
    page_view_limit_per_minute: int = field(default=60, metadata=OPT)
    hot_planet_solar_temp_threshold: int = field(default=40, metadata=OPT)
    min_free_fields_warning: int = field(default=20, metadata=OPT)
    min_colony_fields: int = field(default=300, metadata=OPT)
    max_planets: int = field(default=11, metadata=OPT)
    galaxy_sweep_interval_seconds: int = field(default=270, metadata=OPT)
    galaxy_sweep_pages: int = field(default=100, metadata=OPT)
    galaxy_scan_click_delay_seconds: float = field(default=1.5, metadata=OPT)
    galaxy_scan_click_jitter_seconds: float = field(default=0.6, metadata=OPT)
    galaxy_scan_explore_first: bool = field(default=True, metadata=OPT)
    galaxy_scan_refresh_pct: int = field(default=0, metadata=OPT)
    galaxy_scan_all_galaxies: bool = field(default=False, metadata=OPT)
    galaxy_scan_near_weight: float = field(default=2.0, metadata=OPT)
    galaxy_scan_attack_weight: float = field(default=3.0, metadata=OPT)
    reserved_spy_slots: int = field(default=1, metadata=OPT)
    spy_reserved_slots_only: bool = field(default=False, metadata=OPT)
    spy_dispatch_planet_id: int = field(default=0, metadata=OPT)
    ai_resource_spare_pct: float = field(default=10.0, metadata=OPT)
    ga_population_size: int = field(default=24, metadata=OPT)
    ga_generations: int = field(default=12, metadata=OPT)
    ga_mutation_rate: float = field(default=0.25, metadata=OPT)
    shipyard_targets: dict[str, int] = field(default_factory=dict, metadata=OPT)
    fleet_research_targets: dict[str, int] = field(default_factory=dict, metadata=OPT)


@dataclass(frozen=True)
class NotificationItem:
    """Single operator notification event."""

    _ts_open_record: ClassVar[bool] = True

    id: int
    notification_code: str = field(default="", metadata=OPT)
    status: Literal["unread", "read", "dismissed"] | str = field(default="", metadata=OPT)
    severity: Literal["error", "warning", "info"] | str = field(default="", metadata=OPT)
    category: str = field(default="", metadata=OPT)
    title: str = field(default="", metadata=OPT)
    message: str = field(default="", metadata=OPT)
    help_prompt: str = field(default="", metadata=OPT)
    action_kind: str = field(default="", metadata=OPT)
    planet_coords: str = field(default="", metadata=OPT)
    target_name: str = field(default="", metadata=OPT)
    occurrence_count: int = field(default=0, metadata=OPT)
    created_ts: float = field(default=0.0, metadata=OPT)
    updated_ts: float = field(default=0.0, metadata=OPT)
    details: JsonDict = field(default_factory=dict, metadata=OPT)


@dataclass(frozen=True)
class NotificationsState:
    """Operator notification inbox state."""

    unread_count: int
    items: list[NotificationItem]


@dataclass(frozen=True)
class FeedbackState:
    """Operator Console toast feedback state."""

    message: str
    kind: FeedbackKind
    hidden: bool
