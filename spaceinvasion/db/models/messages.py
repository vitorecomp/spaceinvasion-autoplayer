"""SQLAlchemy ORM models for inbox messages, battle reports, and recycle reports."""

from __future__ import annotations

from sqlalchemy import Index, text
from sqlalchemy.orm import Mapped, mapped_column

from spaceinvasion.db.models.base import Base, identity_pk


class GameMessage(Base):
    """Append-only archive of raw and parsed BitMeUp inbox messages."""

    __tablename__ = "game_messages"
    __table_args__ = (
        Index("idx_game_messages_cat", "category", "ingested_ts"),
        Index("idx_game_messages_coords", "coords", "ingested_ts"),
        Index("idx_game_messages_ts", "ingested_ts"),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    ingested_ts: Mapped[float] = mapped_column()
    msg_uid: Mapped[str] = mapped_column(unique=True)
    server_msg_id: Mapped[str] = mapped_column(server_default=text("''"))
    folder_type: Mapped[str] = mapped_column(server_default=text("''"))
    category: Mapped[str] = mapped_column(server_default=text("'other'"))
    msg_date_text: Mapped[str] = mapped_column(server_default=text("''"))
    sender: Mapped[str] = mapped_column(server_default=text("''"))
    subject: Mapped[str] = mapped_column(server_default=text("''"))
    coords: Mapped[str] = mapped_column(server_default=text("''"))
    body_text: Mapped[str] = mapped_column(server_default=text("''"))
    raw_html: Mapped[str] = mapped_column(server_default=text("''"))
    parsed_json: Mapped[str] = mapped_column(server_default=text("'{}'"))
    deleted_in_game: Mapped[int] = mapped_column(server_default=text("0"))
    deleted_ts: Mapped[float | None] = mapped_column()


class BattleReport(Base):
    """Structured battle report ledger extracted from combat messages."""

    __tablename__ = "battle_reports"
    __table_args__ = (
        Index("idx_battle_reports_coords_ts", "target_coords", "ts"),
        Index("idx_battle_reports_ts", "ts"),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    ts: Mapped[float] = mapped_column()
    msg_uid: Mapped[str] = mapped_column(unique=True)
    server_msg_id: Mapped[str] = mapped_column(server_default=text("''"))
    battle_id: Mapped[str] = mapped_column(server_default=text("''"))
    msg_date_text: Mapped[str] = mapped_column(server_default=text("''"))
    target_coords: Mapped[str] = mapped_column(server_default=text("''"))
    attacker: Mapped[str] = mapped_column(server_default=text("''"))
    defender: Mapped[str] = mapped_column(server_default=text("''"))
    outcome: Mapped[str] = mapped_column(server_default=text("'win'"))
    rounds: Mapped[int] = mapped_column(server_default=text("0"))
    pig_iron: Mapped[int] = mapped_column(server_default=text("0"))
    metal: Mapped[int] = mapped_column(server_default=text("0"))
    kryptonite: Mapped[int] = mapped_column(server_default=text("0"))
    spice: Mapped[int] = mapped_column(server_default=text("0"))
    loot_spice_eq: Mapped[float] = mapped_column(server_default=text("0.0"))
    attacker_lost_spice: Mapped[int] = mapped_column(server_default=text("0"))
    defender_lost_spice: Mapped[int] = mapped_column(server_default=text("0"))
    debris_pig_iron: Mapped[int] = mapped_column(server_default=text("0"))
    debris_metal: Mapped[int] = mapped_column(server_default=text("0"))
    debris_spice_eq: Mapped[float] = mapped_column(server_default=text("0.0"))
    raw_text: Mapped[str] = mapped_column(server_default=text("''"))


class RecycleReport(Base):
    """Structured debris-harvesting ledger extracted from recycler reports."""

    __tablename__ = "recycle_reports"
    __table_args__ = (
        Index("idx_recycle_reports_coords_ts", "coords", "ts"),
        Index("idx_recycle_reports_ts", "ts"),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    ts: Mapped[float] = mapped_column()
    msg_uid: Mapped[str] = mapped_column(unique=True)
    server_msg_id: Mapped[str] = mapped_column(server_default=text("''"))
    msg_date_text: Mapped[str] = mapped_column(server_default=text("''"))
    coords: Mapped[str] = mapped_column(server_default=text("''"))
    recycler_count: Mapped[int] = mapped_column(server_default=text("0"))
    cargo_capacity: Mapped[int] = mapped_column(server_default=text("0"))
    debris_avail_pig: Mapped[int] = mapped_column(server_default=text("0"))
    debris_avail_metal: Mapped[int] = mapped_column(server_default=text("0"))
    harvested_pig_iron: Mapped[int] = mapped_column(server_default=text("0"))
    harvested_metal: Mapped[int] = mapped_column(server_default=text("0"))
    harvested_kryptonite: Mapped[int] = mapped_column(server_default=text("0"))
    harvested_spice: Mapped[int] = mapped_column(server_default=text("0"))
    harvested_spice_eq: Mapped[float] = mapped_column(server_default=text("0.0"))
    raw_text: Mapped[str] = mapped_column(server_default=text("''"))


__all__ = ["BattleReport", "GameMessage", "RecycleReport"]
