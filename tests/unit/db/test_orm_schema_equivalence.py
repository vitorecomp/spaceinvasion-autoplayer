"""Schema-equivalence, layering, and ORM round-trip tests for ORM models."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import RelationshipProperty, Session, selectinload

from spaceinvasion.db.migrations import BASELINE_REVISION, NO_BACKUP, upgrade_database
from spaceinvasion.db.models import (
    Base,
    ColonyCandidateHot,
    GalaxyScan,
    HourlyProductionHistory,
    ManualQueueEvent,
    ManualQueueItem,
    MetaEntry,
    SpyTarget,
    StrategyConfigStore,
    UiNotification,
)
from spaceinvasion.db.queries import (
    GAME_REQUIRED_TABLES,
    HISTORY_TABLES,
    HOT_TABLES,
)
from tests.pg_harness import DisposablePostgresServer

# Internal catalog/versioning tables excluded from 40-table ORM metadata comparison.
_INTERNAL_EMULATION_TABLES: frozenset[str] = frozenset({"_pg_pragma_meta", "alembic_version"})
_REPO_ROOT: Path = Path(__file__).resolve().parents[3]
_MODELS_DIR: Path = _REPO_ROOT / "spaceinvasion" / "db" / "models"
_FORBIDDEN_MODEL_IMPORTS: frozenset[str] = frozenset(
    {
        "bin",
        "spaceinvasion.combat",
        "spaceinvasion.parsing",
        "spaceinvasion.planner",
        "spaceinvasion.runtime",
        "spaceinvasion.session",
    }
)


@dataclass(frozen=True)
class ColumnSpec:
    """Normalized PostgreSQL column descriptor from `pg_catalog`."""

    name: str
    data_type: str
    notnull: bool
    identity: str
    default_expr: str | None


@dataclass(frozen=True)
class ConstraintSpec:
    """Normalized PostgreSQL constraint descriptor from `pg_catalog`."""

    name: str
    contype: str
    definition: str


@dataclass(frozen=True)
class IndexSpec:
    """Normalized PostgreSQL index descriptor from `pg_catalog`."""

    name: str
    is_unique: bool
    is_primary: bool
    definition: str


@dataclass(frozen=True)
class TableSpec:
    """Complete catalog snapshot for a single PostgreSQL table."""

    columns: tuple[ColumnSpec, ...]
    constraints: tuple[ConstraintSpec, ...]
    indexes: tuple[IndexSpec, ...]


def _sqlalchemy_url(unix_dsn: str) -> str:
    return unix_dsn.replace("postgresql://", "postgresql+psycopg://", 1)


def _fetch_columns(
    conn: psycopg.Connection[tuple[object, ...]],
    table_name: str,
) -> tuple[ColumnSpec, ...]:
    sql = (
        "SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod),"
        " a.attnotnull, a.attidentity, pg_catalog.pg_get_expr(ad.adbin, ad.adrelid)"
        " FROM pg_catalog.pg_attribute a"
        " JOIN pg_catalog.pg_class c ON c.oid = a.attrelid"
        " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
        " LEFT JOIN pg_catalog.pg_attrdef ad"
        "   ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum"
        " WHERE n.nspname = 'public' AND c.relname = %s"
        "   AND a.attnum > 0 AND NOT a.attisdropped"
        " ORDER BY a.attnum"
    )
    rows = conn.execute(sql, (table_name,)).fetchall()
    return tuple(
        ColumnSpec(
            name=str(r[0]),
            data_type=str(r[1]),
            notnull=bool(r[2]),
            identity=str(r[3]),
            default_expr=None if r[4] is None else str(r[4]),
        )
        for r in rows
    )


def _fetch_constraints(
    conn: psycopg.Connection[tuple[object, ...]],
    table_name: str,
) -> tuple[ConstraintSpec, ...]:
    sql = (
        "SELECT con.conname, con.contype,"
        " pg_catalog.pg_get_constraintdef(con.oid, true)"
        " FROM pg_catalog.pg_constraint con"
        " JOIN pg_catalog.pg_class c ON c.oid = con.conrelid"
        " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = 'public' AND c.relname = %s"
        " ORDER BY con.contype, con.conname"
    )
    rows = conn.execute(sql, (table_name,)).fetchall()
    return tuple(ConstraintSpec(str(r[0]), str(r[1]), str(r[2])) for r in rows)


def _fetch_indexes(
    conn: psycopg.Connection[tuple[object, ...]],
    table_name: str,
) -> tuple[IndexSpec, ...]:
    sql = (
        "SELECT i.relname, ix.indisunique, ix.indisprimary,"
        " pg_catalog.pg_get_indexdef(i.oid)"
        " FROM pg_catalog.pg_index ix"
        " JOIN pg_catalog.pg_class t ON t.oid = ix.indrelid"
        " JOIN pg_catalog.pg_class i ON i.oid = ix.indexrelid"
        " JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace"
        " WHERE n.nspname = 'public' AND t.relname = %s"
        " ORDER BY i.relname"
    )
    rows = conn.execute(sql, (table_name,)).fetchall()
    return tuple(
        IndexSpec(
            name=str(r[0]),
            is_unique=bool(r[1]),
            is_primary=bool(r[2]),
            definition=str(r[3]),
        )
        for r in rows
    )


def _introspect_pg_schema(
    dsn: str,
    *,
    exclude_tables: frozenset[str] = frozenset(),
) -> dict[str, TableSpec]:
    """Read complete table, column, constraint, and index metadata from `pg_catalog`."""
    schema: dict[str, TableSpec] = {}
    with psycopg.connect(dsn, autocommit=True) as conn:
        sql = "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public'"
        names = sorted(str(r[0]) for r in conn.execute(sql).fetchall())
        for tbl in names:
            if tbl in exclude_tables:
                continue
            schema[tbl] = TableSpec(
                columns=_fetch_columns(conn, tbl),
                constraints=_fetch_constraints(conn, tbl),
                indexes=_fetch_indexes(conn, tbl),
            )
    return schema


def _assert_allowed_import(path: Path, module_name: str) -> None:
    for bad in _FORBIDDEN_MODEL_IMPORTS:
        assert not module_name.startswith(bad), f"{path.name} imports {module_name}"


def _check_model_ast(path: Path, tree: ast.Module) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _assert_allowed_import(path, alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            _assert_allowed_import(path, node.module)
        elif isinstance(node, ast.Name) and node.id == "Any":
            raise AssertionError(f"{path.name} must not use `Any`")
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "print":
                raise AssertionError(f"{path.name} must not call `print()`")
        elif isinstance(node, ast.ExceptHandler):
            exc = node.type
            exc_name = exc.id if isinstance(exc, ast.Name) else ""
            if exc is None or exc_name in {"Exception", "BaseException"}:
                raise AssertionError(f"{path.name} must not use broad except")
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            span = (node.end_lineno or node.lineno) - node.lineno + 1
            limit = 400 if isinstance(node, ast.ClassDef) else 100
            assert span <= limit, f"{path.name}:{node.name} exceeds {limit} LOC"


def _seed_roundtrip_rows(session: Session) -> int:
    queue_item = ManualQueueItem(
        created_ts=1700000100.0,
        updated_ts=1700000100.0,
        kind="UPGRADE_BUILDING",
        planet_id=12476,
        planet_coords="02:380:12",
        target_id=1,
        target_name="Pig iron mine",
        target_level=16,
    )
    session.add(queue_item)
    session.flush()
    assert queue_item.id >= 1
    queue_id = queue_item.id

    session.add_all(
        [
            ManualQueueEvent(
                ts=1700000101.0,
                queue_id=queue_id,
                event_type="enqueued",
                details_json='{"source":"test"}',
            ),
            GalaxyScan(galaxy=2, system=380, last_scanned_ts=1700000102.0),
            SpyTarget(coords="02:380:04", last_scanned_ts=1700000103.0),
            ColonyCandidateHot(
                coords="02:380:08",
                galaxy=2,
                system=380,
                position=8,
                max_fields_est=330,
            ),
            UiNotification(
                created_ts=1700000104.0,
                updated_ts=1700000104.0,
                title="Test notice",
                message="Round-trip check",
            ),
            StrategyConfigStore(
                id=1,
                config_json='{"planner_mode":"priority"}',
                updated_ts=1700000105.0,
            ),
            MetaEntry(key="schema_version", value="14", updated_ts=1700000106.0),
            HourlyProductionHistory(minute_bucket=28333335, recorded_at=1700000107.0),
        ]
    )
    session.commit()
    return queue_id


def test_orm_metadata_covers_exact_required_tables() -> None:
    """`Base.metadata` declares exactly the 40 `GAME_REQUIRED_TABLES`."""
    orm_tables = set(Base.metadata.tables.keys())
    assert orm_tables == set(GAME_REQUIRED_TABLES)
    assert orm_tables == set(HOT_TABLES) | set(HISTORY_TABLES)
    assert len(orm_tables) == 40


def test_orm_schema_matches_migrated_postgres_catalog_exactly(
    pg_harness: DisposablePostgresServer,
) -> None:
    """`Base.metadata.create_all` matches Alembic `head` (`0004`) catalog 1:1 and `0001` matches `si_template`."""
    migrated_schema = _introspect_pg_schema(
        pg_harness.template_dsn,
        exclude_tables=_INTERNAL_EMULATION_TABLES,
    )

    alembic_dsn = pg_harness.create_database(prefix="orm_alembic", from_template=False)
    alembic_eng = create_engine(_sqlalchemy_url(alembic_dsn))
    try:
        upgrade_database(alembic_eng, BASELINE_REVISION, backup_fn=NO_BACKUP)
        upgrade_database(alembic_eng, "head", backup_fn=NO_BACKUP)
    finally:
        alembic_eng.dispose()
    head_schema = _introspect_pg_schema(
        alembic_dsn,
        exclude_tables=_INTERNAL_EMULATION_TABLES | {"alembic_version"},
    )
    assert set(head_schema.keys()) == set(migrated_schema.keys())
    for tbl in sorted(migrated_schema.keys()):
        assert head_schema[tbl].columns == migrated_schema[tbl].columns, f"head col mismatch on {tbl}"
        assert head_schema[tbl].constraints == migrated_schema[tbl].constraints, f"head con mismatch on {tbl}"
        assert head_schema[tbl].indexes == migrated_schema[tbl].indexes, f"head idx mismatch on {tbl}"

    orm_dsn = pg_harness.create_database(prefix="orm_equiv", from_template=False)
    engine = create_engine(_sqlalchemy_url(orm_dsn))
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()

    orm_schema = _introspect_pg_schema(orm_dsn)
    assert set(orm_schema.keys()) == set(head_schema.keys())
    assert set(orm_schema.keys()) == set(GAME_REQUIRED_TABLES)

    for tbl in sorted(head_schema.keys()):
        expected = head_schema[tbl]
        actual = orm_schema[tbl]
        assert actual.columns == expected.columns, f"Column mismatch on {tbl}"
        assert actual.constraints == expected.constraints, f"Constraint mismatch on {tbl}"
        assert actual.indexes == expected.indexes, f"Index mismatch on {tbl}"

    all_indexes = [idx for spec in orm_schema.values() for idx in spec.indexes]
    non_pk = [idx for idx in all_indexes if not idx.is_primary]
    explicit_non_unique = [idx for idx in non_pk if not idx.is_unique]
    assert len(non_pk) == 70
    assert len(explicit_non_unique) == 66


def test_models_package_standards_layering_and_lazy_policy() -> None:
    """Model modules obey Section 4 layering, LOC limits, typing, and lazy policy."""
    model_files = sorted(_MODELS_DIR.glob("*.py"))
    assert len(model_files) == 13

    for path in model_files:
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        assert len(lines) <= 400, f"{path.name} exceeds 400 LOC ({len(lines)})"
        tree = ast.parse(source, filename=str(path))
        assert ast.get_docstring(tree), f"{path.name} is missing a docstring"
        _check_model_ast(path, tree)

    relationships: list[tuple[str, RelationshipProperty[object]]] = []
    for mapper in Base.registry.mappers:
        for prop in mapper.relationships:
            relationships.append((f"{mapper.class_.__name__}.{prop.key}", prop))
    assert relationships, "Expected mapped relationships to test lazy='raise_on_sql'"
    for rel_name, prop in relationships:
        assert prop.lazy == "raise_on_sql", f"{rel_name} must set lazy='raise_on_sql'"


def test_session_roundtrip_and_lazy_raise_on_sql_runtime(
    pg_harness: DisposablePostgresServer,
) -> None:
    """ORM `Session` handles inserts, defaults, reads, and blocks lazy SQL."""
    orm_dsn = pg_harness.create_database(prefix="orm_rt", from_template=False)
    engine = create_engine(_sqlalchemy_url(orm_dsn))
    try:
        Base.metadata.create_all(engine)

        with Session(engine) as session:
            queue_id = _seed_roundtrip_rows(session)

        with Session(engine) as session:
            item_q = select(ManualQueueItem).where(ManualQueueItem.id == queue_id)
            loaded_item = session.execute(item_q).scalar_one()
            assert loaded_item.status == "pending"
            assert loaded_item.priority == "P1"
            assert loaded_item.sort_rank == 1000

            with pytest.raises(InvalidRequestError):
                _ = loaded_item.events

            eager_q = item_q.options(selectinload(ManualQueueItem.events))
            eager_item = session.execute(eager_q).scalar_one()
            assert len(eager_item.events) == 1
            assert eager_item.events[0].event_type == "enqueued"

            scan_q = select(GalaxyScan).where(
                GalaxyScan.galaxy == 2,
                GalaxyScan.system == 380,
            )
            assert session.execute(scan_q).scalar_one().occupied_count == 0

            spy_q = select(SpyTarget).where(SpyTarget.coords == "02:380:04")
            target = session.execute(spy_q).scalar_one()
            assert target.status_color == "yellow"
            assert target.fleet_json == "{}"

            cand_q = select(ColonyCandidateHot).where(
                ColonyCandidateHot.coords == "02:380:08",
            )
            assert session.execute(cand_q).scalar_one().is_active == 1

            notif = session.execute(select(UiNotification)).scalar_one()
            assert notif.status == "unread"
            assert notif.occurrence_count == 1

            cfg_q = select(StrategyConfigStore).where(StrategyConfigStore.id == 1)
            assert session.execute(cfg_q).scalar_one().source == "sqlite"

            meta_q = select(MetaEntry).where(MetaEntry.key == "schema_version")
            assert session.execute(meta_q).scalar_one().value == "14"

            prod_q = select(HourlyProductionHistory).where(
                HourlyProductionHistory.minute_bucket == 28333335,
            )
            assert session.execute(prod_q).scalar_one().pig_iron_per_hour == 0.0
    finally:
        engine.dispose()
