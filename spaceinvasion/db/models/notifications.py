"""SQLAlchemy ORM model for persistent operator console notifications."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, desc, text
from sqlalchemy.orm import Mapped, mapped_column

from spaceinvasion.db.models.base import Base, JsonbText, identity_pk


class UiNotification(Base):
    """Persistent alert and validation notification for the operator console."""

    __tablename__ = "ui_notifications"
    __table_args__ = (
        CheckConstraint("status IN ('unread', 'read', 'dismissed')", name="ck_ui_notifications_status"),
        CheckConstraint("severity IN ('info', 'warning', 'error', 'critical')", name="ck_ui_notifications_severity"),
        Index("idx_ui_notifications_dedupe", "dedupe_key", "status"),
        Index("idx_ui_notifications_status_ts", "status", "updated_ts"),
        Index(
            "idx_ui_notifications_unread",
            desc("updated_ts"),
            desc("id"),
            postgresql_where=text("status = 'unread'"),
        ),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    created_ts: Mapped[float] = mapped_column()
    updated_ts: Mapped[float] = mapped_column()
    severity: Mapped[str] = mapped_column(server_default=text("'warning'"))
    category: Mapped[str] = mapped_column(server_default=text("'action_validation'"))
    title: Mapped[str] = mapped_column()
    message: Mapped[str] = mapped_column()
    help_prompt: Mapped[str] = mapped_column(server_default=text("''"))
    action_kind: Mapped[str] = mapped_column(server_default=text("''"))
    planet_coords: Mapped[str] = mapped_column(server_default=text("''"))
    target_name: Mapped[str] = mapped_column(server_default=text("''"))
    dedupe_key: Mapped[str] = mapped_column(server_default=text("''"))
    occurrence_count: Mapped[int] = mapped_column(server_default=text("1"))
    status: Mapped[str] = mapped_column(server_default=text("'unread'"))
    acknowledged_ts: Mapped[float | None] = mapped_column()
    details_json: Mapped[str] = mapped_column(JsonbText(), server_default=text("'{}'::jsonb"))


__all__ = ["UiNotification"]
