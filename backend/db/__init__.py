"""SQLAlchemy 2.0 ORM PostgreSQL database layer, Alembic migrations, and DSN helpers."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from backend.db import dsn
from backend.db.dsn import (
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

if TYPE_CHECKING:
    from backend.db import migrations as migrations
    from backend.db.session import (
        DatabaseEngine as DatabaseEngine,
    )
    from backend.db.session import (
        UnitOfWork as UnitOfWork,
    )
    from backend.db.session import (
        count_statements as count_statements,
    )
    from backend.db.session import (
        db_uow as db_uow,
    )
    from backend.db.session import (
        normalize_sqlalchemy_dsn as normalize_sqlalchemy_dsn,
    )
    from backend.db.session import (
        ping_postgres as ping_postgres,
    )
    from backend.db.store import (
        DatabaseStore as DatabaseStore,
    )
    from backend.db.store import (
        StoreRepositories as StoreRepositories,
    )

_LAZY_SUBMODULES: frozenset[str] = frozenset({"migrations", "session", "store"})
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "DatabaseEngine": ("backend.db.session", "DatabaseEngine"),
    "DatabaseStore": ("backend.db.store", "DatabaseStore"),
    "StoreRepositories": ("backend.db.store", "StoreRepositories"),
    "UnitOfWork": ("backend.db.session", "UnitOfWork"),
    "count_statements": ("backend.db.session", "count_statements"),
    "db_uow": ("backend.db.session", "db_uow"),
    "normalize_sqlalchemy_dsn": ("backend.db.session", "normalize_sqlalchemy_dsn"),
    "ping_postgres": ("backend.db.session", "ping_postgres"),
}


def __getattr__(name: str) -> object:
    """Lazily load SQLAlchemy/Alembic DB exports so pure-Python notebook imports stay light."""
    if name in _LAZY_SUBMODULES:
        return importlib.import_module(f"backend.db.{name}")
    spec = _LAZY_EXPORTS.get(name)
    if spec is not None:
        mod = importlib.import_module(spec[0])
        return mod.__dict__[spec[1]]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
