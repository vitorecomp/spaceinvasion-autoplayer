"""Colony trial ledger (`colony_attempts`) hydration, recording, and summary helpers."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
import time

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState, homeworld_planet_id, normalize_planet_type
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import BRIDGE_RUNTIME_ERRORS, bounded_api_str, safe_api_int
from spaceinvasion.session.executor import ExecutionResult

_LOGGER = logging.getLogger(__name__)
COLONY_FAILED_DEDUPE_S: float = 600.0
_COORDS_RE = re.compile(r"(\d{1,2}:\d{1,3}:\d{1,2})", re.ASCII)


def colony_min_fields(ctx: BridgeContext) -> int:
    """Return clamped minimum colony field count configured in strategy config."""
    return max(50, safe_api_int(ctx.strategy_config.min_colony_fields, 300))


def safe_record_colony_attempt(
    ctx: BridgeContext,
    *,
    target_coords: str,
    event_type: str,
    origin_coords: str = "",
    planet_id: int | None = None,
    fields_rolled: int | None = None,
    min_fields_target: int = 300,
    expected_min_fields: int | None = None,
    expected_max_fields: int | None = None,
    temperature: int | None = None,
    grade: str = "",
    score: int | None = None,
    status_message: str = "",
    details: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    """Append a row to the colony trial ledger, logging and swallowing runtime errors."""
    db = ctx.db
    if db is None:
        return None
    try:
        out = db.record_colony_attempt(
            target_coords=target_coords,
            event_type=event_type,
            origin_coords=origin_coords,
            planet_id=planet_id,
            fields_rolled=fields_rolled,
            min_fields_target=min_fields_target,
            expected_min_fields=expected_min_fields,
            expected_max_fields=expected_max_fields,
            temperature=temperature,
            grade=grade,
            score=score,
            status_message=status_message,
            details=details,
        )
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.warning("Failed to record colony attempt for %s", target_coords, exc_info=True)
        return None
    return dict(out) if isinstance(out, Mapping) else None


def build_colony_attempts_payload(ctx: BridgeContext) -> dict[str, object]:
    """Return `colony_attempts`, `colony_attempts_summary`, and `attempts_by_coords`."""
    attempts: list[dict[str, object]] = []
    summary: dict[str, object] = {
        "total_events": 0,
        "unique_coords_tried": 0,
        "dispatched_count": 0,
        "deleted_undersized_count": 0,
        "kept_count": 0,
        "failed_count": 0,
        "by_coords": {},
    }
    db = ctx.db
    if db is not None:
        try:
            raw_rows = db.get_colony_attempts(limit=100)
            attempts = [dict(r) for r in (raw_rows or ()) if isinstance(r, Mapping)]
        except BRIDGE_RUNTIME_ERRORS:
            _LOGGER.warning("Failed to read colony attempts", exc_info=True)
        try:
            raw_sum = db.get_colony_attempts_summary()
            if isinstance(raw_sum, Mapping):
                summary = dict(raw_sum)
        except BRIDGE_RUNTIME_ERRORS:
            _LOGGER.warning("Failed to read colony attempts summary", exc_info=True)
    by_coords = summary.get("by_coords")
    if not isinstance(by_coords, Mapping):
        by_coords = {}
        summary["by_coords"] = by_coords
    return {
        "colony_attempts": attempts,
        "colony_attempts_summary": summary,
        "attempts_by_coords": dict(by_coords),
    }


def hydrate_colony_attempt_seen_for_ctx(ctx: BridgeContext) -> None:
    """Populate `ctx.colony_attempt_seen` from persisted `kept`/`settled` colony attempts."""
    db = ctx.db
    if db is None:
        return
    try:
        rows = db.get_colony_attempts(limit=500) or ()
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.warning("Failed to hydrate colony attempt seen cache", exc_info=True)
        return
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("event_type") or "").lower() not in ("kept", "settled"):
            continue
        pid_h = safe_api_int(row.get("planet_id"), 0)
        nc_h = normalize_coords(str(row.get("target_coords") or ""))
        if pid_h > 0 and nc_h:
            ctx.colony_attempt_seen.add((pid_h, nc_h))


def _lookup_colony_candidate(ctx: BridgeContext, norm_target: str, origin_coords: str) -> dict[str, object] | None:
    db = ctx.db
    if db is None or not norm_target:
        return None
    try:
        rows = db.get_colony_candidates(
            origin_coords=origin_coords,
            min_fields_target=colony_min_fields(ctx),
            include_discarded=True,
            limit=1000,
        )
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.debug("Failed to look up colony candidate %s", norm_target, exc_info=True)
        return None
    for row in rows or ():
        if isinstance(row, Mapping) and normalize_coords(str(row.get("coords") or "")) == norm_target:
            return dict(row)
    return None


def record_colony_dispatch_outcome_for_ctx(
    ctx: BridgeContext,
    state: GameState,
    res: ExecutionResult,
    cmd: str,
) -> None:
    """Append `dispatched`/`failed` (`COLONIZE`) or `deleted_undersized` (`ABANDON_COLONY`) rows."""
    act = res.action
    if act.kind not in ("COLONIZE", "ABANDON_COLONY") or ctx.db is None:
        return
    msg = bounded_api_str(res.message, default="")
    min_f = colony_min_fields(ctx)
    origin_pid = safe_api_int(act.planet_id, 0)
    if act.kind == "ABANDON_COLONY":
        if not res.executed:
            return
        planet = next((p for p in state.planets if p.planet_id == origin_pid), None)
        coords_ab = normalize_coords(str((planet.coords if planet is not None else "") or act.planet_coords or ""))
        if not coords_ab:
            return
        min_ab = safe_api_int(act.target_level, 0)
        safe_record_colony_attempt(
            ctx,
            target_coords=coords_ab,
            event_type="deleted_undersized",
            planet_id=origin_pid if origin_pid > 0 else None,
            fields_rolled=(safe_api_int(planet.fields_max, 0) if planet is not None else None),
            min_fields_target=min_ab if min_ab >= 50 else min_f,
            temperature=(safe_api_int(planet.temperature, 0) if planet is not None else None),
            status_message=msg,
            details={"source": cmd},
        )
        return
    tname = game_data.safe_key(act.target_name) if act.target_name is not None else ""
    m_tc = _COORDS_RE.search(tname)
    norm_target = normalize_coords(m_tc.group(1)) if m_tc else ""
    if not norm_target:
        return
    origin_coords = normalize_coords(str(act.planet_coords or ""))
    if not res.executed:
        if not act.executable_now or msg.lower().startswith("guarded"):
            return
        now_wall = time.time()
        prev_fail = ctx.colony_attempt_failed_recent.get(norm_target)
        if prev_fail is not None and prev_fail[0] == msg and (now_wall - prev_fail[1]) < COLONY_FAILED_DEDUPE_S:
            return
        ctx.colony_attempt_failed_recent[norm_target] = (msg, now_wall)
        safe_record_colony_attempt(
            ctx,
            target_coords=norm_target,
            event_type="failed",
            origin_coords=origin_coords,
            min_fields_target=min_f,
            status_message=msg or "COLONIZE dispatch failed",
            details={"source": cmd, "origin_planet_id": origin_pid},
        )
        return
    ctx.colony_attempt_failed_recent.pop(norm_target, None)
    cand = _lookup_colony_candidate(ctx, norm_target, origin_coords) or {}
    exp_max_raw = (
        cand.get("colonize_max_fields")
        if cand.get("colonize_max_fields") is not None
        else cand.get("uninhabited_fields")
    )
    safe_record_colony_attempt(
        ctx,
        target_coords=norm_target,
        event_type="dispatched",
        origin_coords=origin_coords,
        min_fields_target=min_f,
        expected_min_fields=(
            safe_api_int(cand.get("colonize_min_fields"), 0) if cand.get("colonize_min_fields") is not None else None
        ),
        expected_max_fields=safe_api_int(exp_max_raw, 0) if exp_max_raw is not None else None,
        temperature=safe_api_int(cand.get("temperature"), 0) if cand.get("temperature") is not None else None,
        grade=str(cand.get("grade") or ""),
        score=safe_api_int(cand.get("score"), 0) if cand.get("score") is not None else None,
        status_message=msg,
        details={"source": cmd, "origin_planet_id": origin_pid},
    )


def record_new_colonies_for_ctx(ctx: BridgeContext, prev: GameState | None, new_state: GameState) -> None:
    """Append `kept`/`settled` rows when a newly colonized secondary normal planet appears."""
    if prev is None or ctx.db is None:
        return
    try:
        prev_ids = {p.planet_id for p in prev.planets}
        hw_id = homeworld_planet_id(new_state)
        min_f = colony_min_fields(ctx)
        for planet in new_state.planets:
            pid_n = safe_api_int(planet.planet_id, 0)
            if pid_n <= 0 or pid_n == hw_id or normalize_planet_type(planet) != "normal":
                continue
            if pid_n in prev_ids and pid_n not in ctx.colony_attempt_pending_pids:
                continue
            nc_n = normalize_coords(str(planet.coords or ""))
            if not nc_n:
                continue
            key = (pid_n, nc_n)
            if key in ctx.colony_attempt_seen:
                ctx.colony_attempt_pending_pids.discard(pid_n)
                continue
            f_max = safe_api_int(planet.fields_max, 0)
            if f_max <= 0:
                ctx.colony_attempt_pending_pids.add(pid_n)
                continue
            ctx.colony_attempt_pending_pids.discard(pid_n)
            ctx.colony_attempt_seen.add(key)
            safe_record_colony_attempt(
                ctx,
                target_coords=nc_n,
                event_type="kept" if f_max >= min_f else "settled",
                planet_id=pid_n,
                fields_rolled=f_max,
                min_fields_target=min_f,
                temperature=safe_api_int(planet.temperature, 0),
                details={"source": "state", "planet_name": bounded_api_str(planet.name, default="")},
            )
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.warning("Failed to record newly settled colonies", exc_info=True)
