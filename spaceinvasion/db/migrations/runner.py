"""Programmatic Alembic migration runner, baseline stamper, and schema-drift verifier."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, insert, inspect, text

from spaceinvasion.db.migrations.backup import (
    NO_BACKUP,
    BackupCallback,
    BackupOption,
    make_pg_dump_backup_fn,
)
from spaceinvasion.db.models import Base, SchemaMigrationEvent
from spaceinvasion.db.session import DatabaseEngine, create_db_engine, normalize_sqlalchemy_dsn

BASELINE_REVISION: str = "0001"
ALEMBIC_SCRIPT_LOCATION: Path = Path(__file__).resolve().parent
IGNORED_METADATA_TABLES: frozenset[str] = frozenset({"_pg_pragma_meta", "alembic_version"})


class SchemaDriftError(RuntimeError):
    """Raised when live PostgreSQL schema disagrees with `Base.metadata`."""

    def __init__(self, diffs: list[object]) -> None:
        self.diffs = diffs
        super().__init__(f"PostgreSQL schema drift detected ({len(diffs)} difference(s)): {diffs[:5]}")


@dataclass(frozen=True)
class AppliedRevision:
    """Metadata for one revision applied or stamped by the Alembic runner."""

    revision: str
    down_revision: str | None
    name: str
    checksum: str
    elapsed_ms: float
    event_type: str


@dataclass(frozen=True)
class MigrationResult:
    """Outcome summary returned by `upgrade_database` and `stamp_baseline_database`."""

    previous_revision: str | None
    current_revision: str | None
    applied_revisions: tuple[AppliedRevision, ...]
    backup_path: str


def _unwrap_engine(engine: Engine | DatabaseEngine) -> Engine:
    return engine.engine if isinstance(engine, DatabaseEngine) else engine


def _include_object(
    obj: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object,
) -> bool:
    del obj, reflected, compare_to
    return not (type_ == "table" and name in IGNORED_METADATA_TABLES)


def _read_legacy_schema_version(conn: Connection) -> int:
    """Return `MAX(schema_migrations.version)` in the legacy V001..V014 integer scheme, or `0`."""
    tables = set(inspect(conn).get_table_names(schema="public"))
    if "schema_migrations" not in tables:
        return 0
    row: object = conn.execute(text("SELECT MAX(version) FROM schema_migrations")).scalar_one_or_none()
    return row if isinstance(row, int) else 0


def _script_name(revision_id: str, path: str) -> str:
    stem = Path(path).stem if path else ""
    return stem or revision_id


def build_alembic_config(
    dsn: str | None = None,
    *,
    connection: Connection | None = None,
) -> Config:
    """Build a programmatic Alembic `Config` without requiring a repo-root `alembic.ini`."""
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))
    if dsn is not None:
        cfg.set_main_option("sqlalchemy.url", normalize_sqlalchemy_dsn(dsn).replace("%", "%%"))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def compute_revision_checksum(revision_id: str) -> str:
    """Compute a deterministic SHA-256 checksum for an Alembic revision script."""
    script_dir = ScriptDirectory.from_config(build_alembic_config())
    rev = script_dir.get_revision(revision_id)
    if rev is None:
        raise KeyError(f"Unknown Alembic revision: {revision_id!r}")

    hasher = hashlib.sha256()
    rev_path = Path(rev.path)
    hasher.update(rev_path.name.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(rev_path.read_bytes())

    if rev.revision == BASELINE_REVISION:
        baseline_dir = ALEMBIC_SCRIPT_LOCATION / "baseline"
        for helper_path in sorted(baseline_dir.glob("*.py")):
            hasher.update(helper_path.name.encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(helper_path.read_bytes())
    return hasher.hexdigest()


def get_current_revision(engine: Engine | DatabaseEngine | Connection) -> str | None:
    """Return the active revision stored in `alembic_version`, or `None` if unversioned."""
    if isinstance(engine, Connection):
        return MigrationContext.configure(engine).get_current_revision()
    sa_engine = _unwrap_engine(engine)
    with sa_engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        return ctx.get_current_revision()


def _compare_metadata_on_conn(conn: Connection) -> list[object]:
    ctx = MigrationContext.configure(
        conn,
        opts={"compare_type": True, "compare_server_default": True, "include_object": _include_object},
    )
    raw_diffs: object = compare_metadata(ctx, Base.metadata)
    return list(raw_diffs) if isinstance(raw_diffs, (list, tuple)) else []


def verify_models_match_migrations(engine: Engine | DatabaseEngine | Connection) -> list[object]:
    """Compare live PostgreSQL schema against `Base.metadata` via `compare_metadata`."""
    if isinstance(engine, Connection):
        return _compare_metadata_on_conn(engine)
    sa_engine = _unwrap_engine(engine)
    with sa_engine.connect() as conn:
        return _compare_metadata_on_conn(conn)


def verify_schema_against_models(dsn: str) -> list[object]:
    """Connect to `dsn` read-only and verify PostgreSQL schema matches `Base.metadata`."""
    engine = create_db_engine(dsn, pool_size=1, max_overflow=0)
    try:
        return verify_models_match_migrations(engine)
    finally:
        engine.dispose()


def _append_migration_event(
    conn: Connection,
    *,
    event_type: str,
    legacy_version: int,
    from_rev: str | None,
    to_rev: str,
    name: str,
    checksum: str,
    elapsed_ms: float,
    backup_path: str,
    down_revision: str | None,
) -> None:
    details = json.dumps(
        {
            "engine": "alembic",
            "revision": to_rev,
            "previous_revision": from_rev,
            "down_revision": down_revision,
            "legacy_schema_version": legacy_version,
            "elapsed_ms": round(elapsed_ms, 3),
        },
        sort_keys=True,
        allow_nan=False,
    )
    stmt = insert(SchemaMigrationEvent).values(
        ts=round(time.time(), 3),
        event_type=event_type,
        from_version=legacy_version,
        to_version=legacy_version,
        version=None,
        name=name[:128],
        checksum=checksum[:128],
        backup_path=backup_path[:512],
        details_json=details,
    )
    conn.execute(stmt)


def _resolve_pending_revisions(
    script_dir: ScriptDirectory,
    current_rev: str | None,
    target: str,
) -> list[tuple[str, str | None, str]]:
    """Return ordered `(revision, down_revision, name)` steps from `current_rev` to `target`."""
    target_rev_obj = script_dir.get_revision(target)
    if target_rev_obj is None:
        raise ValueError(f"Unknown target Alembic revision: {target!r}")
    if current_rev == target_rev_obj.revision:
        return []

    base_marker = current_rev if current_rev is not None else "base"
    revs = list(script_dir.walk_revisions(base=base_marker, head=target_rev_obj.revision))
    revs.reverse()
    steps: list[tuple[str, str | None, str]] = []
    for rev in revs:
        if rev.revision == current_rev:
            continue
        down = rev.down_revision if isinstance(rev.down_revision, str) else None
        steps.append((rev.revision, down, _script_name(rev.revision, rev.path)))
    return steps


def upgrade_database(
    engine: Engine | DatabaseEngine,
    revision: str = "head",
    *,
    backup_fn: BackupOption,
) -> MigrationResult:
    """Upgrade PostgreSQL to `revision`, requiring `backup_fn` (or explicit `NO_BACKUP`)."""
    if backup_fn is not NO_BACKUP and not callable(backup_fn):
        raise TypeError("upgrade_database requires a callable `backup_fn` or explicit `NO_BACKUP` opt-out")

    sa_engine = _unwrap_engine(engine)
    previous_rev = get_current_revision(sa_engine)
    script_dir = ScriptDirectory.from_config(build_alembic_config())
    pending = _resolve_pending_revisions(script_dir, previous_rev, revision)
    if not pending:
        return MigrationResult(
            previous_revision=previous_rev,
            current_revision=previous_rev,
            applied_revisions=(),
            backup_path="",
        )

    backup_path = ""
    if callable(backup_fn):
        raw_backup = backup_fn()
        backup_path = str(raw_backup) if raw_backup is not None else ""

    applied: list[AppliedRevision] = []
    cur_rev = previous_rev
    for rev_id, down_rev, rev_name in pending:
        checksum = compute_revision_checksum(rev_id)
        t0 = time.perf_counter()
        with sa_engine.begin() as conn:
            cfg = build_alembic_config(connection=conn)
            command.upgrade(cfg, rev_id)
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            legacy_ver = _read_legacy_schema_version(conn)
            _append_migration_event(
                conn,
                event_type="APPLY",
                legacy_version=legacy_ver,
                from_rev=cur_rev,
                to_rev=rev_id,
                name=rev_name,
                checksum=checksum,
                elapsed_ms=elapsed_ms,
                backup_path=backup_path,
                down_revision=down_rev,
            )
        applied.append(
            AppliedRevision(
                revision=rev_id,
                down_revision=down_rev,
                name=rev_name,
                checksum=checksum,
                elapsed_ms=elapsed_ms,
                event_type="APPLY",
            )
        )
        cur_rev = rev_id

    return MigrationResult(
        previous_revision=previous_rev,
        current_revision=get_current_revision(sa_engine),
        applied_revisions=tuple(applied),
        backup_path=backup_path,
    )


def stamp_baseline_database(
    engine: Engine | DatabaseEngine,
    *,
    revision: str = BASELINE_REVISION,
    verify_schema: bool = True,
) -> MigrationResult:
    """Stamp an already-migrated PostgreSQL database with `revision` without running table DDL."""
    sa_engine = _unwrap_engine(engine)
    with sa_engine.connect() as conn:
        existing_tables = set(inspect(conn).get_table_names(schema="public"))
        if "schema_migration_events" not in existing_tables:
            raise RuntimeError("Cannot stamp an uninitialized database missing `schema_migration_events`")
        legacy_ver = _read_legacy_schema_version(conn)

    if verify_schema:
        diffs = verify_models_match_migrations(sa_engine)
        if diffs:
            raise SchemaDriftError(diffs)

    previous_rev = get_current_revision(sa_engine)
    script_dir = ScriptDirectory.from_config(build_alembic_config())
    rev_obj = script_dir.get_revision(revision)
    if rev_obj is None:
        raise ValueError(f"Unknown Alembic revision: {revision!r}")

    rev_name = _script_name(rev_obj.revision, rev_obj.path)
    down_rev = rev_obj.down_revision if isinstance(rev_obj.down_revision, str) else None
    checksum = compute_revision_checksum(rev_obj.revision)

    t0 = time.perf_counter()
    with sa_engine.begin() as conn:
        cfg = build_alembic_config(connection=conn)
        command.stamp(cfg, rev_obj.revision)
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        _append_migration_event(
            conn,
            event_type="STAMP_BASELINE",
            legacy_version=legacy_ver,
            from_rev=previous_rev,
            to_rev=rev_obj.revision,
            name=rev_name,
            checksum=checksum,
            elapsed_ms=elapsed_ms,
            backup_path="",
            down_revision=down_rev,
        )

    applied_item = AppliedRevision(
        revision=rev_obj.revision,
        down_revision=down_rev,
        name=rev_name,
        checksum=checksum,
        elapsed_ms=elapsed_ms,
        event_type="STAMP_BASELINE",
    )
    return MigrationResult(
        previous_revision=previous_rev,
        current_revision=get_current_revision(sa_engine),
        applied_revisions=(applied_item,),
        backup_path="",
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
