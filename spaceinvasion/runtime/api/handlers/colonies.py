"""Non-browser colony candidate listing and discard `@command` handlers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import logging
import re

from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState, count_allocated_normal_planets
from spaceinvasion.runtime.api.handlers._bridge_proto import BridgeApiFacade
from spaceinvasion.runtime.api.registry import CommandRegistry
from spaceinvasion.runtime.api.validation import ApiRequestError, FieldBounds
from spaceinvasion.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    safe_api_int,
    sanitize_json_dict,
)

_LOGGER = logging.getLogger(__name__)
_COORDS_RE: re.Pattern[str] = re.compile(r"(\d{1,2}:\d{1,3}:\d{1,2})", re.ASCII)


@dataclass(frozen=True)
class ColonyCandidatesParams:
    """Parameters for listing graded uninhabited colony candidates."""

    limit: int = field(default=200, metadata={"bounds": FieldBounds(min_val=1, max_val=1000)})
    include_discarded: bool = False
    min_fields: int | None = field(
        default=None,
        metadata={"bounds": FieldBounds(min_val=50, max_val=600)},
    )


@dataclass(frozen=True)
class DiscardColonyCandidateParams:
    """Parameters for marking a colony candidate coordinate as discarded."""

    target_coords: object = None
    coords: object = None


def _own_planet_coords(state: GameState | None) -> set[str]:
    if state is None:
        return set()
    return {normalize_coords(p.coords) for p in state.planets if p.coords}


def _build_colony_attempts_payload(bridge: BridgeApiFacade) -> dict[str, object]:
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
    db = bridge.ctx.db
    if db is not None:
        try:
            raw_rows = db.get_colony_attempts(limit=100)
            attempts = [dict(r) for r in (raw_rows or ()) if isinstance(r, Mapping)]
        except BRIDGE_RUNTIME_ERRORS:
            _LOGGER.warning("Failed to read colony_attempts rows", exc_info=True)
        try:
            raw_sum = db.get_colony_attempts_summary()
            if isinstance(raw_sum, Mapping):
                summary = dict(raw_sum)
        except BRIDGE_RUNTIME_ERRORS:
            _LOGGER.warning("Failed to read colony_attempts_summary", exc_info=True)
    by_coords = summary.get("by_coords")
    if not isinstance(by_coords, Mapping):
        by_coords = {}
        summary["by_coords"] = by_coords
    return {
        "colony_attempts": attempts,
        "colony_attempts_summary": summary,
        "attempts_by_coords": dict(by_coords),
    }


def _handle_colony_candidates(bridge: BridgeApiFacade, params: ColonyCandidatesParams) -> dict[str, object]:
    """Return graded uninhabited colony candidates, fleet availability, and colonization attempt stats."""
    with bridge.ctx.intel_read_lock:
        state_snap = bridge.ctx.last_state
        cfg_snap = bridge.ctx.strategy_config
        db_snap = bridge.ctx.db
        ap = state_snap.active_planet() if state_snap is not None else None
        origin_coords = ap.coords if ap is not None else ""
        min_fields = (
            max(50, int(params.min_fields))
            if params.min_fields is not None
            else max(50, safe_api_int(cfg_snap.min_colony_fields, 300))
        )
        own_coords_snap = _own_planet_coords(state_snap)
        candidates: list[dict[str, object]] = (
            [
                dict(c)
                for c in db_snap.get_colony_candidates(
                    origin_coords=origin_coords,
                    min_fields_target=min_fields,
                    exclude_coords=own_coords_snap,
                    include_discarded=params.include_discarded,
                    limit=params.limit,
                )
            ]
            if db_snap is not None
            else []
        )
        discarded_count = len(db_snap.get_discarded_colony_coords()) if db_snap is not None else 0
        grade_counts: dict[str, int] = {"S": 0, "A+": 0, "A": 0, "B": 0, "C": 0, "D": 0, "?": 0}
        for cand in candidates:
            grade = str(cand.get("grade") or "?")
            grade_counts[grade] = grade_counts.get(grade, 0) + 1
        probed_count = sum(1 for c in candidates if bool(c.get("probed")))
        unprobed_count = len(candidates) - probed_count
        qualified_count = sum(1 for c in candidates if bool(c.get("meets_target")))
        colony_ships = 0
        spy_probes = 0
        planet_count = 0
        allocated_normal_planets = 0
        max_planets = max(1, safe_api_int(cfg_snap.max_planets, 11))
        if state_snap is not None:
            planet_count = len(state_snap.planets)
            allocated_normal_planets = count_allocated_normal_planets(state_snap)
            for planet in state_snap.planets:
                colony_ships += max(0, safe_api_int(planet.ships.get("s8", 0)))
                spy_probes += max(0, safe_api_int(planet.ships.get("s10", 0)))
        needed_colonies = max(0, max_planets - allocated_normal_planets)
        attempts_payload = _build_colony_attempts_payload(bridge)

    return sanitize_json_dict(
        {
            "ok": True,
            "command": "colony_candidates",
            "origin_coords": origin_coords,
            "min_colony_fields": min_fields,
            "allow_colonize": bool(cfg_snap.allow_colonize),
            "allow_galaxy_scan": bool(cfg_snap.allow_galaxy_scan),
            "colony_ships": colony_ships,
            "spy_probes": spy_probes,
            "planet_count": planet_count,
            "allocated_normal_planets": allocated_normal_planets,
            "max_planets": max_planets,
            "needed_colonies": needed_colonies,
            "all_planets_allocated": allocated_normal_planets >= max_planets,
            "grade_counts": grade_counts,
            "probed_count": probed_count,
            "unprobed_count": unprobed_count,
            "qualified_count": qualified_count,
            "discarded_count": discarded_count,
            "candidates": candidates,
            **attempts_payload,
        }
    )


def _handle_discard_colony_candidate(
    bridge: BridgeApiFacade,
    params: DiscardColonyCandidateParams,
) -> dict[str, object]:
    """Mark a colony candidate coordinate as discarded so it is excluded from colonization."""
    raw_tc = params.target_coords if params.target_coords is not None else params.coords
    if not isinstance(raw_tc, str) or not raw_tc.strip():
        raise ApiRequestError("'discard_colony_candidate' requires 'target_coords' formatted as GG:SSS:PP.")
    match = _COORDS_RE.search(raw_tc.strip())
    if not match:
        raise ApiRequestError("'discard_colony_candidate' requires 'target_coords' formatted as GG:SSS:PP.")
    norm_tc = normalize_coords(match.group(1))

    with bridge.ctx.state_lock:
        db = bridge.ctx.db
        if db is None:
            raise ApiRequestError("Database is not configured on this API bridge.")
        changed = bool(db.discard_colony_candidate(norm_tc))
        bridge.ctx.invalidate_dashboard_cache()
        discarded_count = len(db.get_discarded_colony_coords())

    bridge.publish_snapshot()
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "discard_colony_candidate",
            "target_coords": norm_tc,
            "discarded": True,
            "changed": changed,
            "discarded_count": discarded_count,
            "message": f"Discarded colony candidate {norm_tc}.",
        }
    )


def register_colony_handlers(registry: CommandRegistry[BridgeApiFacade]) -> None:
    """Register all 2 non-browser colony candidate commands."""
    registry.command(
        "colony_candidates",
        needs_browser=False,
        mutates=False,
        summary="List graded uninhabited colony candidates and colonization attempt stats.",
        params=ColonyCandidatesParams,
    )(_handle_colony_candidates)
    registry.command(
        "discard_colony_candidate",
        needs_browser=False,
        mutates=True,
        summary="Mark a colony candidate coordinate as discarded.",
        params=DiscardColonyCandidateParams,
    )(_handle_discard_colony_candidate)
