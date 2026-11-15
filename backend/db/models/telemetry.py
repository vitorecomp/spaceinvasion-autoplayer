"""SQLAlchemy ORM models for action history and hourly production telemetry."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, desc, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models.base import Base, identity_pk


class ActionHistory(Base):
    """Append-only audit log of executed and dry-run commander actions."""

    __tablename__ = "action_history"
    __table_args__ = (
        CheckConstraint("executed IN (0, 1)", name="ck_action_history_executed"),
        CheckConstraint("dry_run IN (0, 1)", name="ck_action_history_dry_run"),
        Index("idx_action_history_ts", "ts"),
        Index("idx_action_history_ts_brin", "ts", postgresql_using="brin"),
        Index(
            "idx_action_history_exec_kind_pid_ts",
            "kind",
            "planet_id",
            desc("ts"),
            postgresql_where=text("executed = 1 AND dry_run = 0"),
        ),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    ts: Mapped[float] = mapped_column()
    source: Mapped[str] = mapped_column()
    command: Mapped[str] = mapped_column()
    priority: Mapped[str | None] = mapped_column()
    kind: Mapped[str] = mapped_column()
    planet_id: Mapped[int | None] = mapped_column()
    planet_coords: Mapped[str | None] = mapped_column()
    target_id: Mapped[int | None] = mapped_column()
    target_name: Mapped[str | None] = mapped_column()
    target_level: Mapped[int | None] = mapped_column()
    executed: Mapped[int] = mapped_column()
    dry_run: Mapped[int] = mapped_column()
    reason: Mapped[str | None] = mapped_column()
    message: Mapped[str | None] = mapped_column()


class HourlyProductionHistory(Base):
    """Minute-bucketed empire resource production rate telemetry."""

    __tablename__ = "hourly_production_history"
    __table_args__ = (
        Index("idx_hourly_production_minute", desc("minute_bucket")),
        Index("idx_hourly_production_recorded_brin", "recorded_at", postgresql_using="brin"),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    minute_bucket: Mapped[int] = mapped_column(unique=True)
    recorded_at: Mapped[float] = mapped_column()
    pig_iron_per_hour: Mapped[float] = mapped_column(server_default=text("0"))
    metal_per_hour: Mapped[float] = mapped_column(server_default=text("0"))
    kryptonite_per_hour: Mapped[float] = mapped_column(server_default=text("0"))
    spice_per_hour: Mapped[float] = mapped_column(server_default=text("0"))
    total_per_hour: Mapped[float] = mapped_column(server_default=text("0"))
    speq_per_hour: Mapped[float] = mapped_column(server_default=text("0"))
    planet_count: Mapped[int] = mapped_column(server_default=text("0"))
    active_builders: Mapped[int] = mapped_column(server_default=text("0"))


__all__ = ["ActionHistory", "HourlyProductionHistory"]
