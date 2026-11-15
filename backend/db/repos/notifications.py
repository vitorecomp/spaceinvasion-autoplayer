"""SQLAlchemy 2.0 ORM repository for persistent UI notifications (`ui_notifications`)."""

from __future__ import annotations

from collections.abc import Mapping
import re

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.db.dto import NotificationRecord
from backend.db.models import UiNotification
from backend.db.repos._helpers import affected_rows, dumps_json, round_ts, sanitize_text

_NOTIF_CODE_RE = re.compile(r"#?(?:(?:ntf|notif|notification)[-_:\s#]*)?0*(\d{1,18})", re.IGNORECASE | re.ASCII)
_VALID_SEVERITIES: frozenset[str] = frozenset({"error", "warning", "info"})
_VALID_ACK_STATUSES: frozenset[str] = frozenset({"read", "dismissed", "unread"})
_MAX_NOTIF_ID = 2**62


def format_notification_code(notification_id: int) -> str:
    """Format a notification ID as `NTF-XXXX`."""
    nid = max(0, int(notification_id))
    return f"NTF-{nid:04d}" if nid > 0 else "NTF-0000"


def parse_notification_id(raw: int | str | None) -> int | None:
    """Parse an integer ID or `NTF-XXXX` notification code into a positive ID."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw if 0 < raw <= _MAX_NOTIF_ID else None
    cleaned = raw.strip()
    if not cleaned:
        return None
    match = _NOTIF_CODE_RE.fullmatch(cleaned)
    if not match:
        return None
    val = int(match.group(1))
    return val if 0 < val <= _MAX_NOTIF_ID else None


def _to_dto(row: UiNotification) -> NotificationRecord:
    return NotificationRecord(
        id=row.id,
        notification_code=format_notification_code(row.id),
        created_ts=float(row.created_ts),
        updated_ts=float(row.updated_ts),
        severity=row.severity,
        category=row.category,
        title=row.title,
        message=row.message,
        help_prompt=row.help_prompt,
        action_kind=row.action_kind,
        planet_coords=row.planet_coords,
        target_name=row.target_name,
        dedupe_key=row.dedupe_key,
        occurrence_count=max(1, int(row.occurrence_count)),
        status=row.status,
        acknowledged_ts=float(row.acknowledged_ts) if row.acknowledged_ts is not None else None,
        details_json=row.details_json,
    )


class NotificationsRepository:
    """SQLAlchemy ORM repository over `ui_notifications` bound to an active `Session`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_or_bump(
        self,
        *,
        title: str,
        message: str,
        help_prompt: str = "",
        severity: str = "warning",
        category: str = "action_validation",
        action_kind: str = "",
        planet_coords: str = "",
        target_name: str = "",
        dedupe_key: str = "",
        details: Mapping[str, object] | None = None,
        ts: float | None = None,
    ) -> NotificationRecord:
        """Create an unread notification or increment `occurrence_count` on an identical unread key."""
        now_ts = round_ts(ts)
        sev_s = sanitize_text(severity or "warning").lower()
        if sev_s not in _VALID_SEVERITIES:
            sev_s = "warning"
        cat_s = sanitize_text(category or "action_validation", 48)
        title_s = sanitize_text(title or "Action Needs Review", 180)
        msg_s = sanitize_text(message, 1000)
        help_s = sanitize_text(help_prompt, 1000)
        kind_s = sanitize_text(action_kind).upper()[:32]
        pcoords_s = sanitize_text(planet_coords, 32)
        tname_s = sanitize_text(target_name, 120)
        dkey_s = sanitize_text(dedupe_key or f"{cat_s}:{kind_s}:{pcoords_s}:{tname_s}:{title_s}", 180)
        det_json = dumps_json(dict(details) if details is not None else {})

        existing = self._session.execute(
            select(UiNotification)
            .where(UiNotification.dedupe_key == dkey_s, UiNotification.status == "unread")
            .order_by(UiNotification.id.desc())
            .limit(1)
        ).scalar_one_or_none()

        if existing is not None:
            existing.updated_ts = now_ts
            existing.severity = sev_s
            existing.title = title_s
            existing.message = msg_s
            existing.help_prompt = help_s
            existing.occurrence_count = max(1, int(existing.occurrence_count)) + 1
            existing.details_json = det_json
            self._session.flush()
            return _to_dto(existing)

        created = UiNotification(
            created_ts=now_ts,
            updated_ts=now_ts,
            severity=sev_s,
            category=cat_s,
            title=title_s,
            message=msg_s,
            help_prompt=help_s,
            action_kind=kind_s,
            planet_coords=pcoords_s,
            target_name=tname_s,
            dedupe_key=dkey_s,
            occurrence_count=1,
            status="unread",
            acknowledged_ts=None,
            details_json=det_json,
        )
        self._session.add(created)
        self._session.flush()
        return _to_dto(created)

    def get_by_id(self, notification_id: int | str) -> NotificationRecord | None:
        """Fetch a single notification by integer ID or `NTF-XXXX` code."""
        nid = parse_notification_id(notification_id)
        if nid is None:
            return None
        row = self._session.get(UiNotification, nid)
        return _to_dto(row) if row is not None else None

    def list_notifications(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> tuple[NotificationRecord, ...]:
        """List notifications ordered newest first (`updated_ts DESC, id DESC`)."""
        lim = max(1, min(200, int(limit)))
        st_norm = sanitize_text(status).strip().lower()
        stmt = select(UiNotification)
        if st_norm in ("unread", "read", "dismissed"):
            stmt = stmt.where(UiNotification.status == st_norm)
        elif st_norm == "active":
            stmt = stmt.where(UiNotification.status != "dismissed")
        stmt = stmt.order_by(UiNotification.updated_ts.desc(), UiNotification.id.desc()).limit(lim)
        rows = self._session.execute(stmt).scalars().all()
        return tuple(_to_dto(row) for row in rows)

    def unread_count(self) -> int:
        """Count unread notifications in SQL."""
        cnt = self._session.execute(
            select(func.count()).select_from(UiNotification).where(UiNotification.status == "unread")
        ).scalar_one()
        return int(cnt)

    def acknowledge(
        self,
        notification_id: int | str,
        *,
        status: str = "read",
        ts: float | None = None,
    ) -> NotificationRecord | None:
        """Update one notification status and `acknowledged_ts` in a single `UPDATE ... RETURNING`."""
        nid = parse_notification_id(notification_id)
        if nid is None:
            return None
        st_norm = sanitize_text(status or "read").strip().lower()
        if st_norm not in _VALID_ACK_STATUSES:
            st_norm = "read"
        ack_ts = None if st_norm == "unread" else round_ts(ts)
        stmt = (
            update(UiNotification)
            .where(UiNotification.id == nid)
            .values(status=st_norm, acknowledged_ts=ack_ts)
            .returning(UiNotification)
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        return _to_dto(row) if row is not None else None

    def acknowledge_all(
        self,
        *,
        status: str = "read",
        ts: float | None = None,
    ) -> int:
        """Mark all currently unread notifications as `read` or `dismissed` in one SQL statement."""
        st_norm = sanitize_text(status or "read").strip().lower()
        if st_norm not in ("read", "dismissed"):
            st_norm = "read"
        now_ts = round_ts(ts)
        res = self._session.execute(
            update(UiNotification)
            .where(UiNotification.status == "unread")
            .values(status=st_norm, acknowledged_ts=now_ts)
        )
        return affected_rows(res)


__all__ = [
    "NotificationsRepository",
    "format_notification_code",
    "parse_notification_id",
]
