"""SQLAlchemy 2.0 ORM repository for the manual command queue and its append-only events."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import ColumnElement, case, func, insert, select, update
from sqlalchemy.orm import Session

from backend.db.dto import ManualQueueEventRecord, ManualQueueRecord
from backend.db.models import ManualQueueEvent, ManualQueueItem
from backend.db.repos._helpers import (
    affected_rows,
    clamp_int64,
    dumps_json,
    normalize_coords,
    round_ts,
    sanitize_text,
)

_PRIORITIES: frozenset[str] = frozenset({"P0", "P1", "P2", "P3"})
_UNSET_INT: object = object()


def _normalize_priority(raw: str | None, default: str = "P1") -> str:
    cleaned = sanitize_text(raw if raw is not None else default).strip().upper()
    return cleaned if cleaned in _PRIORITIES else default


def _priority_order_expr() -> ColumnElement[int]:
    return case(
        (ManualQueueItem.priority == "P0", 0),
        (ManualQueueItem.priority == "P1", 1),
        (ManualQueueItem.priority == "P2", 2),
        else_=3,
    )


def _norm_target_coords(raw: str) -> str:
    stripped = raw.strip()
    if not stripped:
        return ""
    norm = normalize_coords(stripped)
    return norm if norm else sanitize_text(stripped, 32)


def _to_item_dto(r: ManualQueueItem) -> ManualQueueRecord:
    return ManualQueueRecord(
        r.id,
        float(r.created_ts),
        float(r.updated_ts),
        r.status,
        _normalize_priority(r.priority, "P1"),
        int(r.sort_rank),
        r.kind.upper(),
        max(0, int(r.planet_id)),
        normalize_coords(r.planet_coords) if r.planet_coords else "",
        int(r.target_id) if r.target_id is not None else None,
        r.target_name,
        _norm_target_coords(r.target_coords),
        int(r.target_level) if r.target_level is not None else None,
        r.note,
        float(r.completed_ts) if r.completed_ts is not None else None,
        float(r.canceled_ts) if r.canceled_ts is not None else None,
        r.result_message,
    )


def _reorder_ids(ordered_ids: list[int], prio_by_id: dict[int, str], qid: int, move_s: str) -> None:
    if qid not in ordered_ids:
        return
    idx = ordered_ids.index(qid)
    cur_p = prio_by_id[qid]
    if move_s in ("top", "bottom"):
        band = [i for i, mid in enumerate(ordered_ids) if prio_by_id[mid] == cur_p]
        target_idx = (band[0] if band else 0) if move_s == "top" else (band[-1] if band else len(ordered_ids) - 1)
        ordered_ids.pop(idx)
        ordered_ids.insert(target_idx, qid)
    elif move_s == "up" and idx > 0:
        prio_by_id[qid] = prio_by_id[ordered_ids[idx - 1]]
        ordered_ids[idx - 1], ordered_ids[idx] = ordered_ids[idx], ordered_ids[idx - 1]
    elif move_s == "down" and idx < len(ordered_ids) - 1:
        prio_by_id[qid] = prio_by_id[ordered_ids[idx + 1]]
        ordered_ids[idx + 1], ordered_ids[idx] = ordered_ids[idx], ordered_ids[idx + 1]


class ManualQueueRepository:
    """SQLAlchemy ORM repository over `manual_queue` and `manual_queue_events`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _append_event(self, queue_id: int, event_type: str, details: Mapping[str, object], ts: float) -> None:
        ev = ManualQueueEvent(
            ts=ts, queue_id=queue_id, event_type=sanitize_text(event_type, 40), details_json=dumps_json(dict(details))
        )
        self._session.add(ev)

    def _band_rank(self, priority: str, position: str, *, exclude_id: int | None = None) -> int:
        stmt = select(func.min(ManualQueueItem.sort_rank), func.max(ManualQueueItem.sort_rank)).where(
            ManualQueueItem.status == "pending",
            ManualQueueItem.priority == priority,
        )
        if exclude_id is not None:
            stmt = stmt.where(ManualQueueItem.id != exclude_id)
        min_r, max_r = self._session.execute(stmt).one()
        if position == "bottom":
            return (int(max_r) + 10) if max_r is not None else 1000
        return (int(min_r) - 10) if min_r is not None else 1000

    def _find_pending_dup(
        self,
        *,
        kind: str,
        planet_id: int,
        target_id: int | None,
        target_coords: str,
        target_level: int | None,
    ) -> ManualQueueItem | None:
        return self._session.execute(
            select(ManualQueueItem)
            .where(
                ManualQueueItem.status == "pending",
                ManualQueueItem.kind == kind,
                ManualQueueItem.planet_id == planet_id,
                func.coalesce(ManualQueueItem.target_id, -1) == (-1 if target_id is None else target_id),
                ManualQueueItem.target_coords == target_coords,
                func.coalesce(ManualQueueItem.target_level, -1) == (-1 if target_level is None else target_level),
            )
            .order_by(ManualQueueItem.id.asc())
            .limit(1)
        ).scalar_one_or_none()

    def list_items(
        self,
        *,
        status: str | None = "pending",
        limit: int = 200,
    ) -> tuple[ManualQueueRecord, ...]:
        """List queue items using canonical SQL priority and `sort_rank` ordering."""
        lim = max(1, min(1000, int(limit)))
        norm_status = sanitize_text(status).strip().lower() if status else None
        stmt = select(ManualQueueItem)
        if norm_status == "pending":
            stmt = stmt.where(ManualQueueItem.status == "pending").order_by(
                _priority_order_expr().asc(),
                ManualQueueItem.sort_rank.asc(),
                ManualQueueItem.id.asc(),
            )
        elif norm_status and norm_status != "all":
            stmt = stmt.where(ManualQueueItem.status == norm_status).order_by(
                ManualQueueItem.updated_ts.desc(),
                ManualQueueItem.id.desc(),
            )
        else:
            stmt = stmt.order_by(
                case((ManualQueueItem.status == "pending", 0), else_=1).asc(),
                _priority_order_expr().asc(),
                ManualQueueItem.sort_rank.asc(),
                ManualQueueItem.updated_ts.desc(),
                ManualQueueItem.id.desc(),
            )
        rows = self._session.execute(stmt.limit(lim)).scalars().all()
        return tuple(_to_item_dto(row) for row in rows)

    def list_events(
        self,
        *,
        queue_id: int | None = None,
        limit: int = 200,
    ) -> tuple[ManualQueueEventRecord, ...]:
        """List append-only manual queue audit events ordered newest first."""
        lim = max(1, min(2000, int(limit)))
        stmt = select(ManualQueueEvent)
        if queue_id is not None:
            stmt = stmt.where(ManualQueueEvent.queue_id == int(queue_id))
        rows = (
            self._session.execute(stmt.order_by(ManualQueueEvent.ts.desc(), ManualQueueEvent.id.desc()).limit(lim))
            .scalars()
            .all()
        )
        return tuple(
            ManualQueueEventRecord(r.id, float(r.ts), int(r.queue_id), r.event_type, r.details_json) for r in rows
        )

    def enqueue(
        self,
        *,
        kind: str,
        priority: str = "P1",
        position: str = "top",
        planet_id: int = 0,
        planet_coords: str = "",
        target_id: int | None = None,
        target_name: str = "",
        target_coords: str = "",
        target_level: int | None = None,
        note: str = "",
        deduplicate_pending: bool = False,
        ts: float | None = None,
    ) -> ManualQueueRecord:
        """Enqueue a pending manual command (or reprioritize a pending duplicate) and log an event."""
        now_ts = round_ts(ts)
        norm_kind = sanitize_text(kind).strip().upper()[:40]
        norm_prio = _normalize_priority(priority, "P1")
        norm_pos = sanitize_text(position).strip().lower()
        pid_i = max(0, clamp_int64(int(planet_id)))
        norm_pc = normalize_coords(planet_coords) if planet_coords.strip() else ""
        tid_i = clamp_int64(int(target_id)) if target_id is not None else None
        tlvl_i = clamp_int64(int(target_level)) if target_level is not None else None
        norm_tc = _norm_target_coords(target_coords)
        tname_s = sanitize_text(target_name).strip()[:160]
        note_s = sanitize_text(note).strip()[:300]

        if deduplicate_pending:
            dup = self._find_pending_dup(
                kind=norm_kind, planet_id=pid_i, target_id=tid_i, target_coords=norm_tc, target_level=tlvl_i
            )
            if dup is not None:
                new_rank = self._band_rank(norm_prio, norm_pos, exclude_id=dup.id)
                dup.updated_ts = now_ts
                dup.priority = norm_prio
                dup.sort_rank = new_rank
                if note_s:
                    dup.note = note_s
                self._append_event(
                    dup.id,
                    "reprioritized",
                    {"priority": norm_prio, "sort_rank": new_rank, "position": norm_pos or "top"},
                    now_ts,
                )
                self._session.flush()
                return _to_item_dto(dup)

        sort_rank = self._band_rank(norm_prio, norm_pos)
        item = ManualQueueItem(
            created_ts=now_ts,
            updated_ts=now_ts,
            status="pending",
            priority=norm_prio,
            sort_rank=sort_rank,
            kind=norm_kind,
            planet_id=pid_i,
            planet_coords=norm_pc,
            target_id=tid_i,
            target_name=tname_s,
            target_coords=norm_tc,
            target_level=tlvl_i,
            note=note_s,
            result_message="",
        )
        self._session.add(item)
        self._session.flush()
        self._append_event(
            item.id,
            "enqueued",
            {
                "kind": norm_kind,
                "priority": norm_prio,
                "sort_rank": sort_rank,
                "planet_id": pid_i,
                "target_id": tid_i,
                "target_name": tname_s,
                "target_coords": norm_tc,
                "target_level": tlvl_i,
            },
            now_ts,
        )
        self._session.flush()
        return _to_item_dto(item)

    def update_item(
        self,
        queue_id: int,
        *,
        priority: str | None = None,
        move: str | None = None,
        sort_rank: int | None = None,
        target_level: int | None | object = _UNSET_INT,
        note: str | None = None,
        ts: float | None = None,
    ) -> ManualQueueRecord | None:
        """Update item fields and optionally reorder pending items in one batched SQL `UPDATE`."""
        qid = int(queue_id)
        row = self._session.get(ManualQueueItem, qid) if qid > 0 else None
        if row is None:
            return None

        now_ts = round_ts(ts)
        if priority is not None:
            row.priority = _normalize_priority(priority, row.priority)
        if sort_rank is not None:
            row.sort_rank = clamp_int64(int(sort_rank))
        if target_level is not _UNSET_INT:
            row.target_level = clamp_int64(int(target_level)) if isinstance(target_level, int) else None
        if note is not None:
            row.note = sanitize_text(note, 300)
        if priority is not None or sort_rank is not None or target_level is not _UNSET_INT or note is not None:
            row.updated_ts = now_ts
            self._session.flush()

        move_s = sanitize_text(move).strip().lower() if move else ""
        if move_s in ("top", "bottom", "up", "down") and row.status == "pending":
            pending = self._session.execute(
                select(ManualQueueItem.id, ManualQueueItem.priority)
                .where(ManualQueueItem.status == "pending")
                .order_by(_priority_order_expr().asc(), ManualQueueItem.sort_rank.asc(), ManualQueueItem.id.asc())
            ).all()
            ordered_ids = [int(r[0]) for r in pending]
            prio_by_id = {int(r[0]): _normalize_priority(str(r[1]), "P1") for r in pending}
            _reorder_ids(ordered_ids, prio_by_id, qid, move_s)
            if ordered_ids:
                rank_map = {mid: (pos_i + 1) * 10 for pos_i, mid in enumerate(ordered_ids)}
                self._session.execute(
                    update(ManualQueueItem)
                    .where(ManualQueueItem.id.in_(ordered_ids))
                    .values(
                        updated_ts=now_ts,
                        priority=case(prio_by_id, value=ManualQueueItem.id),
                        sort_rank=case(rank_map, value=ManualQueueItem.id),
                    )
                )
                self._session.expire(row)
                row = self._session.get(ManualQueueItem, qid)
                if row is None:
                    return None

        self._append_event(
            qid,
            "reordered" if move_s else "updated",
            {
                "priority": row.priority,
                "sort_rank": row.sort_rank,
                "move": move_s or None,
                "target_level": row.target_level,
            },
            now_ts,
        )
        self._session.flush()
        return _to_item_dto(row)

    def cancel_item(self, queue_id: int, *, reason: str = "Canceled by operator", ts: float | None = None) -> bool:
        """Cancel one pending item and append a `canceled` audit event."""
        qid = int(queue_id)
        if qid <= 0:
            return False
        now_ts = round_ts(ts)
        rsn_s = sanitize_text(reason, 240)
        res = self._session.execute(
            update(ManualQueueItem)
            .where(ManualQueueItem.id == qid, ManualQueueItem.status == "pending")
            .values(status="canceled", updated_ts=now_ts, canceled_ts=now_ts, result_message=rsn_s)
        )
        if affected_rows(res) <= 0:
            return False
        self._append_event(qid, "canceled", {"reason": rsn_s}, now_ts)
        self._session.flush()
        return True

    def clear_pending(self, *, reason: str = "Cleared by operator", ts: float | None = None) -> int:
        """Cancel all pending items and bulk-insert audit events in 2 SQL statements (O(1))."""
        now_ts = round_ts(ts)
        rsn_s = sanitize_text(reason, 240)
        canceled_ids = list(
            self._session.execute(
                update(ManualQueueItem)
                .where(ManualQueueItem.status == "pending")
                .values(status="canceled", updated_ts=now_ts, canceled_ts=now_ts, result_message=rsn_s)
                .returning(ManualQueueItem.id)
            )
            .scalars()
            .all()
        )
        if not canceled_ids:
            return 0
        det = dumps_json({"reason": rsn_s})
        rows = [{"ts": now_ts, "queue_id": int(q), "event_type": "canceled", "details_json": det} for q in canceled_ids]
        self._session.execute(insert(ManualQueueEvent), rows)
        return len(canceled_ids)

    def complete_item(self, queue_id: int, *, message: str = "", ts: float | None = None) -> bool:
        """Mark one pending item completed and append a `completed` event."""
        qid = int(queue_id)
        if qid <= 0:
            return False
        now_ts = round_ts(ts)
        msg_s = sanitize_text(message, 300)
        res = self._session.execute(
            update(ManualQueueItem)
            .where(ManualQueueItem.id == qid, ManualQueueItem.status == "pending")
            .values(status="completed", updated_ts=now_ts, completed_ts=now_ts, result_message=msg_s)
        )
        if affected_rows(res) <= 0:
            return False
        self._append_event(qid, "completed", {"message": msg_s}, now_ts)
        self._session.flush()
        return True


__all__ = ["ManualQueueRepository"]
