"""SQLAlchemy ORM models for colony candidates and colonization attempts."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, desc, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models.base import Base, JsonbText, identity_pk


class ColonyCandidateHot(Base):
    """Materialized hot table of ranked uninhabited colony candidates."""

    __tablename__ = "colony_candidates_hot"
    __table_args__ = (
        CheckConstraint("is_probed IN (0, 1)", name="ck_colony_candidates_hot_is_probed"),
        CheckConstraint("is_discarded IN (0, 1)", name="ck_colony_candidates_hot_is_discarded"),
        CheckConstraint("is_active IN (0, 1)", name="ck_colony_candidates_hot_is_active"),
        CheckConstraint("galaxy >= 0 AND system >= 0 AND position >= 0", name="ck_colony_candidates_hot_coords_bounds"),
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
        Index(
            "idx_colony_candidates_hot_probed",
            desc("max_fields_est"),
            "coords",
            postgresql_where=text("is_active = 1 AND is_discarded = 0 AND is_probed = 1"),
        ),
        Index(
            "idx_colony_candidates_hot_prime",
            "galaxy",
            "system",
            "position",
            "coords",
            postgresql_where=text(
                "is_active = 1 AND is_discarded = 0 AND is_probed = 0 AND position >= 7 AND position <= 12"
            ),
        ),
        Index(
            "idx_colony_candidates_hot_unprobed_other",
            "position",
            "galaxy",
            "system",
            "coords",
            postgresql_where=text(
                "is_active = 1 AND is_discarded = 0 AND is_probed = 0 AND (position < 7 OR position > 12)"
            ),
        ),
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
    details_json: Mapped[str] = mapped_column(JsonbText(), server_default=text("'{}'::jsonb"))


__all__ = ["ColonyAttempt", "ColonyCandidateHot"]
