"""SQLAlchemy 2.0 ORM PostgreSQL database layer, Alembic migrations, and DSN helpers."""

from __future__ import annotations

from spaceinvasion.db import dsn, migrations
from spaceinvasion.db.dsn import (
    DEFAULT_DB_BACKEND,
    DEFAULT_PG_DB,
    DEFAULT_PG_HOST,
    DEFAULT_PG_PASSWORD,
    DEFAULT_PG_PORT,
    DEFAULT_PG_USER,
    VALID_DB_BACKENDS,
    ResolvedDatabaseConfig,
    build_default_postgres_url,
    get_unix_socket_host,
    is_postgres_dsn,
    is_unix_socket_dsn,
    normalize_backend_name,
    redact_db_url,
    resolve_database_config,
)
from spaceinvasion.db.session import (
    DatabaseEngine,
    UnitOfWork,
    count_statements,
    db_uow,
    normalize_sqlalchemy_dsn,
    ping_postgres,
)
from spaceinvasion.db.store import DatabaseStore, StoreRepositories

__all__ = [
    "DEFAULT_DB_BACKEND",
    "DEFAULT_PG_DB",
    "DEFAULT_PG_HOST",
    "DEFAULT_PG_PASSWORD",
    "DEFAULT_PG_PORT",
    "DEFAULT_PG_USER",
    "VALID_DB_BACKENDS",
    "DatabaseEngine",
    "DatabaseStore",
    "ResolvedDatabaseConfig",
    "StoreRepositories",
    "UnitOfWork",
    "build_default_postgres_url",
    "count_statements",
    "db_uow",
    "dsn",
    "get_unix_socket_host",
    "is_postgres_dsn",
    "is_unix_socket_dsn",
    "migrations",
    "normalize_backend_name",
    "normalize_sqlalchemy_dsn",
    "ping_postgres",
    "redact_db_url",
    "resolve_database_config",
]
