"""SQLAlchemy ORM models for empire snapshots, hot state, rank, and tutorial."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, desc, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models.base import Base, JsonbText, identity_pk


class StateSnapshot(Base):
    """Append-only full game-state and plan snapshot archive."""

    __tablename__ = "state_snapshots"
    __table_args__ = (
        Index("idx_state_snapshots_ts", "ts"),
        Index("idx_state_snapshots_ts_brin", "ts", postgresql_using="brin"),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    ts: Mapped[float] = mapped_column()
    source: Mapped[str] = mapped_column(server_default=text("'advisor'"))
    universe_id: Mapped[int] = mapped_column(server_default=text("0"))
    user_id: Mapped[int] = mapped_column(server_default=text("0"))
    nickname: Mapped[str] = mapped_column(server_default=text("''"))
    active_planet_id: Mapped[int] = mapped_column(server_default=text("0"))
    planet_count: Mapped[int] = mapped_column(server_default=text("0"))
    planned_action_count: Mapped[int] = mapped_column(server_default=text("0"))
    state_json: Mapped[str] = mapped_column(JsonbText(), server_default=text("'{}'::jsonb"))
    plan_json: Mapped[str] = mapped_column(JsonbText(default_empty="[]"), server_default=text("'[]'::jsonb"))


class LatestStateHot(Base):
    """Singleton hot row holding the most recent full state snapshot."""

    __tablename__ = "latest_state_hot"
    __table_args__ = (CheckConstraint("id = 1", name="latest_state_hot_id_check"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    source_snapshot_id: Mapped[int] = mapped_column(server_default=text("0"))
    ts: Mapped[float] = mapped_column(server_default=text("0.0"))
    source: Mapped[str] = mapped_column(server_default=text("'advisor'"))
    universe_id: Mapped[int] = mapped_column(server_default=text("0"))
    user_id: Mapped[int] = mapped_column(server_default=text("0"))
    nickname: Mapped[str] = mapped_column(server_default=text("''"))
    active_planet_id: Mapped[int] = mapped_column(server_default=text("0"))
    planet_count: Mapped[int] = mapped_column(server_default=text("0"))
    planned_action_count: Mapped[int] = mapped_column(server_default=text("0"))
    state_json: Mapped[str] = mapped_column(JsonbText(), server_default=text("'{}'::jsonb"))
    plan_json: Mapped[str] = mapped_column(JsonbText(default_empty="[]"), server_default=text("'[]'::jsonb"))
    updated_ts: Mapped[float] = mapped_column(server_default=text("0.0"))


class PlanetSnapshot(Base):
    """Append-only per-planet telemetry snapshot archive."""

    __tablename__ = "planet_snapshots"
    __table_args__ = (
        Index("idx_planet_snapshots_pid_ts", "planet_id", "ts"),
        Index("idx_planet_snapshots_pid_ts_id", "planet_id", desc("ts"), desc("id")),
        Index("idx_planet_snapshots_ts_id", desc("ts"), desc("id")),
        Index("idx_planet_snapshots_ts_brin", "ts", postgresql_using="brin"),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    ts: Mapped[float] = mapped_column()
    universe_id: Mapped[int] = mapped_column(server_default=text("0"))
    planet_id: Mapped[int] = mapped_column()
    coords: Mapped[str] = mapped_column()
    name: Mapped[str] = mapped_column()
    planet_type: Mapped[str] = mapped_column()
    temperature: Mapped[int] = mapped_column()
    fields_used: Mapped[int] = mapped_column()
    fields_max: Mapped[int] = mapped_column()
    pig_iron: Mapped[int] = mapped_column()
    metal: Mapped[int] = mapped_column()
    kryptonite: Mapped[int] = mapped_column()
    spice: Mapped[int] = mapped_column()
    energy_produced: Mapped[int] = mapped_column()
    energy_used: Mapped[int] = mapped_column()
    buildings_json: Mapped[str] = mapped_column(JsonbText())
    ships_json: Mapped[str] = mapped_column(JsonbText())
    defense_json: Mapped[str] = mapped_column(JsonbText())
    economy_rates_json: Mapped[str] = mapped_column(JsonbText())
    researches_json: Mapped[str] = mapped_column(JsonbText())


class EmpirePlanetHot(Base):
    """Materialized hot row per owned empire planet."""

    __tablename__ = "empire_planets_hot"
    __table_args__ = (
        CheckConstraint("is_active IN (0, 1)", name="ck_empire_planets_hot_is_active"),
        Index("idx_empire_planets_hot_coords", "coords", "is_active"),
    )

    planet_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    universe_id: Mapped[int] = mapped_column(server_default=text("0"))
    coords: Mapped[str] = mapped_column(server_default=text("''"))
    name: Mapped[str] = mapped_column(server_default=text("''"))
    planet_type: Mapped[str] = mapped_column(server_default=text("'normal'"))
    temperature: Mapped[int] = mapped_column(server_default=text("0"))
    fields_used: Mapped[int] = mapped_column(server_default=text("0"))
    fields_max: Mapped[int] = mapped_column(server_default=text("0"))
    pig_iron: Mapped[int] = mapped_column(server_default=text("0"))
    metal: Mapped[int] = mapped_column(server_default=text("0"))
    kryptonite: Mapped[int] = mapped_column(server_default=text("0"))
    spice: Mapped[int] = mapped_column(server_default=text("0"))
    energy_produced: Mapped[int] = mapped_column(server_default=text("0"))
    energy_used: Mapped[int] = mapped_column(server_default=text("0"))
    is_active: Mapped[int] = mapped_column(server_default=text("1"))
    updated_ts: Mapped[float] = mapped_column(server_default=text("0.0"))


class RankSnapshot(Base):
    """Append-only account rank and score evolution samples."""

    __tablename__ = "rank_snapshots"
    __table_args__ = (
        Index("idx_rank_snapshots_ts", "ts"),
        Index("idx_rank_snapshots_ts_brin", "ts", postgresql_using="brin"),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    ts: Mapped[float] = mapped_column()
    universe_id: Mapped[int] = mapped_column(server_default=text("0"))
    nickname: Mapped[str] = mapped_column(server_default=text("''"))
    server_points: Mapped[int | None] = mapped_column()
    server_rank: Mapped[int | None] = mapped_column()
    estimated_points: Mapped[float] = mapped_column()
    pts_per_hr: Mapped[float] = mapped_column()
    spice_value: Mapped[float] = mapped_column()
    planets: Mapped[int] = mapped_column()


class EconomyRatesCache(Base):
    """Cached per-planet economy throttle percentages."""

    __tablename__ = "economy_rates_cache"

    planet_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    coords: Mapped[str] = mapped_column()
    rates_json: Mapped[str] = mapped_column(JsonbText())
    updated_ts: Mapped[float] = mapped_column()


class TutorialProgress(Base):
    """Observed tutorial step milestones and completion timestamps."""

    __tablename__ = "tutorial_progress"
    __table_args__ = (CheckConstraint("can_complete IN (0, 1)", name="ck_tutorial_progress_can_complete"),)

    step: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    total_steps: Mapped[int] = mapped_column()
    tasks_json: Mapped[str] = mapped_column(JsonbText(default_empty="[]"))
    reward_text: Mapped[str] = mapped_column()
    can_complete: Mapped[int] = mapped_column()
    first_seen_ts: Mapped[float] = mapped_column()
    last_seen_ts: Mapped[float] = mapped_column()
    completed_ts: Mapped[float | None] = mapped_column()


__all__ = [
    "EconomyRatesCache",
    "EmpirePlanetHot",
    "LatestStateHot",
    "PlanetSnapshot",
    "RankSnapshot",
    "StateSnapshot",
    "TutorialProgress",
]
