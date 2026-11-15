"""Matching and progressive completion helper for pending manual command queue items."""

from __future__ import annotations

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from backend.db.models import ManualQueueEvent, ManualQueueItem
from backend.db.repos._helpers import clamp_int64, dumps_json, normalize_coords, round_ts, sanitize_text

_TARGET_ID_KINDS: frozenset[str] = frozenset(
    {"BUILD", "DEMOLISH_BUILDING", "RESEARCH", "BUILD_SHIP", "THROTTLE_BUILDING"}
)
_TARGET_COORD_KINDS: frozenset[str] = frozenset({"SPY", "ATTACK", "RECYCLE", "COLONIZE"})


def _matches_pending_row(
    row: ManualQueueItem,
    *,
    norm_kind: str,
    pid_i: int,
    tid_i: int | None,
    norm_tc: str,
) -> bool:
    if row.kind.upper() != norm_kind:
        return False
    r_pid = int(row.planet_id)
    if norm_kind != "RESEARCH" and r_pid > 0 and pid_i > 0 and r_pid != pid_i:
        return False
    r_tid = int(row.target_id) if row.target_id is not None else None
    r_tc = row.target_coords.strip()
    norm_r_tc = normalize_coords(r_tc) if r_tc else r_tc
    if norm_kind in _TARGET_ID_KINDS:
        return tid_i is None or r_tid == tid_i
    if norm_kind in _TARGET_COORD_KINDS:
        return not norm_tc or norm_r_tc == norm_tc
    if norm_kind == "SCAN_GALAXY":
        if norm_tc and norm_r_tc and not norm_r_tc.startswith(norm_tc[:6]) and not norm_tc.startswith(norm_r_tc[:6]):
            return False
    return True


def _apply_progressive_update(
    session: Session,
    matched: ManualQueueItem,
    *,
    norm_kind: str,
    executed_level: int | None,
    executed_qty: int | None,
    msg_s: str,
    now_ts: float,
) -> bool:
    """Apply a partial-progress update and return True when the item stays pending."""
    goal_lvl = int(matched.target_level) if matched.target_level is not None else None
    if goal_lvl is None:
        return False
    qid = int(matched.id)
    if norm_kind in ("BUILD", "RESEARCH") and executed_level is not None and int(executed_level) < goal_lvl:
        matched.updated_ts = now_ts
        matched.result_message = f"Progressed L{int(executed_level)}/{goal_lvl}: {msg_s}"[:300]
        details: dict[str, object] = {"executed_level": int(executed_level), "target_level": goal_lvl}
    elif norm_kind == "DEMOLISH_BUILDING" and executed_level is not None and int(executed_level) > goal_lvl:
        matched.updated_ts = now_ts
        matched.result_message = f"Demolished to L{int(executed_level)} (goal L{goal_lvl}): {msg_s}"[:300]
        details = {"executed_level": int(executed_level), "target_level": goal_lvl}
    elif norm_kind == "BUILD_SHIP" and executed_qty is not None and int(executed_qty) > 0:
        rem = goal_lvl - int(executed_qty)
        if rem <= 0:
            return False
        matched.updated_ts = now_ts
        matched.target_level = clamp_int64(rem)
        matched.result_message = f"Ordered {int(executed_qty)}x ({rem}x remaining): {msg_s}"[:300]
        details = {"ordered": int(executed_qty), "remaining": rem}
    else:
        return False
    session.add(ManualQueueEvent(ts=now_ts, queue_id=qid, event_type="progressed", details_json=dumps_json(details)))
    session.flush()
    return True


def complete_matching_queue_item(
    session: Session,
    *,
    kind: str,
    planet_id: int | None = None,
    target_id: int | None = None,
    target_coords: str = "",
    executed_level: int | None = None,
    executed_qty: int | None = None,
    message: str = "",
    ts: float | None = None,
) -> int | None:
    """Progress or complete the highest-priority pending `manual_queue` item matching an executed action."""
    norm_kind = sanitize_text(kind).strip().upper()
    if not norm_kind:
        return None
    pid_i = max(0, clamp_int64(int(planet_id))) if planet_id is not None else 0
    tid_i = clamp_int64(int(target_id)) if target_id is not None else None
    raw_tc = target_coords.strip()
    norm_tc = normalize_coords(raw_tc) if raw_tc else ""
    if not norm_tc and raw_tc:
        norm_tc = sanitize_text(raw_tc, 32)
    now_ts = round_ts(ts)
    msg_s = sanitize_text(message, 300)

    prio_ord = case(
        (ManualQueueItem.priority == "P0", 0),
        (ManualQueueItem.priority == "P1", 1),
        (ManualQueueItem.priority == "P2", 2),
        else_=3,
    )
    pending = (
        session.execute(
            select(ManualQueueItem)
            .where(ManualQueueItem.status == "pending")
            .order_by(prio_ord.asc(), ManualQueueItem.sort_rank.asc(), ManualQueueItem.id.asc())
        )
        .scalars()
        .all()
    )
    matched = next(
        (r for r in pending if _matches_pending_row(r, norm_kind=norm_kind, pid_i=pid_i, tid_i=tid_i, norm_tc=norm_tc)),
        None,
    )
    if matched is None:
        return None

    qid = int(matched.id)
    if _apply_progressive_update(
        session,
        matched,
        norm_kind=norm_kind,
        executed_level=executed_level,
        executed_qty=executed_qty,
        msg_s=msg_s,
        now_ts=now_ts,
    ):
        return qid

    matched.status = "completed"
    matched.updated_ts = now_ts
    matched.completed_ts = now_ts
    matched.result_message = msg_s
    session.add(
        ManualQueueEvent(ts=now_ts, queue_id=qid, event_type="completed", details_json=dumps_json({"message": msg_s}))
    )
    session.flush()
    return qid


__all__ = ["complete_matching_queue_item"]
