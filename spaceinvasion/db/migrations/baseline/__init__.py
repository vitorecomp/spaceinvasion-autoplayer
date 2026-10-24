"""Frozen `0001` baseline schema orchestrator independent of live ORM models."""

from __future__ import annotations

from alembic import op

from spaceinvasion.db.migrations.baseline.empire_tables import (
    EMPIRE_TABLE_NAMES,
    create_empire_tables,
)
from spaceinvasion.db.migrations.baseline.planner_and_settings_tables import (
    PLANNER_AND_SETTINGS_TABLE_NAMES,
    create_planner_and_settings_tables,
)
from spaceinvasion.db.migrations.baseline.world_tables import (
    WORLD_TABLE_NAMES,
    create_world_tables,
)

BASELINE_TABLE_NAMES: tuple[str, ...] = (
    *WORLD_TABLE_NAMES,
    *EMPIRE_TABLE_NAMES,
    *PLANNER_AND_SETTINGS_TABLE_NAMES,
)


def create_baseline_schema() -> None:
    """Create all 40 frozen baseline PostgreSQL tables and their 42 explicit indexes."""
    create_world_tables()
    create_empire_tables()
    create_planner_and_settings_tables()


def drop_baseline_schema() -> None:
    """Drop all 40 frozen baseline PostgreSQL tables in reverse creation order."""
    for table_name in reversed(BASELINE_TABLE_NAMES):
        op.drop_table(table_name)


__all__ = [
    "BASELINE_TABLE_NAMES",
    "create_baseline_schema",
    "drop_baseline_schema",
]
