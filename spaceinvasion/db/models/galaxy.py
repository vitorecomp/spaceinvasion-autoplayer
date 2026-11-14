"""SQLAlchemy ORM models for galaxy scans, slot catalogs, and sector hot state."""

from __future__ import annotations

from sqlalchemy import Index, text
from sqlalchemy.orm import Mapped, mapped_column

from spaceinvasion.db.models.base import Base, identity_pk


class GalaxyScan(Base):
    """Latest scan metadata for each `(galaxy, system)` sector."""

    __tablename__ = "galaxy_scans"
    __table_args__ = (Index("idx_galaxy_scans_ts", "last_scanned_ts"),)

    galaxy: Mapped[int] = mapped_column(primary_key=True)
    system: Mapped[int] = mapped_column(primary_key=True)
    last_scanned_ts: Mapped[float] = mapped_column()
    occupied_count: Mapped[int] = mapped_column(server_default=text("0"))
    target_count: Mapped[int] = mapped_column(server_default=text("0"))
    free_count: Mapped[int] = mapped_column(server_default=text("0"))


class GalaxyScanHistory(Base):
    """Append-only history of galaxy system scans."""

    __tablename__ = "galaxy_scan_history"
    __table_args__ = (
        Index(
            "idx_galaxy_scan_history_sys_ts",
            "galaxy",
            "system",
            "ts",
        ),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    ts: Mapped[float] = mapped_column()
    galaxy: Mapped[int] = mapped_column()
    system: Mapped[int] = mapped_column()
    occupied_count: Mapped[int] = mapped_column(server_default=text("0"))
    target_count: Mapped[int] = mapped_column(server_default=text("0"))
    free_count: Mapped[int] = mapped_column(server_default=text("0"))
    debris_count: Mapped[int] = mapped_column(server_default=text("0"))
    asteroid_count: Mapped[int] = mapped_column(server_default=text("0"))


class GalaxySlot(Base):
    """Current known state of each scanned coordinate slot."""

    __tablename__ = "galaxy_slots"
    __table_args__ = (Index("idx_galaxy_slots_sys", "galaxy", "system", "position"),)

    coords: Mapped[str] = mapped_column(primary_key=True)
    galaxy: Mapped[int] = mapped_column()
    system: Mapped[int] = mapped_column()
    position: Mapped[int] = mapped_column()
    planet_name: Mapped[str] = mapped_column(server_default=text("''"))
    owner: Mapped[str] = mapped_column(server_default=text("''"))
    status_color: Mapped[str] = mapped_column(server_default=text("'free'"))
    is_inactive: Mapped[int] = mapped_column(server_default=text("0"))
    is_spacia: Mapped[int] = mapped_column(server_default=text("0"))
    has_debris: Mapped[int] = mapped_column(server_default=text("0"))
    last_scanned_ts: Mapped[float] = mapped_column()
    colonize_min_fields: Mapped[int | None] = mapped_column()
    colonize_max_fields: Mapped[int | None] = mapped_column()
    temperature: Mapped[int | None] = mapped_column()


class GalaxySlotHistory(Base):
    """Append-only observations of individual galaxy slots."""

    __tablename__ = "galaxy_slot_history"
    __table_args__ = (Index("idx_galaxy_slot_history_coords_ts", "coords", "ts"),)

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    ts: Mapped[float] = mapped_column()
    coords: Mapped[str] = mapped_column()
    galaxy: Mapped[int] = mapped_column()
    system: Mapped[int] = mapped_column()
    position: Mapped[int] = mapped_column()
    planet_name: Mapped[str] = mapped_column(server_default=text("''"))
    owner: Mapped[str] = mapped_column(server_default=text("''"))
    status_color: Mapped[str] = mapped_column(server_default=text("'free'"))
    is_inactive: Mapped[int] = mapped_column(server_default=text("0"))
    is_spacia: Mapped[int] = mapped_column(server_default=text("0"))
    has_debris: Mapped[int] = mapped_column(server_default=text("0"))
    has_asteroid: Mapped[int] = mapped_column(server_default=text("0"))
    colonize_min_fields: Mapped[int | None] = mapped_column()
    colonize_max_fields: Mapped[int | None] = mapped_column()
    temperature: Mapped[int | None] = mapped_column()


class GalaxySectorHot(Base):
    """Materialized per-sector summary table for fast dashboard reads."""

    __tablename__ = "galaxy_sector_hot"
    __table_args__ = (Index("idx_galaxy_sector_hot_ts", "last_scanned_ts"),)

    galaxy: Mapped[int] = mapped_column(primary_key=True)
    system: Mapped[int] = mapped_column(primary_key=True)
    last_scanned_ts: Mapped[float] = mapped_column(server_default=text("0.0"))
    scan_count: Mapped[int] = mapped_column(server_default=text("0"))
    occupied_count: Mapped[int] = mapped_column(server_default=text("0"))
    target_count: Mapped[int] = mapped_column(server_default=text("0"))
    inactive_count: Mapped[int] = mapped_column(server_default=text("0"))
    spacia_count: Mapped[int] = mapped_column(server_default=text("0"))
    debris_count: Mapped[int] = mapped_column(server_default=text("0"))
    free_count: Mapped[int] = mapped_column(server_default=text("0"))
    prime_colony_free_count: Mapped[int] = mapped_column(server_default=text("0"))
    hist_debris_max: Mapped[int] = mapped_column(server_default=text("0"))
    owners_json: Mapped[str] = mapped_column(server_default=text("'[]'"))
    owner_planet_counts_json: Mapped[str] = mapped_column(server_default=text("'{}'"))
    coord_owners_json: Mapped[str] = mapped_column(server_default=text("'{}'"))
    spacia_coords_json: Mapped[str] = mapped_column(server_default=text("'[]'"))
    updated_ts: Mapped[float] = mapped_column(server_default=text("0.0"))


__all__ = [
    "GalaxyScan",
    "GalaxyScanHistory",
    "GalaxySectorHot",
    "GalaxySlot",
    "GalaxySlotHistory",
]
