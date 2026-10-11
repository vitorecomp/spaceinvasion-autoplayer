"""Baseline SpaceInvasion PostgreSQL schema matching V001..V014.

Revision ID: 0001
Revises:
Create Date: 2026-11-20 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from spaceinvasion.db.alembic_migrations.baseline import (
    create_baseline_schema,
    drop_baseline_schema,
)

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create all 40 baseline PostgreSQL tables, constraints, and indexes."""
    create_baseline_schema()


def downgrade() -> None:
    """Drop all 40 baseline PostgreSQL tables."""
    drop_baseline_schema()
