"""SQLAlchemy ORM models for metadata, settings, config history, and migrations."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models.base import Base, identity_pk


class MetaEntry(Base):
    """Key-value store for runtime metadata, schema markers, and labels."""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column()
    updated_ts: Mapped[float] = mapped_column()


class StrategyConfigStore(Base):
    """Singleton persisted strategy configuration JSON document."""

    __tablename__ = "strategy_config_store"
    __table_args__ = (CheckConstraint("id = 1", name="strategy_config_store_id_check"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    config_json: Mapped[str] = mapped_column()
    updated_ts: Mapped[float] = mapped_column()
    source: Mapped[str] = mapped_column(server_default=text("'sqlite'"))


class ConfigHistory(Base):
    """Append-only history of strategy configuration revisions."""

    __tablename__ = "config_history"
    __table_args__ = (Index("idx_config_history_ts", "ts"),)

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    ts: Mapped[float] = mapped_column()
    config_json: Mapped[str] = mapped_column()


class SchemaMigration(Base):
    """Applied forward-only schema migration ledger with SHA-256 checksums."""

    __tablename__ = "schema_migrations"

    version: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    version_tag: Mapped[str] = mapped_column()
    name: Mapped[str] = mapped_column()
    description: Mapped[str] = mapped_column(server_default=text("''"))
    checksum: Mapped[str] = mapped_column()
    applied_ts: Mapped[float] = mapped_column()
    duration_ms: Mapped[float] = mapped_column(server_default=text("0.0"))
    app_schema_version: Mapped[str] = mapped_column(server_default=text("''"))
    details_json: Mapped[str] = mapped_column(server_default=text("'{}'"))


class SchemaMigrationEvent(Base):
    """Append-only audit trail of migration and backup lifecycle events."""

    __tablename__ = "schema_migration_events"
    __table_args__ = (Index("idx_schema_migration_events_ts", "ts"),)

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    ts: Mapped[float] = mapped_column()
    event_type: Mapped[str] = mapped_column()
    from_version: Mapped[int] = mapped_column(server_default=text("0"))
    to_version: Mapped[int] = mapped_column(server_default=text("0"))
    version: Mapped[int | None] = mapped_column()
    name: Mapped[str] = mapped_column(server_default=text("''"))
    checksum: Mapped[str] = mapped_column(server_default=text("''"))
    backup_path: Mapped[str] = mapped_column(server_default=text("''"))
    details_json: Mapped[str] = mapped_column(server_default=text("'{}'"))


__all__ = [
    "ConfigHistory",
    "MetaEntry",
    "SchemaMigration",
    "SchemaMigrationEvent",
    "StrategyConfigStore",
]
