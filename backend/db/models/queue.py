"""SQLAlchemy ORM models for the manual command queue and its event log."""

from __future__ import annotations

from sqlalchemy import Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.models.base import Base, identity_pk


class ManualQueueItem(Base):
    """Operator-queued manual action awaiting execution by the commander."""

    __tablename__ = "manual_queue"
    __table_args__ = (
        Index(
            "idx_manual_queue_status_prio_rank",
            "status",
            "priority",
            "sort_rank",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    created_ts: Mapped[float] = mapped_column()
    updated_ts: Mapped[float] = mapped_column()
    status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    priority: Mapped[str] = mapped_column(server_default=text("'P1'"))
    sort_rank: Mapped[int] = mapped_column(server_default=text("1000"))
    kind: Mapped[str] = mapped_column()
    planet_id: Mapped[int] = mapped_column(server_default=text("0"))
    planet_coords: Mapped[str] = mapped_column(server_default=text("''"))
    target_id: Mapped[int | None] = mapped_column()
    target_name: Mapped[str] = mapped_column(server_default=text("''"))
    target_coords: Mapped[str] = mapped_column(server_default=text("''"))
    target_level: Mapped[int | None] = mapped_column()
    note: Mapped[str] = mapped_column(server_default=text("''"))
    completed_ts: Mapped[float | None] = mapped_column()
    canceled_ts: Mapped[float | None] = mapped_column()
    result_message: Mapped[str] = mapped_column(server_default=text("''"))

    events: Mapped[list[ManualQueueEvent]] = relationship(
        "ManualQueueEvent",
        primaryjoin="ManualQueueItem.id == foreign(ManualQueueEvent.queue_id)",
        viewonly=True,
        lazy="raise_on_sql",
    )


class ManualQueueEvent(Base):
    """Append-only lifecycle events associated with a manual queue item."""

    __tablename__ = "manual_queue_events"
    __table_args__ = (Index("idx_manual_queue_events_qid_ts", "queue_id", "ts"),)

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    ts: Mapped[float] = mapped_column()
    queue_id: Mapped[int] = mapped_column()
    event_type: Mapped[str] = mapped_column()
    details_json: Mapped[str] = mapped_column(server_default=text("'{}'"))

    queue_item: Mapped[ManualQueueItem | None] = relationship(
        "ManualQueueItem",
        primaryjoin="foreign(ManualQueueEvent.queue_id) == ManualQueueItem.id",
        viewonly=True,
        lazy="raise_on_sql",
    )


__all__ = ["ManualQueueEvent", "ManualQueueItem"]
