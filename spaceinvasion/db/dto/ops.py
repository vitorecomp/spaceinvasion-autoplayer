"""Frozen operational DTO dataclasses for notifications, queue, settings, and planner runs."""

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


__all__ = [
    "AiPlannerActiveRecord",
    "AiPlannerRunRecord",
    "ConfigHistoryRecord",
    "ConfigOptimizerActiveRecord",
    "ConfigOptimizerRunRecord",
    "FieldConflictEventRecord",
    "FieldConflictPromptRecord",
    "ManualQueueEventRecord",
    "ManualQueueRecord",
    "MetaRecord",
    "NotificationRecord",
    "SchemaMigrationEventRecord",
    "SchemaMigrationRecord",
    "StrategyConfigRecord",
]
