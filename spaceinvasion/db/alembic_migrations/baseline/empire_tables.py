"""Frozen `0001` baseline DDL for empire snapshots, queue, and UI notification tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from spaceinvasion.db.alembic_migrations.baseline._cols import bi, dbl, id_pk, txt

EMPIRE_TABLE_NAMES: tuple[str, ...] = (
    "state_snapshots",
    "latest_state_hot",
    "planet_snapshots",
    "empire_planets_hot",
    "rank_snapshots",
    "economy_rates_cache",
    "tutorial_progress",
    "manual_queue",
    "manual_queue_events",
    "ui_notifications",
)


def _create_state_snapshot_tables() -> None:
    op.create_table(
        "state_snapshots",
        id_pk(),
        dbl("ts"),
        txt("source", default="'advisor'"),
        bi("universe_id", default="0"),
        bi("user_id", default="0"),
        txt("nickname", default="''"),
        bi("active_planet_id", default="0"),
        bi("planet_count", default="0"),
        bi("planned_action_count", default="0"),
        txt("state_json", default="'{}'"),
        txt("plan_json", default="'[]'"),
    )
    op.create_index("idx_state_snapshots_ts", "state_snapshots", ["ts"])

    op.create_table(
        "latest_state_hot",
        bi("id", pk=True),
        bi("source_snapshot_id", default="0"),
        dbl("ts", default="0.0"),
        txt("source", default="'advisor'"),
        bi("universe_id", default="0"),
        bi("user_id", default="0"),
        txt("nickname", default="''"),
        bi("active_planet_id", default="0"),
        bi("planet_count", default="0"),
        bi("planned_action_count", default="0"),
        txt("state_json", default="'{}'"),
        txt("plan_json", default="'[]'"),
        dbl("updated_ts", default="0.0"),
        sa.CheckConstraint("id = 1", name="latest_state_hot_id_check"),
    )

    op.create_table(
        "planet_snapshots",
        id_pk(),
        dbl("ts"),
        bi("universe_id", default="0"),
        bi("planet_id"),
        txt("coords"),
        txt("name"),
        txt("planet_type"),
        bi("temperature"),
        bi("fields_used"),
        bi("fields_max"),
        bi("pig_iron"),
        bi("metal"),
        bi("kryptonite"),
        bi("spice"),
        bi("energy_produced"),
        bi("energy_used"),
        txt("buildings_json"),
        txt("ships_json"),
        txt("defense_json"),
        txt("economy_rates_json"),
        txt("researches_json"),
    )
    op.create_index("idx_planet_snapshots_pid_ts", "planet_snapshots", ["planet_id", "ts"])


def _create_planet_hot_and_rank_tables() -> None:
    op.create_table(
        "empire_planets_hot",
        bi("planet_id", pk=True),
        bi("universe_id", default="0"),
        txt("coords", default="''"),
        txt("name", default="''"),
        txt("planet_type", default="'normal'"),
        bi("temperature", default="0"),
        bi("fields_used", default="0"),
        bi("fields_max", default="0"),
        bi("pig_iron", default="0"),
        bi("metal", default="0"),
        bi("kryptonite", default="0"),
        bi("spice", default="0"),
        bi("energy_produced", default="0"),
        bi("energy_used", default="0"),
        bi("is_active", default="1"),
        dbl("updated_ts", default="0.0"),
    )
    op.create_index("idx_empire_planets_hot_coords", "empire_planets_hot", ["coords", "is_active"])

    op.create_table(
        "rank_snapshots",
        id_pk(),
        dbl("ts"),
        bi("universe_id", default="0"),
        txt("nickname", default="''"),
        bi("server_points", nullable=True),
        bi("server_rank", nullable=True),
        dbl("estimated_points"),
        dbl("pts_per_hr"),
        dbl("spice_value"),
        bi("planets"),
    )
    op.create_index("idx_rank_snapshots_ts", "rank_snapshots", ["ts"])

    op.create_table(
        "economy_rates_cache",
        bi("planet_id", pk=True),
        txt("coords"),
        txt("rates_json"),
        dbl("updated_ts"),
    )

    op.create_table(
        "tutorial_progress",
        bi("step", pk=True),
        bi("total_steps"),
        txt("tasks_json"),
        txt("reward_text"),
        bi("can_complete"),
        dbl("first_seen_ts"),
        dbl("last_seen_ts"),
        dbl("completed_ts", nullable=True),
    )


def _create_queue_and_notification_tables() -> None:
    op.create_table(
        "manual_queue",
        id_pk(),
        dbl("created_ts"),
        dbl("updated_ts"),
        txt("status", default="'pending'"),
        txt("priority", default="'P1'"),
        bi("sort_rank", default="1000"),
        txt("kind"),
        bi("planet_id", default="0"),
        txt("planet_coords", default="''"),
        bi("target_id", nullable=True),
        txt("target_name", default="''"),
        txt("target_coords", default="''"),
        bi("target_level", nullable=True),
        txt("note", default="''"),
        dbl("completed_ts", nullable=True),
        dbl("canceled_ts", nullable=True),
        txt("result_message", default="''"),
    )
    op.create_index(
        "idx_manual_queue_status_prio_rank",
        "manual_queue",
        ["status", "priority", "sort_rank", "id"],
    )

    op.create_table(
        "manual_queue_events",
        id_pk(),
        dbl("ts"),
        bi("queue_id"),
        txt("event_type"),
        txt("details_json", default="'{}'"),
    )
    op.create_index("idx_manual_queue_events_qid_ts", "manual_queue_events", ["queue_id", "ts"])

    op.create_table(
        "ui_notifications",
        id_pk(),
        dbl("created_ts"),
        dbl("updated_ts"),
        txt("severity", default="'warning'"),
        txt("category", default="'action_validation'"),
        txt("title"),
        txt("message"),
        txt("help_prompt", default="''"),
        txt("action_kind", default="''"),
        txt("planet_coords", default="''"),
        txt("target_name", default="''"),
        txt("dedupe_key", default="''"),
        bi("occurrence_count", default="1"),
        txt("status", default="'unread'"),
        dbl("acknowledged_ts", nullable=True),
        txt("details_json", default="'{}'"),
    )
    op.create_index("idx_ui_notifications_dedupe", "ui_notifications", ["dedupe_key", "status"])
    op.create_index("idx_ui_notifications_status_ts", "ui_notifications", ["status", "updated_ts"])


def create_empire_tables() -> None:
    """Create the 10 frozen baseline snapshot, queue, and notification tables."""
    _create_state_snapshot_tables()
    _create_planet_hot_and_rank_tables()
    _create_queue_and_notification_tables()


__all__ = ["EMPIRE_TABLE_NAMES", "create_empire_tables"]
