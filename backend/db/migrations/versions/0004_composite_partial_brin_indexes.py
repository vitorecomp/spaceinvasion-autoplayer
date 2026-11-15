"""Add measured composite, partial, and BRIN indexes from live pg_stat_statements.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-20 12:47:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import desc, text

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE_SPY_TARGET_PREDICATE: str = "merged_into = '' AND is_own_planet = 0"

_BRIN_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("idx_galaxy_slot_history_ts_brin", "galaxy_slot_history", "ts"),
    ("idx_galaxy_scan_history_ts_brin", "galaxy_scan_history", "ts"),
    ("idx_planet_snapshots_ts_brin", "planet_snapshots", "ts"),
    ("idx_action_history_ts_brin", "action_history", "ts"),
    ("idx_hourly_production_recorded_brin", "hourly_production_history", "recorded_at"),
    ("idx_rank_snapshots_ts_brin", "rank_snapshots", "ts"),
    ("idx_state_snapshots_ts_brin", "state_snapshots", "ts"),
)

_DROP_ORDER: tuple[tuple[str, str], ...] = (
    ("idx_state_snapshots_ts_brin", "state_snapshots"),
    ("idx_rank_snapshots_ts_brin", "rank_snapshots"),
    ("idx_hourly_production_recorded_brin", "hourly_production_history"),
    ("idx_action_history_ts_brin", "action_history"),
    ("idx_planet_snapshots_ts_brin", "planet_snapshots"),
    ("idx_galaxy_scan_history_ts_brin", "galaxy_scan_history"),
    ("idx_galaxy_slot_history_ts_brin", "galaxy_slot_history"),
    ("idx_action_history_exec_kind_pid_ts", "action_history"),
    ("idx_field_conflict_prompts_pending", "field_conflict_prompts"),
    ("idx_ui_notifications_unread", "ui_notifications"),
    ("idx_manual_queue_active", "manual_queue"),
    ("idx_colony_candidates_hot_unprobed_other", "colony_candidates_hot"),
    ("idx_colony_candidates_hot_prime", "colony_candidates_hot"),
    ("idx_colony_candidates_hot_probed", "colony_candidates_hot"),
    ("idx_galaxy_scan_history_ts_id", "galaxy_scan_history"),
    ("idx_galaxy_slot_history_ts_id", "galaxy_slot_history"),
    ("idx_planet_snapshots_ts_id", "planet_snapshots"),
    ("idx_planet_snapshots_pid_ts_id", "planet_snapshots"),
    ("idx_spy_targets_active_non_own", "spy_targets"),
    ("idx_spy_targets_active_spied", "spy_targets"),
    ("idx_spy_targets_active_blocked", "spy_targets"),
    ("idx_spy_targets_active_debris", "spy_targets"),
    ("idx_spy_targets_priority_unspied", "spy_targets"),
    ("idx_spy_targets_priority_spied", "spy_targets"),
)


def _create_spy_target_indexes() -> None:
    op.create_index(
        "idx_spy_targets_priority_spied",
        "spy_targets",
        ["is_inactive", "is_spacia", "has_debris", desc("last_scanned_ts"), desc("points").nulls_last(), "coords"],
        postgresql_where=text(f"{_ACTIVE_SPY_TARGET_PREDICATE} AND last_spied_ts > 0.0"),
    )
    op.create_index(
        "idx_spy_targets_priority_unspied",
        "spy_targets",
        ["is_inactive", "is_spacia", "has_debris", desc("last_scanned_ts"), desc("points").nulls_last(), "coords"],
        postgresql_where=text(f"{_ACTIVE_SPY_TARGET_PREDICATE} AND last_spied_ts <= 0.0"),
    )
    op.create_index(
        "idx_spy_targets_active_debris",
        "spy_targets",
        [desc("last_scanned_ts"), desc("points").nulls_last(), "coords"],
        postgresql_where=text("merged_into = '' AND is_own_planet = 0 AND has_debris = 1"),
    )
    op.create_index(
        "idx_spy_targets_active_blocked",
        "spy_targets",
        [desc("blocked_until_ts"), "coords"],
        postgresql_where=text("blocked_until_ts > 0.0"),
    )
    op.create_index(
        "idx_spy_targets_active_spied",
        "spy_targets",
        [desc("last_scanned_ts"), "coords"],
        postgresql_where=text("merged_into = '' AND is_own_planet = 0 AND last_spied_ts > 0.0"),
    )
    op.create_index(
        "idx_spy_targets_active_non_own",
        "spy_targets",
        ["is_inactive", "is_spacia", "has_debris", "last_spied_ts"],
        postgresql_where=text("merged_into = '' AND is_own_planet = 0"),
    )


def _create_colony_and_hot_subset_indexes() -> None:
    op.create_index("idx_planet_snapshots_pid_ts_id", "planet_snapshots", ["planet_id", desc("ts"), desc("id")])
    op.create_index("idx_planet_snapshots_ts_id", "planet_snapshots", [desc("ts"), desc("id")])
    op.create_index("idx_galaxy_slot_history_ts_id", "galaxy_slot_history", [desc("ts"), desc("id")])
    op.create_index("idx_galaxy_scan_history_ts_id", "galaxy_scan_history", [desc("ts"), desc("id")])
    op.create_index(
        "idx_colony_candidates_hot_probed",
        "colony_candidates_hot",
        [desc("max_fields_est"), "coords"],
        postgresql_where=text("is_active = 1 AND is_discarded = 0 AND is_probed = 1"),
    )
    op.create_index(
        "idx_colony_candidates_hot_prime",
        "colony_candidates_hot",
        ["galaxy", "system", "position", "coords"],
        postgresql_where=text(
            "is_active = 1 AND is_discarded = 0 AND is_probed = 0 AND position >= 7 AND position <= 12"
        ),
    )
    op.create_index(
        "idx_colony_candidates_hot_unprobed_other",
        "colony_candidates_hot",
        ["position", "galaxy", "system", "coords"],
        postgresql_where=text(
            "is_active = 1 AND is_discarded = 0 AND is_probed = 0 AND (position < 7 OR position > 12)"
        ),
    )
    op.create_index(
        "idx_manual_queue_active",
        "manual_queue",
        ["priority", "sort_rank", "id"],
        postgresql_where=text("status IN ('pending', 'ready')"),
    )
    op.create_index(
        "idx_ui_notifications_unread",
        "ui_notifications",
        [desc("updated_ts"), desc("id")],
        postgresql_where=text("status = 'unread'"),
    )
    op.create_index(
        "idx_field_conflict_prompts_pending",
        "field_conflict_prompts",
        [desc("created_ts"), "conflict_key"],
        postgresql_where=text("status = 'pending'"),
    )
    op.create_index(
        "idx_action_history_exec_kind_pid_ts",
        "action_history",
        ["kind", "planet_id", desc("ts")],
        postgresql_where=text("executed = 1 AND dry_run = 0"),
    )


def upgrade() -> None:
    """Create all measured composite, partial, and BRIN indexes."""
    _create_spy_target_indexes()
    _create_colony_and_hot_subset_indexes()
    for name, table_name, col_name in _BRIN_INDEXES:
        op.create_index(name, table_name, [col_name], postgresql_using="brin")


def downgrade() -> None:
    """Drop all revision 0004 composite, partial, and BRIN indexes."""
    for name, table_name in _DROP_ORDER:
        op.drop_index(name, table_name=table_name)
