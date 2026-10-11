"""Tests for Alembic baseline migrations, schema drift verification, and DB unit of work."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from alembic import command
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from spaceinvasion.db.alembic_migrations import (
    BASELINE_REVISION,
    SchemaDriftError,
    build_alembic_config,
    compute_revision_checksum,
    get_current_revision,
    stamp_baseline_database,
    upgrade_database,
    verify_models_match_migrations,
    verify_schema_against_models,
)
from spaceinvasion.db.models import (
    ManualQueueItem,
    MetaEntry,
    SchemaMigrationEvent,
    UiNotification,
)
from spaceinvasion.db.session import (
    DatabaseEngine,
    StatementCountExceededError,
    TransitionalSessionBridge,
    UnitOfWork,
    count_statements,
    create_db_engine,
    create_session_factory,
    db_uow,
    normalize_sqlalchemy_dsn,
    session_raw_cursor,
)
from tests.pg_harness import DisposablePostgresServer
from tests.unit.db.test_orm_schema_equivalence import _introspect_pg_schema

_REPO_ROOT: Path = Path(__file__).resolve().parents[3]
_SESSION_MODULE: Path = _REPO_ROOT / "spaceinvasion" / "db" / "session.py"
_ALEMBIC_DIR: Path = _REPO_ROOT / "spaceinvasion" / "db" / "alembic_migrations"
_EXCLUDED_CATALOG_TABLES: frozenset[str] = frozenset({"_pg_pragma_meta", "alembic_version"})
_FORBIDDEN_DB_IMPORTS: frozenset[str] = frozenset(
    {
        "bin",
        "spaceinvasion.combat",
        "spaceinvasion.parsing",
        "spaceinvasion.planner",
        "spaceinvasion.runtime",
        "spaceinvasion.session",
    }
)


def test_fresh_upgrade_matches_si_template_and_records_audit_and_backup(
    pg_harness: DisposablePostgresServer,
) -> None:
    """Fresh DB `upgrade_database('head')` matches `si_template` 1:1 with zero drift and audit."""
    migrated_schema = _introspect_pg_schema(
        pg_harness.template_dsn,
        exclude_tables=_EXCLUDED_CATALOG_TABLES,
    )
    fresh_dsn = pg_harness.create_database(prefix="alembic_fresh", from_template=False)

    backup_calls: list[str] = []

    def _fake_backup() -> str:
        path = "/tmp/fake_pre_upgrade_0001.dump"
        backup_calls.append(path)
        return path

    with DatabaseEngine.from_dsn(fresh_dsn, pool_size=2, max_overflow=0) as db:
        assert get_current_revision(db) is None
        result = upgrade_database(db, "head", backup_fn=_fake_backup)

        assert result.previous_revision is None
        assert result.current_revision == BASELINE_REVISION
        assert result.backup_path == "/tmp/fake_pre_upgrade_0001.dump"
        assert backup_calls == ["/tmp/fake_pre_upgrade_0001.dump"]
        assert len(result.applied_revisions) == 1
        applied = result.applied_revisions[0]
        assert applied.revision == BASELINE_REVISION
        assert applied.event_type == "APPLY"
        assert applied.checksum == compute_revision_checksum(BASELINE_REVISION)
        assert applied.elapsed_ms >= 0.0

        # Exact 1:1 pg_catalog match against MigrationRunner-built si_template.
        alembic_schema = _introspect_pg_schema(
            fresh_dsn,
            exclude_tables=_EXCLUDED_CATALOG_TABLES,
        )
        assert set(alembic_schema.keys()) == set(migrated_schema.keys())
        for table_name in sorted(migrated_schema.keys()):
            expected = migrated_schema[table_name]
            actual = alembic_schema[table_name]
            assert actual.columns == expected.columns, f"Column mismatch on {table_name}"
            assert actual.constraints == expected.constraints, f"Constraint mismatch on {table_name}"
            assert actual.indexes == expected.indexes, f"Index mismatch on {table_name}"

        # Alembic autogenerate compare_metadata reports zero drift.
        assert verify_models_match_migrations(db) == []
        assert verify_schema_against_models(fresh_dsn) == []

        # Audit row appended in schema_migration_events.
        with db_uow(db) as session:
            events = list(session.execute(select(SchemaMigrationEvent)).scalars().all())
            assert len(events) == 1
            ev = events[0]
            assert ev.event_type == "APPLY"
            assert ev.version == 1
            assert ev.to_version == 1
            assert ev.checksum == applied.checksum
            assert ev.backup_path == "/tmp/fake_pre_upgrade_0001.dump"
            details = json.loads(ev.details_json)
            assert details["revision"] == BASELINE_REVISION
            assert details["engine"] == "alembic"
            assert details["elapsed_ms"] >= 0.0

        # Second upgrade is a clean idempotent no-op (no duplicate backup or audit row).
        second = upgrade_database(db, "head", backup_fn=_fake_backup)
        assert second.applied_revisions == ()
        assert backup_calls == ["/tmp/fake_pre_upgrade_0001.dump"]

        # Verify downgrade('base') + re-upgrade round-trip works cleanly.
        with db.engine.begin() as conn:
            command.downgrade(build_alembic_config(connection=conn), "base")
        assert get_current_revision(db) is None
        re_upgraded = upgrade_database(db, "head")
        assert re_upgraded.current_revision == BASELINE_REVISION
        assert verify_models_match_migrations(db) == []


def test_stamp_baseline_on_migrated_db_zero_table_ddl_and_preserves_rows(
    pg_harness: DisposablePostgresServer,
) -> None:
    """Stamping a `si_template` clone runs zero game-table DDL, keeps rows, and sets `0001`."""
    cloned_dsn = pg_harness.create_database(prefix="alembic_stamp", from_template=True)

    with DatabaseEngine.from_dsn(cloned_dsn, pool_size=2, max_overflow=0) as db:
        with db_uow(db) as session:
            session.add_all(
                [
                    MetaEntry(key="stamp_sentinel", value="keep_me", updated_ts=1700000200.0),
                    UiNotification(
                        created_ts=1700000201.0,
                        updated_ts=1700000201.0,
                        title="Pre-stamp notice",
                        message="Must survive baseline stamp",
                    ),
                ]
            )

        with count_statements(db) as counter:
            result = stamp_baseline_database(db)

        # Zero DDL statements may touch any of the 40 game tables (only alembic_version is allowed).
        game_table_ddl = [
            stmt
            for stmt in counter.statements
            if any(kw in stmt.upper() for kw in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "CREATE INDEX"))
            and "ALEMBIC_VERSION" not in stmt.upper()
        ]
        assert game_table_ddl == [], f"Unexpected game-table DDL during stamp: {game_table_ddl}"

        assert result.current_revision == BASELINE_REVISION
        assert get_current_revision(db) == BASELINE_REVISION
        assert verify_models_match_migrations(db) == []

        with db_uow(db) as session:
            ver: object = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert ver == BASELINE_REVISION

            sentinel = session.execute(select(MetaEntry).where(MetaEntry.key == "stamp_sentinel")).scalar_one()
            assert sentinel.value == "keep_me"
            notif = session.execute(
                select(UiNotification).where(UiNotification.title == "Pre-stamp notice")
            ).scalar_one()
            assert notif.message == "Must survive baseline stamp"

            stamp_events = list(
                session.execute(select(SchemaMigrationEvent).where(SchemaMigrationEvent.event_type == "STAMP_BASELINE"))
                .scalars()
                .all()
            )
            assert len(stamp_events) == 1
            assert stamp_events[0].checksum == compute_revision_checksum(BASELINE_REVISION)
            details = json.loads(stamp_events[0].details_json)
            assert details["revision"] == BASELINE_REVISION
            assert details["elapsed_ms"] >= 0.0

        # Mutate schema intentionally -> verify_models_match_migrations & stamp detect drift.
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE meta ADD COLUMN drift_probe_col TEXT"))
        diffs = verify_models_match_migrations(db)
        assert len(diffs) >= 1
        with pytest.raises(SchemaDriftError):
            stamp_baseline_database(db)


def test_uow_atomic_commit_and_rollback_with_mixed_orm_and_raw_sql(
    pg_harness: DisposablePostgresServer,
) -> None:
    """`UnitOfWork` and `db_uow` commit/roll back ORM + transitional raw SQL atomically."""
    dsn = pg_harness.create_database(prefix="uow_tx", from_template=True)
    engine = create_db_engine(dsn, pool_size=2, max_overflow=0)
    session_factory = create_session_factory(engine)
    try:
        # 1. Mixed ORM + TransitionalSessionBridge + raw psycopg cursor commit together.
        with db_uow(session_factory) as session:
            session.add(MetaEntry(key="tx_orm_ok", value="1", updated_ts=1700000300.0))
            bridge = TransitionalSessionBridge(session)
            bridge.execute(
                "INSERT INTO meta(key, value, updated_ts) VALUES (%s, %s, %s)",
                ("tx_driver_ok", "2", 1700000301.0),
            )
            with bridge.raw_cursor() as cur:
                cur.execute(
                    "INSERT INTO meta(key, value, updated_ts) VALUES (%s, %s, %s)",
                    ("tx_raw_ok", "3", 1700000302.0),
                )

        with db_uow(session_factory) as session:
            rows = session.execute(
                select(MetaEntry.key, MetaEntry.value)
                .where(MetaEntry.key.in_(["tx_orm_ok", "tx_driver_ok", "tx_raw_ok"]))
                .order_by(MetaEntry.key)
            ).all()
            assert [(r[0], r[1]) for r in rows] == [
                ("tx_driver_ok", "2"),
                ("tx_orm_ok", "1"),
                ("tx_raw_ok", "3"),
            ]

        # 2. Failure inside db_uow rolls back ORM + driver SQL + raw psycopg cursor together.
        with pytest.raises(RuntimeError, match="abort_mixed_uow"):
            with db_uow(session_factory) as session:
                session.add(
                    ManualQueueItem(
                        created_ts=1700000310.0,
                        updated_ts=1700000310.0,
                        kind="UPGRADE_BUILDING",
                        target_name="Rolled back mine",
                    )
                )
                session.flush()
                session.connection().exec_driver_sql(
                    "INSERT INTO meta(key, value, updated_ts) VALUES (%s, %s, %s)",
                    ("tx_driver_rb", "bad", 1700000311.0),
                )
                with session_raw_cursor(session) as cur:
                    cur.execute(
                        "INSERT INTO meta(key, value, updated_ts) VALUES (%s, %s, %s)",
                        ("tx_raw_rb", "bad", 1700000312.0),
                    )
                raise RuntimeError("abort_mixed_uow")

        # 3. Failure inside explicit UnitOfWork also rolls back cleanly.
        with pytest.raises(RuntimeError, match="abort_explicit_uow"):
            with UnitOfWork(session_factory) as uow:
                uow.session.add(MetaEntry(key="tx_uow_cls_rb", value="bad", updated_ts=1700000313.0))
                with uow.raw_cursor() as cur:
                    cur.execute(
                        "INSERT INTO meta(key, value, updated_ts) VALUES (%s, %s, %s)",
                        ("tx_uow_raw_rb", "bad", 1700000314.0),
                    )
                raise RuntimeError("abort_explicit_uow")

        with db_uow(session_factory) as session:
            leaked_meta = list(
                session.execute(
                    select(MetaEntry.key).where(
                        MetaEntry.key.in_(["tx_driver_rb", "tx_raw_rb", "tx_uow_cls_rb", "tx_uow_raw_rb"])
                    )
                )
                .scalars()
                .all()
            )
            leaked_queue = list(
                session.execute(select(ManualQueueItem.id).where(ManualQueueItem.target_name == "Rolled back mine"))
                .scalars()
                .all()
            )
            assert leaked_meta == []
            assert leaked_queue == []
    finally:
        engine.dispose()


def test_statement_timeout_enforcement_and_count_statements(
    pg_harness: DisposablePostgresServer,
) -> None:
    """`statement_timeout_ms` cancels slow SQL (`OperationalError`) and `count_statements` tracks SQL."""
    dsn = pg_harness.create_database(prefix="uow_timeout", from_template=True)
    assert normalize_sqlalchemy_dsn(dsn).startswith("postgresql+psycopg://")
    with pytest.raises(ValueError, match="Unsupported database DSN scheme"):
        normalize_sqlalchemy_dsn("sqlite:///tmp/forbidden.db")

    timeout_engine = create_db_engine(dsn, pool_size=1, max_overflow=0, statement_timeout_ms=50)
    try:
        with pytest.raises(OperationalError):
            with timeout_engine.connect() as conn:
                conn.execute(text("SELECT pg_sleep(0.2)"))
    finally:
        timeout_engine.dispose()

    with DatabaseEngine.from_dsn(dsn, pool_size=2, max_overflow=0, statement_timeout_ms=5000) as db:
        with count_statements(db, max_statements=3) as counter:
            with db_uow(db) as session:
                session.add(MetaEntry(key="cnt_1", value="a", updated_ts=1700000400.0))
                session.flush()
                row = session.execute(select(MetaEntry).where(MetaEntry.key == "cnt_1")).scalar_one()
                assert row.value == "a"
                bridge = TransitionalSessionBridge(session)
                res = bridge.execute("SELECT 1")
                assert res.one()[0] == 1
        assert counter.count == 3

        # Listener detaches cleanly after exiting context manager.
        with db_uow(db) as session:
            assert session.execute(text("SELECT 42")).scalar_one() == 42
        assert counter.count == 3

        # Budget enforcement raises StatementCountExceededError when exceeded.
        with pytest.raises(StatementCountExceededError):
            with count_statements(db, max_statements=1):
                with db_uow(db) as session:
                    session.execute(text("SELECT 1"))
                    session.execute(text("SELECT 2"))


def _check_module_standards(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    assert len(lines) <= 400, f"{path.name} exceeds 400 LOC ({len(lines)})"
    tree = ast.parse(source, filename=str(path))
    assert ast.get_docstring(tree), f"{path.name} is missing a module docstring"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for bad in _FORBIDDEN_DB_IMPORTS:
                    assert not alias.name.startswith(bad), f"{path.name} imports {alias.name}"
        elif isinstance(node, ast.ImportFrom) and node.module:
            for bad in _FORBIDDEN_DB_IMPORTS:
                assert not node.module.startswith(bad), f"{path.name} imports {node.module}"
            if node.module == "os":
                for alias in node.names:
                    assert alias.name not in {"environ", "getenv"}, f"{path.name} reads os.{alias.name}"
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "os":
                assert node.attr not in {"environ", "getenv"}, f"{path.name} reads os.{node.attr}"
        elif isinstance(node, ast.Name) and node.id == "Any":
            raise AssertionError(f"{path.name} must not use `Any`")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            raise AssertionError(f"{path.name} must not call `print()`")
        elif isinstance(node, ast.ExceptHandler):
            exc = node.type
            exc_name = exc.id if isinstance(exc, ast.Name) else ""
            if exc is None or exc_name in {"Exception", "BaseException"}:
                raise AssertionError(f"{path.name} must not use broad except")
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            span = (node.end_lineno or node.lineno) - node.lineno + 1
            limit = 400 if isinstance(node, ast.ClassDef) else 100
            assert span <= limit, f"{path.name}:{node.name} exceeds {limit} LOC ({span})"


def test_alembic_and_session_engineering_standards() -> None:
    """All new `session.py` and `alembic_migrations/` modules satisfy Section 4 standards."""
    modules = [_SESSION_MODULE, *sorted(_ALEMBIC_DIR.rglob("*.py"))]
    assert len(modules) >= 9
    for module_path in modules:
        _check_module_standards(module_path)
