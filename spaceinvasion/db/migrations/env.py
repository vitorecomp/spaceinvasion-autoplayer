"""Alembic migration environment for SpaceInvasion PostgreSQL schemas."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

from spaceinvasion.db.models import Base

_IGNORED_TABLES: frozenset[str] = frozenset({"_pg_pragma_meta", "alembic_version"})
target_metadata = Base.metadata


def include_object(
    obj: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object,
) -> bool:
    """Exclude Alembic version metadata and legacy PRAGMA emulation table from diffs."""
    del obj, reflected, compare_to
    return not (type_ == "table" and name in _IGNORED_TABLES)


def run_migrations_offline() -> None:
    """Run migrations in offline SQL generation mode."""
    url = context.config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations online, reusing an injected `Connection` when provided."""
    shared_conn = context.config.attributes.get("connection")
    if isinstance(shared_conn, Connection):
        _run_with_connection(shared_conn)
        return

    section = context.config.get_section(context.config.config_ini_section) or {}
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            _run_with_connection(connection)
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
