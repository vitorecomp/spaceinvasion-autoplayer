"""SQLAlchemy 2.0 PostgreSQL engine, unit of work, transitional bridge, and SQL counter."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
import time
from types import TracebackType
from typing import Unpack

import psycopg
from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.engine import CursorResult, make_url
from sqlalchemy.orm import Session, sessionmaker

from backend.core.redaction import redact_sensitive
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

DEFAULT_POOL_SIZE: int = 5
DEFAULT_MAX_OVERFLOW: int = 10
DEFAULT_POOL_TIMEOUT_S: float = 30.0
DEFAULT_STATEMENT_TIMEOUT_MS: int = 30_000

SessionFactory = sessionmaker[Session]
SqlParams = Sequence[object] | Mapping[str, object] | None


class StatementCountExceededError(AssertionError):
    """Raised when a `count_statements` block exceeds its configured SQL budget."""


@dataclass
class StatementCounter:
    """Recorded SQL statements executed against a SQLAlchemy `Engine`."""

    statements: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Total number of captured SQL statements."""
        return len(self.statements)


def normalize_sqlalchemy_dsn(dsn: str) -> str:
    """Normalize a PostgreSQL DSN (TCP or Unix-socket) to use `postgresql+psycopg://`."""
    cleaned = dsn.strip()
    if not cleaned:
        raise ValueError("PostgreSQL DSN must be non-empty")
    if cleaned.startswith("postgresql+psycopg://"):
        return cleaned
    if cleaned.startswith("postgresql://"):
        return "postgresql+psycopg://" + cleaned[len("postgresql://") :]
    if cleaned.startswith("postgres://"):
        return "postgresql+psycopg://" + cleaned[len("postgres://") :]
    raise ValueError(f"Unsupported database DSN scheme (PostgreSQL required): {cleaned.split(':', 1)[0]!r}")


def create_db_engine(
    dsn: str,
    *,
    pool_size: int = DEFAULT_POOL_SIZE,
    max_overflow: int = DEFAULT_MAX_OVERFLOW,
    pool_pre_ping: bool = True,
    pool_timeout: float = DEFAULT_POOL_TIMEOUT_S,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
    echo: bool = False,
) -> Engine:
    """Create a pooled SQLAlchemy 2.0 PostgreSQL engine with statement-timeout enforcement."""
    if pool_size < 1:
        raise ValueError(f"pool_size must be >= 1, got {pool_size}")
    if max_overflow < 0:
        raise ValueError(f"max_overflow must be >= 0, got {max_overflow}")
    if statement_timeout_ms < 0:
        raise ValueError(f"statement_timeout_ms must be >= 0, got {statement_timeout_ms}")

    normalized = normalize_sqlalchemy_dsn(dsn)
    url = make_url(normalized)
    connect_args: dict[str, object] = {}
    if statement_timeout_ms > 0:
        existing_options = url.query.get("options")
        timeout_opt = f"-c statement_timeout={int(statement_timeout_ms)}"
        if isinstance(existing_options, str) and existing_options.strip():
            connect_args["options"] = f"{existing_options.strip()} {timeout_opt}"
        else:
            connect_args["options"] = timeout_opt

    return create_engine(
        normalized,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=pool_pre_ping,
        pool_timeout=pool_timeout,
        connect_args=connect_args,
        echo=echo,
        future=True,
    )


def create_session_factory(engine: Engine) -> SessionFactory:
    """Build a typed SQLAlchemy 2.0 `sessionmaker` with `expire_on_commit=False`."""
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, autoflush=True)


def _extract_psycopg_connection(sa_conn: Connection) -> psycopg.Connection[tuple[object, ...]]:
    """Return the underlying `psycopg.Connection` bound to an active SQLAlchemy `Connection`."""
    raw = sa_conn.connection.driver_connection
    if not isinstance(raw, psycopg.Connection):
        raise TypeError(f"Expected psycopg.Connection driver connection, got {type(raw).__name__}")
    return raw


@contextmanager
def session_raw_cursor(session: Session) -> Iterator[psycopg.Cursor[tuple[object, ...]]]:
    """Yield a raw psycopg cursor bound to the active ORM `Session` transaction.

    Flushes pending ORM writes first so raw SQL and ORM reads share one atomic view.
    Never commits, rolls back, or closes the underlying connection; the enclosing
    unit of work owns transaction completion.
    """
    session.flush()
    raw_conn = _extract_psycopg_connection(session.connection())
    with raw_conn.cursor() as cursor:
        yield cursor


class TransitionalSessionBridge:
    """Transitional DBAPI/SQL bridge sharing the active ORM `Session` transaction.

    Allows legacy repository SQL and new SQLAlchemy ORM repositories to execute
    inside the same database transaction during Phase 3b migration without
    committing in two halves.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        """Underlying active SQLAlchemy `Session`."""
        return self._session

    @property
    def sa_connection(self) -> Connection:
        """Active SQLAlchemy `Connection` bound to the current transaction."""
        self._session.flush()
        return self._session.connection()

    def execute(self, sql: str, params: SqlParams = None) -> CursorResult[Unpack[tuple[object, ...]]]:
        """Execute driver-level SQL on `session.connection()` inside the shared transaction."""
        conn = self.sa_connection
        if params is None:
            return conn.exec_driver_sql(sql)
        return conn.exec_driver_sql(sql, params)

    @contextmanager
    def raw_cursor(self) -> Iterator[psycopg.Cursor[tuple[object, ...]]]:
        """Yield a raw `psycopg.Cursor` on the shared DBAPI connection."""
        with session_raw_cursor(self._session) as cursor:
            yield cursor


class UnitOfWork:
    """Explicit unit-of-work context manager owning one atomic database transaction."""

    def __init__(self, source: SessionFactory | DatabaseEngine) -> None:
        self._factory: SessionFactory = source.session_factory if isinstance(source, DatabaseEngine) else source
        self._session: Session | None = None
        self._bridge: TransitionalSessionBridge | None = None

    @property
    def session(self) -> Session:
        """Active ORM `Session` (valid only while inside the `with` block)."""
        if self._session is None:
            raise RuntimeError("UnitOfWork is not active; enter it with a `with` statement")
        return self._session

    @property
    def connection(self) -> Connection:
        """Active SQLAlchemy `Connection` bound to the unit-of-work transaction."""
        self.session.flush()
        return self.session.connection()

    @property
    def bridge(self) -> TransitionalSessionBridge:
        """Transitional raw-SQL / DBAPI bridge bound to this unit of work."""
        if self._bridge is None:
            raise RuntimeError("UnitOfWork is not active; enter it with a `with` statement")
        return self._bridge

    def flush(self) -> None:
        """Flush pending ORM changes into the active transaction without committing."""
        self.session.flush()

    @contextmanager
    def raw_cursor(self) -> Iterator[psycopg.Cursor[tuple[object, ...]]]:
        """Yield a raw `psycopg.Cursor` bound to this unit-of-work transaction."""
        with session_raw_cursor(self.session) as cursor:
            yield cursor

    def __enter__(self) -> UnitOfWork:
        session = self._factory()
        session.begin()
        self._session = session
        self._bridge = TransitionalSessionBridge(session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        session = self._session
        self._session = None
        self._bridge = None
        if session is None:
            return
        try:
            if exc_type is None:
                session.commit()
            else:
                session.rollback()
        finally:
            session.close()


@dataclass(frozen=True)
class DatabaseEngine:
    """Managed SQLAlchemy 2.0 PostgreSQL engine and session factory."""

    engine: Engine
    session_factory: SessionFactory
    statement_timeout_ms: int
    pool_size: int

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        pool_size: int = DEFAULT_POOL_SIZE,
        max_overflow: int = DEFAULT_MAX_OVERFLOW,
        pool_pre_ping: bool = True,
        pool_timeout: float = DEFAULT_POOL_TIMEOUT_S,
        statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
        echo: bool = False,
    ) -> DatabaseEngine:
        """Construct a `DatabaseEngine` and `SessionFactory` from a PostgreSQL DSN."""
        engine = create_db_engine(
            dsn,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=pool_pre_ping,
            pool_timeout=pool_timeout,
            statement_timeout_ms=statement_timeout_ms,
            echo=echo,
        )
        return cls(engine, create_session_factory(engine), statement_timeout_ms, pool_size)

    def unit_of_work(self) -> UnitOfWork:
        """Create a `UnitOfWork` bound to this engine's `SessionFactory`."""
        return UnitOfWork(self.session_factory)

    def dispose(self) -> None:
        """Close pooled connections held by the underlying SQLAlchemy engine."""
        self.engine.dispose()

    def __enter__(self) -> DatabaseEngine:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.dispose()


@contextmanager
def db_uow(source: SessionFactory | DatabaseEngine) -> Iterator[Session]:
    """Run one atomic transaction per API command or commander tick."""
    factory = source.session_factory if isinstance(source, DatabaseEngine) else source
    with factory() as session:
        with session.begin():
            yield session


def _unwrap_engine(engine: Engine | DatabaseEngine) -> Engine:
    return engine.engine if isinstance(engine, DatabaseEngine) else engine


@contextmanager
def count_statements(
    engine: Engine | DatabaseEngine,
    *,
    max_statements: int | None = None,
) -> Iterator[StatementCounter]:
    """Count SQL statements executed on `engine` via `before_cursor_execute`.

    Optionally raises `StatementCountExceededError` on normal exit if the
    recorded count exceeds `max_statements`.
    """
    if max_statements is not None and max_statements < 0:
        raise ValueError(f"max_statements must be >= 0, got {max_statements}")

    sa_engine = _unwrap_engine(engine)
    counter = StatementCounter()

    def _on_before_cursor_execute(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del conn, cursor, parameters, context, executemany
        counter.statements.append(statement)

    event.listen(sa_engine, "before_cursor_execute", _on_before_cursor_execute)
    try:
        yield counter
    finally:
        event.remove(sa_engine, "before_cursor_execute", _on_before_cursor_execute)

    if max_statements is not None and counter.count > max_statements:
        preview = "\n  ".join(counter.statements[:10])
        raise StatementCountExceededError(
            f"Executed {counter.count} SQL statements (limit {max_statements}):\n  {preview}"
        )


def ping_postgres(dsn: str, *, timeout: float = 2.0) -> tuple[bool, float, str]:
    """Ping a PostgreSQL DSN (`server_version`) and return `(ok, latency_ms, message)`."""
    t0 = time.perf_counter()
    try:
        with psycopg.connect(dsn, connect_timeout=max(1, int(round(timeout)))) as conn:
            row = conn.execute("SELECT current_setting('server_version')").fetchone()
            ver = str(row[0]) if row and row[0] else "PostgreSQL"
        return (True, round((time.perf_counter() - t0) * 1000.0, 2), f"PostgreSQL {ver}")
    except (psycopg.Error, OSError, ValueError) as exc:
        return (False, round((time.perf_counter() - t0) * 1000.0, 2), str(redact_sensitive(str(exc))))


__all__ = [
    "DEFAULT_DB_BACKEND",
    "DEFAULT_MAX_OVERFLOW",
    "DEFAULT_PG_DB",
    "DEFAULT_PG_HOST",
    "DEFAULT_PG_PASSWORD",
    "DEFAULT_PG_PORT",
    "DEFAULT_PG_USER",
    "DEFAULT_POOL_SIZE",
    "DEFAULT_POOL_TIMEOUT_S",
    "DEFAULT_STATEMENT_TIMEOUT_MS",
    "VALID_DB_BACKENDS",
    "DatabaseEngine",
    "ResolvedDatabaseConfig",
    "SessionFactory",
    "SqlParams",
    "StatementCountExceededError",
    "StatementCounter",
    "TransitionalSessionBridge",
    "UnitOfWork",
    "build_default_postgres_url",
    "count_statements",
    "create_db_engine",
    "create_session_factory",
    "db_uow",
    "get_unix_socket_host",
    "is_postgres_dsn",
    "is_unix_socket_dsn",
    "normalize_backend_name",
    "normalize_sqlalchemy_dsn",
    "ping_postgres",
    "redact_db_url",
    "resolve_database_config",
    "session_raw_cursor",
]
