"""Programmatic Alembic migration engine for SpaceInvasion PostgreSQL schemas."""

from __future__ import annotations

from backend.db.migrations.runner import (
    ALEMBIC_SCRIPT_LOCATION,
    BASELINE_REVISION,
    IGNORED_METADATA_TABLES,
    NO_BACKUP,
    AppliedRevision,
    BackupCallback,
    BackupOption,
    MigrationResult,
    SchemaDriftError,
    build_alembic_config,
    compute_revision_checksum,
    get_current_revision,
    make_pg_dump_backup_fn,
    stamp_baseline_database,
    upgrade_database,
    verify_models_match_migrations,
    verify_schema_against_models,
)

__all__ = [
    "ALEMBIC_SCRIPT_LOCATION",
    "BASELINE_REVISION",
    "IGNORED_METADATA_TABLES",
    "NO_BACKUP",
    "AppliedRevision",
    "BackupCallback",
    "BackupOption",
    "MigrationResult",
    "SchemaDriftError",
    "build_alembic_config",
    "compute_revision_checksum",
    "get_current_revision",
    "make_pg_dump_backup_fn",
    "stamp_baseline_database",
    "upgrade_database",
    "verify_models_match_migrations",
    "verify_schema_against_models",
]
