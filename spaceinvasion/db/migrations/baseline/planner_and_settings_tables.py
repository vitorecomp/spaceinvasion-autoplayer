"""Frozen `0001` baseline DDL for planner runs, field conflicts, telemetry, and settings."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from spaceinvasion.db.migrations.baseline._cols import bi, dbl, id_pk, txt

PLANNER_AND_SETTINGS_TABLE_NAMES: tuple[str, ...] = (
    "config_optimizer_runs",
    "config_optimizer_active",
    "ai_planner_runs",
    "ai_planner_active",
    "field_conflict_prompts",
    "field_conflict_events",
    "action_history",
    "hourly_production_history",
    "meta",
    "strategy_config_store",
    "config_history",
    "schema_migrations",
    "schema_migration_events",
)


def _create_planner_tables() -> None:
    op.create_table(
        "config_optimizer_runs",
        id_pk(),
        dbl("created_ts"),
        txt("trigger_source"),
        dbl("elapsed_ms", default="0"),
        txt("target_ship_key", default="'s16'"),
        dbl("ships_per_month", default="0"),
        txt("limiting_factor", default="''"),
        txt("params_json"),
        txt("reserved_floors_json", default="'{}'"),
        txt("result_json"),
    )
    op.create_index("idx_config_optimizer_runs_ts", "config_optimizer_runs", ["created_ts"])

    op.create_table(
        "config_optimizer_active",
        bi("singleton_id", pk=True),
        bi("run_id", nullable=True),
        dbl("updated_ts"),
        txt("params_json"),
        txt("reserved_floors_json", default="'{}'"),
        txt("result_json"),
        sa.CheckConstraint("singleton_id = 1", name="config_optimizer_active_singleton_id_check"),
    )

    op.create_table(
        "ai_planner_runs",
        id_pk(),
        dbl("created_ts"),
        bi("config_run_id", nullable=True),
        txt("trigger_source"),
        dbl("elapsed_ms", default="0"),
        bi("reward_weeks", default="26"),
        dbl("weekly_discount", default="0.9"),
        dbl("discounted_reward_spice_eq", default="0"),
        dbl("total_net_spice_eq", default="0"),
        bi("actions_count", default="0"),
        txt("params_json"),
        txt("summary_json"),
        txt("result_json"),
    )
    op.create_index("idx_ai_planner_runs_cfg", "ai_planner_runs", ["config_run_id", "created_ts"])
    op.create_index("idx_ai_planner_runs_ts", "ai_planner_runs", ["created_ts"])

    op.create_table(
        "ai_planner_active",
        bi("singleton_id", pk=True),
        bi("run_id", nullable=True),
        bi("config_run_id", nullable=True),
        dbl("updated_ts"),
        txt("params_json"),
        txt("summary_json"),
        txt("result_json"),
        sa.CheckConstraint("singleton_id = 1", name="ai_planner_active_singleton_id_check"),
    )

    op.create_table(
        "field_conflict_prompts",
        txt("conflict_key", pk=True),
        bi("planet_id"),
        txt("planet_coords"),
        txt("blocked_building_key"),
        bi("blocked_target_level"),
        txt("candidate_building_key"),
        bi("candidate_level"),
        txt("status", default="'pending'"),
        txt("decision", default="''"),
        dbl("created_ts"),
        dbl("resolved_ts", nullable=True),
        txt("detail_json", default="'{}'"),
    )
    op.create_index("idx_field_conflict_prompts_planet", "field_conflict_prompts", ["planet_id", "status"])
    op.create_index("idx_field_conflict_prompts_status_ts", "field_conflict_prompts", ["status", "created_ts"])

    op.create_table(
        "field_conflict_events",
        id_pk(),
        dbl("created_ts"),
        txt("conflict_key"),
        txt("event_type"),
        txt("decision", default="''"),
        txt("detail_json", default="'{}'"),
    )
    op.create_index("idx_field_conflict_events_key_ts", "field_conflict_events", ["conflict_key", "created_ts"])
    op.create_index("idx_field_conflict_events_ts", "field_conflict_events", ["created_ts"])


def _create_telemetry_and_settings_tables() -> None:
    op.create_table(
        "action_history",
        id_pk(),
        dbl("ts"),
        txt("source"),
        txt("command"),
        txt("priority", nullable=True),
        txt("kind"),
        bi("planet_id", nullable=True),
        txt("planet_coords", nullable=True),
        bi("target_id", nullable=True),
        txt("target_name", nullable=True),
        bi("target_level", nullable=True),
        bi("executed"),
        bi("dry_run"),
        txt("reason", nullable=True),
        txt("message", nullable=True),
    )
    op.create_index("idx_action_history_ts", "action_history", ["ts"])

    op.create_table(
        "hourly_production_history",
        id_pk(),
        bi("minute_bucket", unique=True),
        dbl("recorded_at"),
        dbl("pig_iron_per_hour", default="0"),
        dbl("metal_per_hour", default="0"),
        dbl("kryptonite_per_hour", default="0"),
        dbl("spice_per_hour", default="0"),
        dbl("total_per_hour", default="0"),
        dbl("speq_per_hour", default="0"),
        bi("planet_count", default="0"),
        bi("active_builders", default="0"),
    )
    op.create_index(
        "idx_hourly_production_minute",
        "hourly_production_history",
        [sa.desc(sa.column("minute_bucket"))],
    )

    op.create_table(
        "meta",
        txt("key", pk=True),
        txt("value"),
        dbl("updated_ts"),
    )

    op.create_table(
        "strategy_config_store",
        bi("id", pk=True),
        txt("config_json"),
        dbl("updated_ts"),
        txt("source", default="'sqlite'"),
        sa.CheckConstraint("id = 1", name="strategy_config_store_id_check"),
    )

    op.create_table(
        "config_history",
        id_pk(),
        dbl("ts"),
        txt("config_json"),
    )
    op.create_index("idx_config_history_ts", "config_history", ["ts"])

    op.create_table(
        "schema_migrations",
        bi("version", pk=True),
        txt("version_tag"),
        txt("name"),
        txt("description", default="''"),
        txt("checksum"),
        dbl("applied_ts"),
        dbl("duration_ms", default="0.0"),
        txt("app_schema_version", default="''"),
        txt("details_json", default="'{}'"),
    )

    op.create_table(
        "schema_migration_events",
        id_pk(),
        dbl("ts"),
        txt("event_type"),
        bi("from_version", default="0"),
        bi("to_version", default="0"),
        bi("version", nullable=True),
        txt("name", default="''"),
        txt("checksum", default="''"),
        txt("backup_path", default="''"),
        txt("details_json", default="'{}'"),
    )
    op.create_index("idx_schema_migration_events_ts", "schema_migration_events", ["ts"])


def create_planner_and_settings_tables() -> None:
    """Create the 13 frozen baseline planner, telemetry, settings, and ledger tables."""
    _create_planner_tables()
    _create_telemetry_and_settings_tables()


__all__ = ["PLANNER_AND_SETTINGS_TABLE_NAMES", "create_planner_and_settings_tables"]
