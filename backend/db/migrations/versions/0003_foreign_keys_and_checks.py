"""Add verified parent-child foreign keys and CHECK constraints on flags/enums.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-20 12:46:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (constraint_name, child_table, parent_table, local_cols, remote_cols)
_FOREIGN_KEYS: tuple[tuple[str, str, str, list[str], list[str]], ...] = (
    ("fk_manual_queue_events_queue_id", "manual_queue_events", "manual_queue", ["queue_id"], ["id"]),
    (
        "fk_field_conflict_events_conflict_key",
        "field_conflict_events",
        "field_conflict_prompts",
        ["conflict_key"],
        ["conflict_key"],
    ),
    (
        "fk_config_optimizer_active_run_id",
        "config_optimizer_active",
        "config_optimizer_runs",
        ["run_id"],
        ["id"],
    ),
    ("fk_ai_planner_active_run_id", "ai_planner_active", "ai_planner_runs", ["run_id"], ["id"]),
    (
        "fk_ai_planner_active_config_run_id",
        "ai_planner_active",
        "config_optimizer_runs",
        ["config_run_id"],
        ["id"],
    ),
    ("fk_ai_planner_runs_config_run_id", "ai_planner_runs", "config_optimizer_runs", ["config_run_id"], ["id"]),
)

# (constraint_name, table_name, condition_sql)
_CHECK_CONSTRAINTS: tuple[tuple[str, str, str], ...] = (
    ("ck_action_history_executed", "action_history", "executed IN (0, 1)"),
    ("ck_action_history_dry_run", "action_history", "dry_run IN (0, 1)"),
    ("ck_bash_blocks_is_bash", "bash_blocks", "is_bash IN (0, 1)"),
    ("ck_colony_candidates_hot_is_probed", "colony_candidates_hot", "is_probed IN (0, 1)"),
    ("ck_colony_candidates_hot_is_discarded", "colony_candidates_hot", "is_discarded IN (0, 1)"),
    ("ck_colony_candidates_hot_is_active", "colony_candidates_hot", "is_active IN (0, 1)"),
    (
        "ck_colony_candidates_hot_coords_bounds",
        "colony_candidates_hot",
        "galaxy >= 0 AND system >= 0 AND position >= 0",
    ),
    ("ck_empire_planets_hot_is_active", "empire_planets_hot", "is_active IN (0, 1)"),
    (
        "ck_field_conflict_prompts_status",
        "field_conflict_prompts",
        "status IN ('pending', 'resolved', 'dismissed', 'expired')",
    ),
    ("ck_galaxy_scans_coords_bounds", "galaxy_scans", "galaxy >= 1 AND system >= 1"),
    ("ck_galaxy_sector_hot_coords_bounds", "galaxy_sector_hot", "galaxy >= 1 AND system >= 1"),
    ("ck_galaxy_slot_history_is_inactive", "galaxy_slot_history", "is_inactive IN (0, 1)"),
    ("ck_galaxy_slot_history_is_spacia", "galaxy_slot_history", "is_spacia IN (0, 1)"),
    ("ck_galaxy_slot_history_has_debris", "galaxy_slot_history", "has_debris IN (0, 1)"),
    ("ck_galaxy_slot_history_has_asteroid", "galaxy_slot_history", "has_asteroid IN (0, 1)"),
    ("ck_galaxy_slots_is_inactive", "galaxy_slots", "is_inactive IN (0, 1)"),
    ("ck_galaxy_slots_is_spacia", "galaxy_slots", "is_spacia IN (0, 1)"),
    ("ck_galaxy_slots_has_debris", "galaxy_slots", "has_debris IN (0, 1)"),
    ("ck_game_messages_deleted_in_game", "game_messages", "deleted_in_game IN (0, 1)"),
    (
        "ck_manual_queue_status",
        "manual_queue",
        "status IN ('pending', 'ready', 'running', 'completed', 'canceled', 'failed')",
    ),
    ("ck_manual_queue_priority", "manual_queue", "priority IN ('P0', 'P1', 'P2', 'P3')"),
    ("ck_raid_history_is_exempt", "raid_history", "is_exempt IN (0, 1)"),
    ("ck_spy_reports_is_spacia", "spy_reports", "is_spacia IN (0, 1)"),
    ("ck_spy_reports_dug_in", "spy_reports", "dug_in IS NULL OR dug_in IN (0, 1)"),
    ("ck_spy_targets_is_inactive", "spy_targets", "is_inactive IN (0, 1)"),
    ("ck_spy_targets_is_spacia", "spy_targets", "is_spacia IN (0, 1)"),
    ("ck_spy_targets_has_debris", "spy_targets", "has_debris IN (0, 1)"),
    ("ck_spy_targets_dug_in", "spy_targets", "dug_in IN (0, 1)"),
    ("ck_spy_targets_is_own_planet", "spy_targets", "is_own_planet IN (0, 1)"),
    ("ck_tutorial_progress_can_complete", "tutorial_progress", "can_complete IN (0, 1)"),
    ("ck_ui_notifications_status", "ui_notifications", "status IN ('unread', 'read', 'dismissed')"),
    ("ck_ui_notifications_severity", "ui_notifications", "severity IN ('info', 'warning', 'error', 'critical')"),
)


def upgrade() -> None:
    """Create verified parent-child foreign keys and CHECK constraints."""
    for name, source_tbl, referent_tbl, local_cols, remote_cols in _FOREIGN_KEYS:
        op.create_foreign_key(name, source_tbl, referent_tbl, local_cols, remote_cols)
    for name, table_name, condition in _CHECK_CONSTRAINTS:
        op.create_check_constraint(name, table_name, condition)


def downgrade() -> None:
    """Drop all revision 0003 CHECK constraints and foreign keys cleanly."""
    for name, table_name, _condition in reversed(_CHECK_CONSTRAINTS):
        op.drop_constraint(name, table_name, type_="check")
    for name, source_tbl, _referent_tbl, _local_cols, _remote_cols in reversed(_FOREIGN_KEYS):
        op.drop_constraint(name, source_tbl, type_="foreignkey")
