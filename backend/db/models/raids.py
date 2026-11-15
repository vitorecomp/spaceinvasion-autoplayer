"""SQLAlchemy ORM models for raid history, bash blocks, and hourly loot rollups."""

from __future__ import annotations

from sqlalchemy import Index, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models.base import Base, identity_pk


class RaidHistory(Base):
    """Append-only ledger of dispatched raids and recorded outcomes."""

    __tablename__ = "raid_history"
    __table_args__ = (
        Index("idx_raid_history_coords_ts", "target_coords", "attacked_ts"),
        Index("idx_raid_history_ts", "attacked_ts"),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    target_coords: Mapped[str] = mapped_column()
    attacked_ts: Mapped[float] = mapped_column()
    is_exempt: Mapped[int] = mapped_column(server_default=text("0"))
    outcome: Mapped[str] = mapped_column(server_default=text("''"))
    loot_json: Mapped[str] = mapped_column(server_default=text("'{}'"))


class BashBlock(Base):
    """Append-only ledger of server bash-limit and protection cooldown blocks."""

    __tablename__ = "bash_blocks"
    __table_args__ = (
        Index("idx_bash_blocks_coords_ts", "target_coords", "blocked_ts"),
        Index("idx_bash_blocks_until_ts", "blocked_until_ts"),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    target_coords: Mapped[str] = mapped_column()
    blocked_ts: Mapped[float] = mapped_column()
    blocked_until_ts: Mapped[float] = mapped_column()
    cooldown_seconds: Mapped[float] = mapped_column(server_default=text("0.0"))
    is_bash: Mapped[int] = mapped_column(server_default=text("1"))
    reason: Mapped[str] = mapped_column(server_default=text("''"))


class LootRecycleHourly(Base):
    """Hourly aggregated stolen loot and recycled debris totals."""

    __tablename__ = "loot_recycle_hourly"

    hour_bucket: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    stolen_pig_iron: Mapped[float] = mapped_column(server_default=text("0"))
    stolen_metal: Mapped[float] = mapped_column(server_default=text("0"))
    stolen_kryptonite: Mapped[float] = mapped_column(server_default=text("0"))
    stolen_spice: Mapped[float] = mapped_column(server_default=text("0"))
    stolen_total: Mapped[float] = mapped_column(server_default=text("0"))
    recycled_pig_iron: Mapped[float] = mapped_column(server_default=text("0"))
    recycled_metal: Mapped[float] = mapped_column(server_default=text("0"))
    recycled_kryptonite: Mapped[float] = mapped_column(server_default=text("0"))
    recycled_spice: Mapped[float] = mapped_column(server_default=text("0"))
    recycled_total: Mapped[float] = mapped_column(server_default=text("0"))
    attack_count: Mapped[int] = mapped_column(server_default=text("0"))
    recycle_count: Mapped[int] = mapped_column(server_default=text("0"))
    updated_at: Mapped[float] = mapped_column(server_default=text("0"))


__all__ = ["BashBlock", "LootRecycleHourly", "RaidHistory"]
