"""Building queue accounting, effective projected levels/energy, and active research increments."""

from __future__ import annotations

from collections.abc import Mapping
import re

from spaceinvasion.core import catalog
from spaceinvasion.core.state import GameState, PlanetState
from spaceinvasion.planner.heuristics._common import (
    _norm_planet_type,
    _normalize_manual_coords,
    _safe_strategy_int,
    _safe_strategy_str,
)


def _is_building_queue_job_active(job: object, state: GameState) -> bool:
    if getattr(job, "category", "") != "building":
        return False
    raw_fin = getattr(job, "finish_ts", None)
    raw_st = getattr(state, "timestamp", None)
    fin_ts = catalog.safe_finite_float(raw_fin, 0.0) if raw_fin is not None else 0.0
    st_ts = catalog.safe_finite_float(raw_st, 0.0) if raw_st is not None else 0.0
    return not (fin_ts > 0.0 and st_ts > 0.0 and fin_ts <= st_ts)


def _building_job_matches_planet(
    job: object,
    state: GameState,
    planet: PlanetState | None,
    *,
    is_active_planet: bool,
) -> bool:
    if not _is_building_queue_job_active(job, state):
        return False
    if planet is None:
        return bool(is_active_planet)
    origin = _safe_strategy_str(getattr(job, "origin_coords", "")).strip()
    if origin:
        p_coords = _safe_strategy_str(getattr(planet, "coords", "")).strip()
        if not p_coords:
            return False
        return _normalize_manual_coords(origin) == _normalize_manual_coords(p_coords)
    return bool(is_active_planet)


def _count_planet_building_queues(
    state: GameState,
    planet: PlanetState | None,
    *,
    is_active_planet: bool,
) -> int:
    return sum(
        1
        for job in (getattr(state, "active_queues", ()) or ())
        if _building_job_matches_planet(job, state, planet, is_active_planet=is_active_planet)
    )


def _queued_building_counts(
    state: GameState,
    planet: PlanetState | None = None,
    *,
    is_active_planet: bool | None = None,
) -> dict[int, int]:
    target_p = planet if planet is not None else state.active_planet()
    if is_active_planet is None:
        active_p = state.active_planet()
        is_act = target_p is None or active_p is None or target_p.planet_id == active_p.planet_id
    else:
        is_act = bool(is_active_planet)
    counts: dict[int, int] = {}
    for job in getattr(state, "active_queues", ()) or ():
        if job.building_id is None:
            continue
        if not _building_job_matches_planet(job, state, target_p, is_active_planet=is_act):
            continue
        bid = catalog.safe_optional_int(job.building_id, None)
        if bid is not None:
            counts[bid] = counts.get(bid, 0) + 1
    return counts


def _max_build_slots(planet: PlanetState, state: GameState) -> int:
    if _norm_planet_type(planet) == "asteroid":
        return 1
    specs = getattr(state, "specs", None)
    if isinstance(specs, Mapping) and _safe_strategy_int(specs.get("construction_commander", 0), 0) > 0:
        return 6
    return 5


def _effective_building_levels_and_energy(
    state: GameState,
    planet: PlanetState,
    *,
    is_active_planet: bool,
) -> tuple[dict[str, int], int]:
    """Project building levels and energy net after active queues complete."""
    sim_levels: dict[int, int] = {}
    eff_map: dict[str, int] = {
        catalog.safe_key(k): max(0, _safe_strategy_int(v, 0)) for k, v in (planet.buildings or {}).items()
    }
    projected_net = _safe_strategy_int(getattr(planet.energy, "net", 0), 0)

    for job in state.active_queues:
        if job.building_id is None:
            continue
        if not _building_job_matches_planet(job, state, planet, is_active_planet=is_active_planet):
            continue
        bid = catalog.safe_optional_int(job.building_id, None)
        if bid is None:
            continue
        cur_lvl = sim_levels.get(bid, planet.building_level(bid))
        j_tgt = catalog.safe_optional_int(job.target_level, None)
        max_tbl = max(
            _safe_strategy_int((catalog.BUILDING_BY_ID.get(bid) or {}).get("max_table_level", 100), 100),
            100,
        )
        if j_tgt is not None and 0 <= j_tgt < cur_lvl:
            tgt = j_tgt
            for step_lvl in range(tgt + 1, min(cur_lvl, max_tbl + 1) + 1):
                projected_net -= catalog.building_energy_delta(bid, step_lvl)
        else:
            tgt = j_tgt if (j_tgt is not None and j_tgt > cur_lvl) else cur_lvl + 1
            for step_lvl in range(cur_lvl + 1, min(tgt, max_tbl + 1) + 1):
                projected_net += catalog.building_energy_delta(bid, step_lvl)
        sim_levels[bid] = tgt
        eff_map[f"b{bid}"] = tgt
    return eff_map, projected_net


def _eff_level(planet: PlanetState, eff_buildings: Mapping[str, int], building_id: int) -> int:
    key = f"b{building_id}"
    return (
        max(0, _safe_strategy_int(eff_buildings[key], 0))
        if key in eff_buildings
        else int(planet.building_level(building_id))
    )


def _energy_starvation_delta(building_id: int, next_level: int, projected_energy_net: int) -> int:
    delta = catalog.building_energy_delta(building_id, next_level)
    return delta if (delta < 0 and (projected_energy_net + delta) < 0) else 0


def _active_research_increments(state: GameState) -> dict[str, int]:
    """Return in-flight research increments (+1) keyed by canonical research ID (`r1`..`r16`)."""
    inc: dict[str, int] = {}
    for r_off in state.research_offers or ():
        if getattr(r_off, "active", False):
            rk = _safe_strategy_str(getattr(r_off, "research_key", "")).strip()
            if rk:
                inc[rk] = max(inc.get(rk, 0), 1)
    for q in state.active_queues or ():
        if getattr(q, "category", "") != "research":
            continue
        raw_name = _safe_strategy_str(getattr(q, "name", ""))
        clean_name = re.sub(r"\(.*?\)", "", raw_name)
        clean_name = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", "", clean_name)
        clean_name = re.sub(r"\b(?:level|lvl|l)\s*\d+\b", "", clean_name, flags=re.IGNORECASE).strip()
        if not clean_name:
            continue
        r_unit = catalog.resolve_unit("research", clean_name)
        if r_unit and r_unit.get("id"):
            rk = str(r_unit["id"])
            inc[rk] = max(inc.get(rk, 0), 1)
            continue
        low_clean = clean_name.lower()
        for r_off in state.research_offers or ():
            if _safe_strategy_str(getattr(r_off, "name", "")).strip().lower() == low_clean:
                rk = _safe_strategy_str(getattr(r_off, "research_key", "")).strip()
                if rk:
                    inc[rk] = max(inc.get(rk, 0), 1)
                break
    return inc
