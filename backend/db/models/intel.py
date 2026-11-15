"""SQLAlchemy ORM models for espionage targets, reports, and research maxima."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, desc, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models.base import Base, JsonbText, identity_pk

_ACTIVE_SPY_TARGET_PREDICATE: str = "merged_into = '' AND is_own_planet = 0"


class SpyTarget(Base):
    """Current espionage dossier and attack-eligibility state per coordinate."""

    __tablename__ = "spy_targets"
    __table_args__ = (
        CheckConstraint("is_inactive IN (0, 1)", name="ck_spy_targets_is_inactive"),
        CheckConstraint("is_spacia IN (0, 1)", name="ck_spy_targets_is_spacia"),
        CheckConstraint("has_debris IN (0, 1)", name="ck_spy_targets_has_debris"),
        CheckConstraint("dug_in IN (0, 1)", name="ck_spy_targets_dug_in"),
        CheckConstraint("is_own_planet IN (0, 1)", name="ck_spy_targets_is_own_planet"),
        Index(
            "idx_spy_targets_colony_filter",
            "is_own_planet",
            "merged_into",
            "is_spacia",
            "is_inactive",
        ),
        Index(
            "idx_spy_targets_priority_spied",
            "is_inactive",
            "is_spacia",
            "has_debris",
            desc("last_scanned_ts"),
            desc("points").nulls_last(),
            "coords",
            postgresql_where=text(f"{_ACTIVE_SPY_TARGET_PREDICATE} AND last_spied_ts > 0.0"),
        ),
        Index(
            "idx_spy_targets_priority_unspied",
            "is_inactive",
            "is_spacia",
            "has_debris",
            desc("last_scanned_ts"),
            desc("points").nulls_last(),
            "coords",
            postgresql_where=text(f"{_ACTIVE_SPY_TARGET_PREDICATE} AND last_spied_ts <= 0.0"),
        ),
        Index(
            "idx_spy_targets_active_debris",
            desc("last_scanned_ts"),
            desc("points").nulls_last(),
            "coords",
            postgresql_where=text("merged_into = '' AND is_own_planet = 0 AND has_debris = 1"),
        ),
        Index(
            "idx_spy_targets_active_blocked",
            desc("blocked_until_ts"),
            "coords",
            postgresql_where=text("blocked_until_ts > 0.0"),
        ),
        Index(
            "idx_spy_targets_active_spied",
            desc("last_scanned_ts"),
            "coords",
            postgresql_where=text("merged_into = '' AND is_own_planet = 0 AND last_spied_ts > 0.0"),
        ),
        Index(
            "idx_spy_targets_active_non_own",
            "is_inactive",
            "is_spacia",
            "has_debris",
            "last_spied_ts",
            postgresql_where=text("merged_into = '' AND is_own_planet = 0"),
        ),
    )

    coords: Mapped[str] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(server_default=text("''"))
    planet_name: Mapped[str] = mapped_column(server_default=text("''"))
    status_color: Mapped[str] = mapped_column(server_default=text("'yellow'"))
    is_inactive: Mapped[int] = mapped_column(server_default=text("0"))
    is_spacia: Mapped[int] = mapped_column(server_default=text("0"))
    has_debris: Mapped[int] = mapped_column(server_default=text("0"))
    points: Mapped[int | None] = mapped_column()
    pig_iron: Mapped[int] = mapped_column(server_default=text("0"))
    metal: Mapped[int] = mapped_column(server_default=text("0"))
    kryptonite: Mapped[int] = mapped_column(server_default=text("0"))
    spice: Mapped[int] = mapped_column(server_default=text("0"))
    fleet_json: Mapped[str] = mapped_column(JsonbText(), server_default=text("'{}'::jsonb"))
    defense_json: Mapped[str] = mapped_column(JsonbText(), server_default=text("'{}'::jsonb"))
    dug_in: Mapped[int] = mapped_column(server_default=text("0"))
    last_scanned_ts: Mapped[float] = mapped_column()
    last_spied_ts: Mapped[float] = mapped_column(server_default=text("0.0"))
    blocked_until_ts: Mapped[float] = mapped_column(server_default=text("0.0"))
    block_reason: Mapped[str] = mapped_column(server_default=text("''"))
    bash_hits: Mapped[int] = mapped_column(server_default=text("0"))
    last_blocked_ts: Mapped[float] = mapped_column(server_default=text("0.0"))
    uninhabited_fields: Mapped[int | None] = mapped_column()
    spy_count: Mapped[int] = mapped_column(server_default=text("0"))
    is_own_planet: Mapped[int] = mapped_column(server_default=text("0"))
    merged_into: Mapped[str] = mapped_column(server_default=text("''"))
    colonize_min_fields: Mapped[int | None] = mapped_column()
    colonize_max_fields: Mapped[int | None] = mapped_column()
    temperature: Mapped[int | None] = mapped_column()


class SpyReportRecord(Base):
    """Append-only archive of parsed espionage reports."""

    __tablename__ = "spy_reports"
    __table_args__ = (
        CheckConstraint("is_spacia IN (0, 1)", name="ck_spy_reports_is_spacia"),
        CheckConstraint("dug_in IS NULL OR dug_in IN (0, 1)", name="ck_spy_reports_dug_in"),
        Index("idx_spy_reports_coords_ts", "target_coords", "ts"),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    ts: Mapped[float] = mapped_column()
    target_coords: Mapped[str] = mapped_column()
    owner: Mapped[str] = mapped_column(server_default=text("''"))
    planet_name: Mapped[str] = mapped_column(server_default=text("''"))
    is_spacia: Mapped[int] = mapped_column(server_default=text("0"))
    pig_iron: Mapped[int] = mapped_column(server_default=text("0"))
    metal: Mapped[int] = mapped_column(server_default=text("0"))
    kryptonite: Mapped[int] = mapped_column(server_default=text("0"))
    spice: Mapped[int] = mapped_column(server_default=text("0"))
    loot_spice_eq: Mapped[float] = mapped_column(server_default=text("0.0"))
    buildings_json: Mapped[str] = mapped_column(JsonbText(), server_default=text("'{}'::jsonb"))
    fleet_json: Mapped[str] = mapped_column(JsonbText(), server_default=text("'{}'::jsonb"))
    defense_json: Mapped[str] = mapped_column(JsonbText(), server_default=text("'{}'::jsonb"))
    researches_json: Mapped[str] = mapped_column(JsonbText(), server_default=text("'{}'::jsonb"))
    dug_in: Mapped[int | None] = mapped_column()
    uninhabited_fields: Mapped[int | None] = mapped_column()
    server_msg_id: Mapped[str] = mapped_column(server_default=text("''"))
    msg_date_text: Mapped[str] = mapped_column(server_default=text("''"))
    raw_text: Mapped[str] = mapped_column(server_default=text("''"))


class UniverseResearchMax(Base):
    """Monotonic highest observed universe research level per technology key."""

    __tablename__ = "universe_research_max"

    research_key: Mapped[str] = mapped_column(primary_key=True)
    research_id: Mapped[int] = mapped_column()
    slug: Mapped[str] = mapped_column()
    name: Mapped[str] = mapped_column()
    own_level: Mapped[int] = mapped_column(server_default=text("0"))
    spied_max_level: Mapped[int] = mapped_column(server_default=text("0"))
    server_record_level: Mapped[int | None] = mapped_column()
    universe_max_level: Mapped[int] = mapped_column(server_default=text("0"))
    catalog_max_level: Mapped[int | None] = mapped_column()
    holder: Mapped[str] = mapped_column(server_default=text("''"))
    holder_coords: Mapped[str] = mapped_column(server_default=text("''"))
    updated_ts: Mapped[float] = mapped_column()


class UniverseResearchMaxHistory(Base):
    """Append-only ledger of universe research maximum refreshes."""

    __tablename__ = "universe_research_max_history"
    __table_args__ = (Index("idx_universe_research_max_hist_ts", "ts"),)

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    ts: Mapped[float] = mapped_column()
    source: Mapped[str] = mapped_column(server_default=text("'refresh'"))
    records_json: Mapped[str] = mapped_column(JsonbText(default_empty="[]"))


__all__ = [
    "SpyReportRecord",
    "SpyTarget",
    "UniverseResearchMax",
    "UniverseResearchMaxHistory",
]
