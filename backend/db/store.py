"""Explicit `DatabaseStore` composition root wiring `DatabaseEngine`, Alembic, repos, and queries."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import TracebackType

from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.db.dsn import redact_db_url
from backend.db.dto import DbSummaryRecord, DbVerifyRecord
from backend.db.migrations import (
    BackupOption,
    MigrationResult,
    SchemaDriftError,
    get_current_revision,
    stamp_baseline_database,
    upgrade_database,
    verify_models_match_migrations,
)
from backend.db.queries.dashboard import GAME_REQUIRED_TABLES, query_db_summary, query_db_verify
from backend.db.repos import (
    ColoniesRepository,
    GalaxyRepository,
    IntelRepository,
    ManualQueueRepository,
    MessagesRepository,
    NotificationsRepository,
    PlannerRunsRepository,
    RaidsRepository,
    SettingsRepository,
    SnapshotsRepository,
    TelemetryRepository,
)
from backend.db.session import (
    DEFAULT_MAX_OVERFLOW,
    DEFAULT_POOL_SIZE,
    DEFAULT_POOL_TIMEOUT_S,
    DEFAULT_STATEMENT_TIMEOUT_MS,
    DatabaseEngine,
    SessionFactory,
    UnitOfWork,
    db_uow,
)


@dataclass(frozen=True)
class StoreRepositories:
    """All 11 typed ORM repositories bound to a single active unit-of-work `Session`."""

    session: Session
    notifications: NotificationsRepository
    queue: ManualQueueRepository
    settings: SettingsRepository
    planner_runs: PlannerRunsRepository
    telemetry: TelemetryRepository
    colonies: ColoniesRepository
    raids: RaidsRepository
    messages: MessagesRepository
    snapshots: SnapshotsRepository
    intel: IntelRepository
    galaxy: GalaxyRepository

    @classmethod
    def from_session(cls, session: Session) -> StoreRepositories:
        """Bind all 11 ORM repositories to an open transactional `Session`."""
        return cls(
            session=session,
            notifications=NotificationsRepository(session),
            queue=ManualQueueRepository(session),
            settings=SettingsRepository(session),
            planner_runs=PlannerRunsRepository(session),
            telemetry=TelemetryRepository(session),
            colonies=ColoniesRepository(session),
            raids=RaidsRepository(session),
            messages=MessagesRepository(session),
            snapshots=SnapshotsRepository(session),
            intel=IntelRepository(session),
            galaxy=GalaxyRepository(session),
        )


class DatabaseStore:
    """Collaborator-friendly PostgreSQL composition root for SpaceInvasion services."""

    def __init__(self, db_engine: DatabaseEngine, *, postgres_url: str = "") -> None:
        self._db_engine = db_engine
        self._postgres_url = postgres_url.strip()

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
    ) -> DatabaseStore:
        """Build a `DatabaseStore` from a PostgreSQL TCP or Unix-socket DSN."""
        db_engine = DatabaseEngine.from_dsn(
            dsn,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=pool_pre_ping,
            pool_timeout=pool_timeout,
            statement_timeout_ms=statement_timeout_ms,
            echo=echo,
        )
        return cls(db_engine, postgres_url=dsn)

    @property
    def postgres_url(self) -> str:
        """Configured PostgreSQL DSN (empty string when built from a bare `DatabaseEngine`)."""
        return self._postgres_url

    @property
    def display_target(self) -> str:
        """Redacted PostgreSQL connection target for logs and diagnostics."""
        return redact_db_url(self._postgres_url) if self._postgres_url else "postgres"

    @property
    def db_engine(self) -> DatabaseEngine:
        """Managed `DatabaseEngine` wrapper."""
        return self._db_engine

    @property
    def engine(self) -> Engine:
        """Underlying SQLAlchemy 2.0 `Engine`."""
        return self._db_engine.engine

    @property
    def session_factory(self) -> SessionFactory:
        """Configured SQLAlchemy `sessionmaker` (`expire_on_commit=False`)."""
        return self._db_engine.session_factory

    def uow(self) -> UnitOfWork:
        """Create an explicit `UnitOfWork` context manager for one atomic transaction."""
        return self._db_engine.unit_of_work()

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Run one atomic transaction yielding the active SQLAlchemy `Session`."""
        with db_uow(self._db_engine) as active_session:
            yield active_session

    def bind(self, session: Session) -> StoreRepositories:
        """Bind all 11 typed ORM repositories to an existing active `Session`."""
        return StoreRepositories.from_session(session)

    @contextmanager
    def repositories(self) -> Iterator[StoreRepositories]:
        """Run one atomic transaction yielding all 11 repositories bound to the same `Session`."""
        with db_uow(self._db_engine) as active_session:
            yield StoreRepositories.from_session(active_session)

    def notifications_repo(self, session: Session) -> NotificationsRepository:
        """Bind `NotificationsRepository` to `session`."""
        return NotificationsRepository(session)

    def queue_repo(self, session: Session) -> ManualQueueRepository:
        """Bind `ManualQueueRepository` to `session`."""
        return ManualQueueRepository(session)

    def settings_repo(self, session: Session) -> SettingsRepository:
        """Bind `SettingsRepository` to `session`."""
        return SettingsRepository(session)

    def planner_runs_repo(self, session: Session) -> PlannerRunsRepository:
        """Bind `PlannerRunsRepository` to `session`."""
        return PlannerRunsRepository(session)

    def telemetry_repo(self, session: Session) -> TelemetryRepository:
        """Bind `TelemetryRepository` to `session`."""
        return TelemetryRepository(session)

    def colonies_repo(self, session: Session) -> ColoniesRepository:
        """Bind `ColoniesRepository` to `session`."""
        return ColoniesRepository(session)

    def raids_repo(self, session: Session) -> RaidsRepository:
        """Bind `RaidsRepository` to `session`."""
        return RaidsRepository(session)

    def messages_repo(self, session: Session) -> MessagesRepository:
        """Bind `MessagesRepository` to `session`."""
        return MessagesRepository(session)

    def snapshots_repo(self, session: Session) -> SnapshotsRepository:
        """Bind `SnapshotsRepository` to `session`."""
        return SnapshotsRepository(session)

    def intel_repo(self, session: Session) -> IntelRepository:
        """Bind `IntelRepository` to `session`."""
        return IntelRepository(session)

    def galaxy_repo(self, session: Session) -> GalaxyRepository:
        """Bind `GalaxyRepository` to `session`."""
        return GalaxyRepository(session)

    def ensure_ready(
        self,
        *,
        backup_fn: BackupOption,
        allow_stamp_existing: bool = True,
        verify_schema: bool = True,
    ) -> MigrationResult:
        """Ensure the PostgreSQL schema is at Alembic `head` safely.

        - If `alembic_version` is absent and existing V14 game tables already exist
          (and `allow_stamp_existing=True`), stamps `0001` cleanly with zero table DDL
          before running any post-baseline upgrades.
        - Otherwise runs `upgrade_database(..., target_revision="head", backup_fn=backup_fn)`.
        """
        current_rev = get_current_revision(self._db_engine)
        if current_rev is None and allow_stamp_existing:
            existing = set(inspect(self._db_engine.engine).get_table_names(schema="public"))
            if all(tbl in existing for tbl in GAME_REQUIRED_TABLES):
                stamp_res = stamp_baseline_database(self._db_engine, verify_schema=verify_schema)
                upgrade_res = upgrade_database(self._db_engine, "head", backup_fn=backup_fn)
                if verify_schema and upgrade_res.applied_revisions:
                    diffs = verify_models_match_migrations(self._db_engine)
                    if diffs:
                        raise SchemaDriftError(diffs)
                return upgrade_res if upgrade_res.applied_revisions else stamp_res
        res = upgrade_database(self._db_engine, "head", backup_fn=backup_fn)
        if verify_schema:
            diffs = verify_models_match_migrations(self._db_engine)
            if diffs:
                raise SchemaDriftError(diffs)
        return res

    def summary(
        self,
        *,
        recent_actions_limit: int = 12,
        spy_targets_limit: int = 25,
    ) -> DbSummaryRecord:
        """Execute `query_db_summary` inside a dedicated read transaction."""
        with self.session() as active_session:
            return query_db_summary(
                active_session,
                recent_actions_limit=recent_actions_limit,
                spy_targets_limit=spy_targets_limit,
            )

    def verify(self) -> DbVerifyRecord:
        """Execute `query_db_verify` inside a dedicated read transaction."""
        with self.session() as active_session:
            return query_db_verify(active_session, self._db_engine)

    def snapshot_xid(self) -> int:
        """Return PostgreSQL `pg_snapshot_xmax(pg_current_snapshot())` for cross-process cache invalidation."""
        try:
            with self._db_engine.engine.connect() as conn:
                raw = conn.execute(text("SELECT pg_snapshot_xmax(pg_current_snapshot())::text")).scalar()
                conn.rollback()
            return int(str(raw)) if raw is not None else 0
        except (SQLAlchemyError, ValueError, TypeError):
            return 0

    def close(self) -> None:
        """Dispose pooled SQLAlchemy connections."""
        self._db_engine.dispose()

    def __enter__(self) -> DatabaseStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


__all__ = ["DatabaseStore", "StoreRepositories"]
