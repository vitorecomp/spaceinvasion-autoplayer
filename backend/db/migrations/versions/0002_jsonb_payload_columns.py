"""Convert queried/structured domain JSON columns from TEXT to native PostgreSQL JSONB.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20 12:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Text, text
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table_name, column_name, fallback_empty_json, original_text_default_sql)
_JSONB_COLUMNS: tuple[tuple[str, str, str, str | None], ...] = (
    ("ai_planner_active", "params_json", "{}", None),
    ("ai_planner_active", "summary_json", "{}", None),
    ("ai_planner_active", "result_json", "{}", None),
    ("ai_planner_runs", "params_json", "{}", None),
    ("ai_planner_runs", "summary_json", "{}", None),
    ("ai_planner_runs", "result_json", "{}", None),
    ("colony_attempts", "details_json", "{}", "'{}'"),
    ("config_history", "config_json", "{}", None),
    ("config_optimizer_active", "params_json", "{}", None),
    ("config_optimizer_active", "reserved_floors_json", "{}", "'{}'"),
    ("config_optimizer_active", "result_json", "{}", None),
    ("config_optimizer_runs", "params_json", "{}", None),
    ("config_optimizer_runs", "reserved_floors_json", "{}", "'{}'"),
    ("config_optimizer_runs", "result_json", "{}", None),
    ("economy_rates_cache", "rates_json", "{}", None),
    ("field_conflict_events", "detail_json", "{}", "'{}'"),
    ("field_conflict_prompts", "detail_json", "{}", "'{}'"),
    ("galaxy_sector_hot", "owners_json", "[]", "'[]'"),
    ("galaxy_sector_hot", "owner_planet_counts_json", "{}", "'{}'"),
    ("galaxy_sector_hot", "coord_owners_json", "{}", "'{}'"),
    ("galaxy_sector_hot", "spacia_coords_json", "[]", "'[]'"),
    ("game_messages", "parsed_json", "{}", "'{}'"),
    ("latest_state_hot", "state_json", "{}", "'{}'"),
    ("latest_state_hot", "plan_json", "[]", "'[]'"),
    ("manual_queue_events", "details_json", "{}", "'{}'"),
    ("planet_snapshots", "buildings_json", "{}", None),
    ("planet_snapshots", "ships_json", "{}", None),
    ("planet_snapshots", "defense_json", "{}", None),
    ("planet_snapshots", "economy_rates_json", "{}", None),
    ("planet_snapshots", "researches_json", "{}", None),
    ("raid_history", "loot_json", "{}", "'{}'"),
    ("spy_reports", "buildings_json", "{}", "'{}'"),
    ("spy_reports", "fleet_json", "{}", "'{}'"),
    ("spy_reports", "defense_json", "{}", "'{}'"),
    ("spy_reports", "researches_json", "{}", "'{}'"),
    ("spy_targets", "fleet_json", "{}", "'{}'"),
    ("spy_targets", "defense_json", "{}", "'{}'"),
    ("state_snapshots", "state_json", "{}", "'{}'"),
    ("state_snapshots", "plan_json", "[]", "'[]'"),
    ("strategy_config_store", "config_json", "{}", None),
    ("tutorial_progress", "tasks_json", "[]", None),
    ("ui_notifications", "details_json", "{}", "'{}'"),
    ("universe_research_max_history", "records_json", "[]", None),
)


def upgrade() -> None:
    """Alter all 43 structured domain JSON columns from `TEXT` to `JSONB`."""
    for table_name, col_name, fallback_empty, orig_default in _JSONB_COLUMNS:
        if orig_default is not None:
            op.alter_column(table_name, col_name, server_default=None)
        using_sql = f"CASE WHEN btrim({col_name}) = '' THEN '{fallback_empty}'::jsonb ELSE {col_name}::jsonb END"
        op.alter_column(
            table_name,
            col_name,
            existing_type=Text(),
            type_=JSONB(),
            existing_nullable=False,
            postgresql_using=using_sql,
        )
        if orig_default is not None:
            op.alter_column(table_name, col_name, server_default=text(f"{orig_default}::jsonb"))


def downgrade() -> None:
    """Losslessly convert all 43 `JSONB` columns back to `TEXT` with original defaults."""
    for table_name, col_name, _fallback_empty, orig_default in reversed(_JSONB_COLUMNS):
        if orig_default is not None:
            op.alter_column(table_name, col_name, server_default=None)
        op.alter_column(
            table_name,
            col_name,
            existing_type=JSONB(),
            type_=Text(),
            existing_nullable=False,
            postgresql_using=f"{col_name}::text",
        )
        if orig_default is not None:
            op.alter_column(table_name, col_name, server_default=text(orig_default))
