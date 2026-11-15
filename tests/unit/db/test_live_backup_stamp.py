"""Read-only backup restore, schema equivalence, and Alembic baseline stamp verification."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest
from sqlalchemy import select

from backend.db.migrations import (
    BASELINE_REVISION,
    get_current_revision,
    stamp_baseline_database,
    verify_models_match_migrations,
)
from backend.db.models import Base, SchemaMigration, SchemaMigrationEvent
from backend.db.session import DatabaseEngine, db_uow
from tests.pg_harness import DisposablePostgresServer

LIVE_BACKUP_ENV_VAR: str = "SPACEINVASION_LIVE_BACKUP_DUMP"


def _dump_disposable_db(
    pg_harness: DisposablePostgresServer,
    db_name: str,
    dump_path: Path,
) -> None:
    """Export a disposable DB in PostgreSQL custom dump format for synthetic restore tests."""
    host_pg_dump = shutil.which("pg_dump")
    if host_pg_dump:
        cmd = [host_pg_dump, "-h", str(pg_harness.socket_dir), "-U", "postgres", "-Fc", "-d", db_name]
    else:
        podman_bin = shutil.which("podman")
        if podman_bin is None or not pg_harness.container_name:
            pytest.skip("Neither host pg_dump nor disposable Podman container is available")
        cmd = [podman_bin, "exec", pg_harness.container_name, "pg_dump", "-U", "postgres", "-Fc", "-d", db_name]
    with dump_path.open("wb") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump exited with code {proc.returncode}")


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


def _assert_restored_db_matches_and_stamps(restored_dsn: str) -> None:
    """Assert zero ORM schema drift and clean `14 -> 14` Alembic baseline stamping."""
    with DatabaseEngine.from_dsn(restored_dsn, pool_size=1, max_overflow=0) as db:
        assert get_current_revision(db) is None
        assert verify_models_match_migrations(db) == []

        result = stamp_baseline_database(db, verify_schema=True)
        assert result.current_revision == BASELINE_REVISION
        assert get_current_revision(db) == BASELINE_REVISION
        assert verify_models_match_migrations(db) == []

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


def test_synthetic_backup_restore_matches_models_and_stamps_cleanly(
    pg_harness: DisposablePostgresServer,
    tmp_path: Path,
) -> None:
    """Restoring a synthetic custom-format dump of a V14 DB verifies zero drift and stamps `14 -> 14`."""
    src_dsn = pg_harness.create_database(prefix="synth_src", from_template=False)
    with DatabaseEngine.from_dsn(src_dsn, pool_size=1, max_overflow=0) as src_db:
        Base.metadata.create_all(src_db.engine)
        with db_uow(src_db) as session:
            session.add(
                SchemaMigration(
                    version=14,
                    version_tag="V014",
                    name="legacy_v14",
                    description="Simulated pre-Alembic V14 schema",
                    checksum="legacy",
                    applied_ts=1700000100.0,
                    duration_ms=1.0,
                    app_schema_version="14",
                )
            )
    src_db_name = src_dsn.split("///", 1)[1].split("?", 1)[0]
    dump_path = tmp_path / "synthetic_v14.dump"
    _dump_disposable_db(pg_harness, src_db_name, dump_path)

    restored_dsn = pg_harness.create_database(prefix="synth_restored", from_template=False)
    dst_db = restored_dsn.split("///", 1)[1].split("?", 1)[0]
    _restore_dump_into_disposable_db(pg_harness, dump_path, dst_db)
    _assert_restored_db_matches_and_stamps(restored_dsn)


@pytest.mark.live_backup
def test_live_backup_schema_matches_models_and_stamps_cleanly(
    pg_harness: DisposablePostgresServer,
) -> None:
    """Opt-in: restored operator-supplied PG dump has zero model drift and stamps cleanly `14 -> 14`."""
    raw_path = os.environ.get(LIVE_BACKUP_ENV_VAR, "").strip()
    if not raw_path:
        pytest.skip(f"Set {LIVE_BACKUP_ENV_VAR} to a custom-format PostgreSQL dump path to run this test")
    dump_path = Path(raw_path)
    if not dump_path.is_file():
        pytest.skip(f"{LIVE_BACKUP_ENV_VAR} does not point to an existing file")

    restored_dsn = pg_harness.create_database(prefix="live_backup", from_template=False)
    db_name = restored_dsn.split("///", 1)[1].split("?", 1)[0]
    _restore_dump_into_disposable_db(pg_harness, dump_path, db_name)
    _assert_restored_db_matches_and_stamps(restored_dsn)
