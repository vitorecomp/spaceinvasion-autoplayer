"""SQLAlchemy ORM models for colony candidates and colonization attempts."""

from __future__ import annotations

from sqlalchemy import Index, desc, text
from sqlalchemy.orm import Mapped, mapped_column

from spaceinvasion.db.models.base import Base, identity_pk


class ColonyCandidateHot(Base):
    """Materialized hot table of ranked uninhabited colony candidates."""

    __tablename__ = "colony_candidates_hot"
    __table_args__ = (
        Index(
            "idx_colony_candidates_hot_rank",
            "is_active",
            "is_discarded",
            "is_probed",
            desc("max_fields_est"),
            "position",
            "coords",
        ),
        Index("idx_colony_candidates_hot_sys", "galaxy", "system", "is_active"),
    )

    coords: Mapped[str] = mapped_column(primary_key=True)
    galaxy: Mapped[int] = mapped_column(server_default=text("0"))
    system: Mapped[int] = mapped_column(server_default=text("0"))
    position: Mapped[int] = mapped_column(server_default=text("0"))
    uninhabited_fields: Mapped[int | None] = mapped_column()
    colonize_min_fields: Mapped[int | None] = mapped_column()
    colonize_max_fields: Mapped[int | None] = mapped_column()
    max_fields_est: Mapped[int] = mapped_column(server_default=text("0"))
    temperature: Mapped[int | None] = mapped_column()
    is_probed: Mapped[int] = mapped_column(server_default=text("0"))
    last_spied_ts: Mapped[float] = mapped_column(server_default=text("0.0"))
    last_scanned_ts: Mapped[float] = mapped_column(server_default=text("0.0"))
    spy_count: Mapped[int] = mapped_column(server_default=text("0"))
    blocked_until_ts: Mapped[float] = mapped_column(server_default=text("0.0"))
    block_reason: Mapped[str] = mapped_column(server_default=text("''"))
    is_discarded: Mapped[int] = mapped_column(server_default=text("0"))
    is_active: Mapped[int] = mapped_column(server_default=text("1"))
    updated_ts: Mapped[float] = mapped_column(server_default=text("0.0"))


class ColonyAttempt(Base):
    """Append-only audit trail of colony dispatches, rolls, and abandonments."""

    __tablename__ = "colony_attempts"
    __table_args__ = (
        Index("idx_colony_attempts_coords_ts", "target_coords", "ts"),
        Index("idx_colony_attempts_ts", "ts"),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    ts: Mapped[float] = mapped_column()
    event_type: Mapped[str] = mapped_column()
    target_coords: Mapped[str] = mapped_column()
    origin_coords: Mapped[str] = mapped_column(server_default=text("''"))
    planet_id: Mapped[int | None] = mapped_column()
    fields_rolled: Mapped[int | None] = mapped_column()
    min_fields_target: Mapped[int] = mapped_column(server_default=text("300"))
    expected_min_fields: Mapped[int | None] = mapped_column()
    expected_max_fields: Mapped[int | None] = mapped_column()
    temperature: Mapped[int | None] = mapped_column()
    grade: Mapped[str] = mapped_column(server_default=text("''"))
    score: Mapped[int | None] = mapped_column()
    status_message: Mapped[str] = mapped_column(server_default=text("''"))
    details_json: Mapped[str] = mapped_column(server_default=text("'{}'"))


__all__ = ["ColonyAttempt", "ColonyCandidateHot"]
