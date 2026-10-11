"""Typed frozen DTO dataclasses returned by `spaceinvasion.db` repositories and queries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationRecord:
    """Typed UI notification row (`ui_notifications`)."""

    id: int
    notification_code: str
    created_ts: float
    updated_ts: float
    severity: str
    category: str
    title: str
    message: str
    help_prompt: str
    action_kind: str
    planet_coords: str
    target_name: str
    dedupe_key: str
    occurrence_count: int
    status: str
    acknowledged_ts: float | None
    details_json: str


@dataclass(frozen=True)
class ManualQueueRecord:
    """Typed operator manual command queue row (`manual_queue`)."""

    id: int
    created_ts: float
    updated_ts: float
    status: str
    priority: str
    sort_rank: int
    kind: str
    planet_id: int
    planet_coords: str
    target_id: int | None
    target_name: str
    target_coords: str
    target_level: int | None
    note: str
    completed_ts: float | None
    canceled_ts: float | None
    result_message: str


@dataclass(frozen=True)
class ManualQueueEventRecord:
    """Typed append-only queue lifecycle event row (`manual_queue_events`)."""

    id: int
    ts: float
    queue_id: int
    event_type: str
    details_json: str


@dataclass(frozen=True)
class MetaRecord:
    """Typed key-value metadata row (`meta`)."""

    key: str
    value: str
    updated_ts: float


@dataclass(frozen=True)
class StrategyConfigRecord:
    """Typed singleton strategy config snapshot (`strategy_config_store`)."""

    id: int
    config_json: str
    updated_ts: float
    source: str


@dataclass(frozen=True)
class ConfigHistoryRecord:
    """Typed append-only config revision row (`config_history`)."""

    id: int
    ts: float
    config_json: str


@dataclass(frozen=True)
class SchemaMigrationRecord:
    """Typed legacy schema migration ledger row (`schema_migrations`)."""

    version: int
    version_tag: str
    name: str
    description: str
    checksum: str
    applied_ts: float
    duration_ms: float
    app_schema_version: str
    details_json: str


@dataclass(frozen=True)
class SchemaMigrationEventRecord:
    """Typed append-only migration audit event row (`schema_migration_events`)."""

    id: int
    ts: float
    event_type: str
    from_version: int
    to_version: int
    version: int | None
    name: str
    checksum: str
    backup_path: str
    details_json: str


@dataclass(frozen=True)
class ConfigOptimizerRunRecord:
    """Typed config-optimizer history row (`config_optimizer_runs`)."""

    id: int
    created_ts: float
    trigger_source: str
    elapsed_ms: float
    target_ship_key: str
    ships_per_month: float
    limiting_factor: str
    params_json: str
    reserved_floors_json: str
    result_json: str


@dataclass(frozen=True)
class ConfigOptimizerActiveRecord:
    """Typed active config-optimizer singleton (`config_optimizer_active`)."""

    singleton_id: int
    run_id: int | None
    updated_ts: float
    params_json: str
    reserved_floors_json: str
    result_json: str


@dataclass(frozen=True)
class AiPlannerRunRecord:
    """Typed AI-planner history row (`ai_planner_runs`)."""

    id: int
    created_ts: float
    config_run_id: int | None
    trigger_source: str
    elapsed_ms: float
    reward_weeks: int
    weekly_discount: float
    discounted_reward_spice_eq: float
    total_net_spice_eq: float
    actions_count: int
    params_json: str
    summary_json: str
    result_json: str


@dataclass(frozen=True)
class AiPlannerActiveRecord:
    """Typed active AI-planner singleton (`ai_planner_active`)."""

    singleton_id: int
    run_id: int | None
    config_run_id: int | None
    updated_ts: float
    params_json: str
    summary_json: str
    result_json: str


@dataclass(frozen=True)
class FieldConflictPromptRecord:
    """Typed planet field-limit conflict prompt (`field_conflict_prompts`)."""

    conflict_key: str
    planet_id: int
    planet_coords: str
    blocked_building_key: str
    blocked_target_level: int
    candidate_building_key: str
    candidate_level: int
    status: str
    decision: str
    created_ts: float
    resolved_ts: float | None
    detail_json: str


@dataclass(frozen=True)
class FieldConflictEventRecord:
    """Typed field-conflict audit event (`field_conflict_events`)."""

    id: int
    created_ts: float
    conflict_key: str
    event_type: str
    decision: str
    detail_json: str


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
