"""Field-conflict prompt and audit-event SQLAlchemy operations shared by `PlannerRunsRepository`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from spaceinvasion.db.dto import FieldConflictEventRecord, FieldConflictPromptRecord
from spaceinvasion.db.models import FieldConflictEvent, FieldConflictPrompt
from spaceinvasion.db.repos._helpers import (
    as_str_dict,
    dumps_json,
    loads_json_dict,
    normalize_building_key,
    normalize_coords,
    round_ts,
    sanitize_text,
)

_DETAIL_EXTRA_KEYS: tuple[str, ...] = (
    "planet_name",
    "blocked_building_name",
    "candidate_building_name",
    "fields_used",
    "fields_max",
    "candidates",
    "reason",
)


@dataclass(frozen=True)
class _ParsedConflict:
    conflict_key: str
    planet_id: int
    planet_coords: str
    blocked_bkey: str
    blocked_tlvl: int
    cand_bkey: str
    cand_lvl: int
    status: str
    decision: str
    created_ts: float
    resolved_ts: float | None
    detail_json: str


def _to_int(raw: object, default: int = 0) -> int:
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(float(raw.strip()))
        except ValueError:
            return default
    return default


def _to_float(raw: object, default: float = 0.0) -> float:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip())
        except ValueError:
            return default
    return default


def _parse_conflict_input(conflict: Mapping[str, object], ts: float | None) -> _ParsedConflict:
    planet_id = max(0, _to_int(conflict.get("planet_id"), 0))
    raw_coords = sanitize_text(conflict.get("planet_coords") or conflict.get("coords")).strip()
    planet_coords = normalize_coords(raw_coords) if raw_coords else ""
    blocked_bkey = normalize_building_key(
        str(conflict.get("blocked_building_key") or conflict.get("building_key") or "")
    )
    blocked_tlvl = max(0, _to_int(conflict.get("blocked_target_level", conflict.get("target_level", 0)), 0))
    cand_bkey = normalize_building_key(str(conflict.get("candidate_building_key") or ""))
    cand_lvl = max(0, _to_int(conflict.get("candidate_level", 0), 0))
    raw_cands = conflict.get("candidates")
    if (not cand_bkey or cand_lvl <= 0) and isinstance(raw_cands, Sequence) and not isinstance(raw_cands, str):
        for c_item in raw_cands:
            if isinstance(c_item, Mapping):
                ck = normalize_building_key(
                    str(c_item.get("building_key") or c_item.get("key") or c_item.get("building_id") or "")
                )
                cl = max(0, _to_int(c_item.get("level", c_item.get("current_level", 0)), 0))
                if ck:
                    cand_bkey = cand_bkey or ck
                    cand_lvl = cl if cand_lvl <= 0 else cand_lvl
                    break

    conflict_key = sanitize_text(conflict.get("conflict_key"), 128).strip()
    if not conflict_key:
        loc = str(planet_id) if planet_id > 0 else (planet_coords or "0")
        conflict_key = f"{loc}:{blocked_bkey or 'b0'}:{blocked_tlvl}"
    status_in = sanitize_text(conflict.get("status") or "pending", 32).strip().lower() or "pending"
    decision_in = sanitize_text(conflict.get("decision") or "", 64).strip().lower()
    created_raw = conflict.get("created_ts")
    now_ts = round_ts(ts if ts is not None else (_to_float(created_raw) if created_raw is not None else None))
    resolved_raw = conflict.get("resolved_ts")
    resolved_ts = round_ts(_to_float(resolved_raw)) if resolved_raw is not None else None

    detail_dict = as_str_dict(conflict.get("detail"))
    for extra_k in _DETAIL_EXTRA_KEYS:
        if extra_k in conflict and extra_k not in detail_dict:
            detail_dict[extra_k] = conflict[extra_k]
    return _ParsedConflict(
        conflict_key=conflict_key,
        planet_id=planet_id,
        planet_coords=planet_coords,
        blocked_bkey=blocked_bkey,
        blocked_tlvl=blocked_tlvl,
        cand_bkey=cand_bkey,
        cand_lvl=cand_lvl,
        status=status_in,
        decision=decision_in,
        created_ts=now_ts,
        resolved_ts=resolved_ts,
        detail_json=dumps_json(detail_dict),
    )


def _to_dto(row: FieldConflictPrompt) -> FieldConflictPromptRecord:
    return FieldConflictPromptRecord(
        conflict_key=row.conflict_key,
        planet_id=int(row.planet_id),
        planet_coords=row.planet_coords,
        blocked_building_key=row.blocked_building_key,
        blocked_target_level=int(row.blocked_target_level),
        candidate_building_key=row.candidate_building_key,
        candidate_level=int(row.candidate_level),
        status=row.status,
        decision=row.decision,
        created_ts=float(row.created_ts),
        resolved_ts=float(row.resolved_ts) if row.resolved_ts is not None else None,
        detail_json=row.detail_json,
    )


def upsert_conflict_prompt(
    session: Session,
    conflict: Mapping[str, object],
    *,
    ts: float | None = None,
) -> FieldConflictPromptRecord:
    """Upsert a field-limit conflict prompt and append a `created`/`updated` event."""
    parsed = _parse_conflict_input(conflict, ts)
    existing = session.get(FieldConflictPrompt, parsed.conflict_key)
    prev_state = (
        (existing.status, existing.detail_json, existing.candidate_building_key, existing.candidate_level)
        if existing is not None
        else None
    )

    stmt = pg_insert(FieldConflictPrompt).values(
        conflict_key=parsed.conflict_key,
        planet_id=parsed.planet_id,
        planet_coords=parsed.planet_coords,
        blocked_building_key=parsed.blocked_bkey,
        blocked_target_level=parsed.blocked_tlvl,
        candidate_building_key=parsed.cand_bkey,
        candidate_level=parsed.cand_lvl,
        status=parsed.status,
        decision=parsed.decision,
        created_ts=parsed.created_ts,
        resolved_ts=parsed.resolved_ts,
        detail_json=parsed.detail_json,
    )
    upsert = stmt.on_conflict_do_update(
        index_elements=[FieldConflictPrompt.conflict_key],
        set_={
            "planet_id": stmt.excluded.planet_id,
            "planet_coords": case(
                (stmt.excluded.planet_coords != "", stmt.excluded.planet_coords),
                else_=FieldConflictPrompt.planet_coords,
            ),
            "blocked_building_key": stmt.excluded.blocked_building_key,
            "blocked_target_level": stmt.excluded.blocked_target_level,
            "candidate_building_key": case(
                (stmt.excluded.candidate_building_key != "", stmt.excluded.candidate_building_key),
                else_=FieldConflictPrompt.candidate_building_key,
            ),
            "candidate_level": case(
                (stmt.excluded.candidate_level > 0, stmt.excluded.candidate_level),
                else_=FieldConflictPrompt.candidate_level,
            ),
            "status": case(
                (FieldConflictPrompt.status == "resolved", FieldConflictPrompt.status), else_=stmt.excluded.status
            ),
            "decision": case(
                (FieldConflictPrompt.status == "resolved", FieldConflictPrompt.decision), else_=stmt.excluded.decision
            ),
            "resolved_ts": func.coalesce(FieldConflictPrompt.resolved_ts, stmt.excluded.resolved_ts),
            "detail_json": stmt.excluded.detail_json,
        },
    )
    session.execute(upsert)

    if prev_state is None:
        ev_type: str | None = "created"
    elif prev_state[0] != "resolved" and (
        prev_state[1] != parsed.detail_json or prev_state[2] != parsed.cand_bkey or prev_state[3] != parsed.cand_lvl
    ):
        ev_type = "updated"
    else:
        ev_type = None

    if ev_type is not None:
        session.add(
            FieldConflictEvent(
                created_ts=parsed.created_ts,
                conflict_key=parsed.conflict_key,
                event_type=ev_type,
                decision=parsed.decision,
                detail_json=parsed.detail_json,
            )
        )
    session.flush()
    if existing is not None:
        session.expire(existing)
    row = session.get(FieldConflictPrompt, parsed.conflict_key)
    assert row is not None
    return _to_dto(row)


def resolve_conflict_prompt(
    session: Session,
    conflict_key: str,
    decision: str,
    *,
    detail: Mapping[str, object] | None = None,
    ts: float | None = None,
) -> FieldConflictPromptRecord | None:
    """Resolve a field conflict prompt and append a `resolved` event."""
    ck = sanitize_text(conflict_key, 128).strip()
    row = session.get(FieldConflictPrompt, ck) if ck else None
    if row is None:
        return None
    dec = sanitize_text(decision, 64).strip().lower()
    now_ts = round_ts(ts)
    merged = loads_json_dict(row.detail_json)
    extra = as_str_dict(detail)
    if extra:
        merged.update(extra)
    row.status = "resolved"
    row.decision = dec
    row.resolved_ts = now_ts
    row.detail_json = dumps_json(merged)
    session.add(
        FieldConflictEvent(
            created_ts=now_ts,
            conflict_key=ck,
            event_type="resolved",
            decision=dec,
            detail_json=dumps_json(extra or merged),
        )
    )
    session.flush()
    return _to_dto(row)


def list_conflict_prompts(session: Session, *, status: str | None = "pending") -> tuple[FieldConflictPromptRecord, ...]:
    """List field conflict prompts filtered by status."""
    clean_status = sanitize_text(status).strip().lower() if status is not None else ""
    stmt = select(FieldConflictPrompt)
    if clean_status and clean_status not in ("all", "*"):
        stmt = stmt.where(FieldConflictPrompt.status == clean_status)
    rows = (
        session.execute(stmt.order_by(FieldConflictPrompt.created_ts.desc(), FieldConflictPrompt.conflict_key.asc()))
        .scalars()
        .all()
    )
    return tuple(_to_dto(row) for row in rows)


def list_conflict_events(
    session: Session,
    *,
    conflict_key: str | None = None,
    limit: int = 100,
) -> tuple[FieldConflictEventRecord, ...]:
    """List append-only field conflict audit events ordered newest first."""
    lim = max(1, min(1000, int(limit)))
    stmt = select(FieldConflictEvent)
    if conflict_key:
        stmt = stmt.where(FieldConflictEvent.conflict_key == conflict_key)
    rows = (
        session.execute(stmt.order_by(FieldConflictEvent.created_ts.desc(), FieldConflictEvent.id.desc()).limit(lim))
        .scalars()
        .all()
    )
    return tuple(
        FieldConflictEventRecord(
            id=r.id,
            created_ts=float(r.created_ts),
            conflict_key=r.conflict_key,
            event_type=r.event_type,
            decision=r.decision,
            detail_json=r.detail_json,
        )
        for r in rows
    )


__all__ = [
    "list_conflict_events",
    "list_conflict_prompts",
    "resolve_conflict_prompt",
    "upsert_conflict_prompt",
]
