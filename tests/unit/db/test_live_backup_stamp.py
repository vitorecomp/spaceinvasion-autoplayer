"""Read-only live-backup schema equivalence and Alembic baseline stamp verification."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest
from sqlalchemy import select

from spaceinvasion.db.alembic_migrations import (
    BASELINE_REVISION,
    get_current_revision,
    stamp_baseline_database,
    verify_models_match_migrations,
)
from spaceinvasion.db.models import SchemaMigrationEvent
from spaceinvasion.db.session import DatabaseEngine, db_uow
from tests.pg_harness import DisposablePostgresServer

_LIVE_BACKUP_DUMP: Path = Path(
    "/usr/local/google/home/vieiravitor/workspace/opensource/spaceinvasion-autoplayer"
    "/data/backups/pre_phase0_20261120T111949Z.dump"
)


def _restore_dump_into_disposable_db(
    pg_harness: DisposablePostgresServer,
    dump_path: Path,
    db_name: str,
) -> None:
    """Restore a custom-format PostgreSQL dump into an isolated disposable DB.

    Uses `podman exec -i <container> pg_restore` when host `pg_restore` is unavailable.
    Never prints or logs any domain data from the backup.
    """
    host_pg_restore = shutil.which("pg_restore")
    if host_pg_restore:
        cmd = [
            host_pg_restore,
            "-h",
            str(pg_harness.socket_dir),
            "-U",
            "postgres",
            "-d",
            db_name,
            "--no-owner",
            "--no-privileges",
            "--no-comments",
            str(dump_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, check=False)
    else:
        podman_bin = shutil.which("podman")
        if podman_bin is None or not pg_harness.container_name:
            pytest.skip("Neither host pg_restore nor disposable Podman container is available")
        cmd = [
            podman_bin,
            "exec",
            "-i",
            pg_harness.container_name,
            "pg_restore",
            "-U",
            "postgres",
            "-d",
            db_name,
            "--no-owner",
            "--no-privileges",
            "--no-comments",
        ]
        with dump_path.open("rb") as fh:
            proc = subprocess.run(cmd, stdin=fh, capture_output=True, check=False)

    if proc.returncode != 0:
        raise RuntimeError(f"pg_restore exited with code {proc.returncode}")


def test_live_backup_schema_matches_models_and_stamps_cleanly(
    pg_harness: DisposablePostgresServer,
) -> None:
    """Restored copy of the live backup has zero model drift and stamps cleanly `14 -> 14`."""
    if not _LIVE_BACKUP_DUMP.is_file():
        pytest.skip("Live pre-Phase-0 backup dump is not present in this environment")

    restored_dsn = pg_harness.create_database(prefix="live_backup", from_template=False)
    db_name = restored_dsn.split("///", 1)[1].split("?", 1)[0]
    _restore_dump_into_disposable_db(pg_harness, _LIVE_BACKUP_DUMP, db_name)

    with DatabaseEngine.from_dsn(restored_dsn, pool_size=1, max_overflow=0) as db:
        assert get_current_revision(db) is None
        # Verify zero schema drift between live production backup and SQLAlchemy ORM models.
        diffs = verify_models_match_migrations(db)
        assert diffs == []

        # Stamp baseline revision `0001` with verify_schema=True (zero table DDL).
        result = stamp_baseline_database(db, verify_schema=True)
        assert result.current_revision == BASELINE_REVISION
        assert get_current_revision(db) == BASELINE_REVISION
        assert verify_models_match_migrations(db) == []

        # Confirm the append-only audit row keeps legacy integer versions `14 -> 14, version=None`.
        with db_uow(db) as session:
            stamp_events = list(
                session.execute(select(SchemaMigrationEvent).where(SchemaMigrationEvent.event_type == "STAMP_BASELINE"))
                .scalars()
                .all()
            )
            assert len(stamp_events) == 1
            ev = stamp_events[0]
            assert ev.from_version == 14
            assert ev.to_version == 14
            assert ev.version is None
            assert ev.name == "0001_baseline_schema"
