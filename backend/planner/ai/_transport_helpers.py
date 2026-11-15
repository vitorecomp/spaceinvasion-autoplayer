"""Helper protocols, rate/travel evaluators, in-progress mapping, and Rule A for transport needs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import math
from typing import Protocol, runtime_checkable

from backend.core import catalog as game_data
from backend.core.coercion import coerce_finite_float, coerce_int
from backend.planner.ai._problem_in_progress import _extract_live_in_progress_state
from backend.planner.ai._problem_queues import estimate_interplanet_transport_seconds
from backend.planner.ai._reconcile_helpers import (
    PlannedActionLike,
    ReadResourcesLike,
    resource_value,
    resources_float_list,
    resources_is_zero,
)
from backend.planner.ai._types import PATH_FLOW_BUILDING_KEYS, PATH_NEVER, GameStateLike, PlanetLike
from backend.planner.ai.problem import planet_hourly_output
from backend.planner.config_optimizer import (
    PRODUCTION_FLOW_KEYS,
    RESOURCE_KEYS,
    build_monotonic_base_times,
    building_level_flows,
    building_time_divisor,
    research_time_divisor,
    solar_cell_energy_per_unit,
    technology_multiplier,
)


@runtime_checkable
class _NetPerHourHolder(Protocol):
    net_per_hour: object


@runtime_checkable
class _RatesHolder(Protocol):
    rates: object


def select_lab_planet_index(state: GameStateLike, normal_planets: Sequence[PlanetLike]) -> int:
    """Return index of the highest-level Research lab normal planet (or ``-1`` if none)."""
    if not normal_planets:
        return -1
    best_lab_idx = max(
        range(len(normal_planets)),
        key=lambda i: (
            normal_planets[i].building_level(8),
            1 if (state.active_planet_id is not None and normal_planets[i].planet_id == state.active_planet_id) else 0,
        ),
    )
    return best_lab_idx if normal_planets[best_lab_idx].building_level(8) >= 1 else -1


def make_base_hours_fn() -> Callable[[str, int], float]:
    """Build a cached base-duration-in-hours lookup for buildings and research."""
    base_times_cache: dict[str, dict[int, float]] = {}

    def _base_hours(key: str, level: int) -> float:
        if key not in base_times_cache:
            info = game_data.BUILDING_BY_ID[int(key[1:])] if key.startswith("b") else game_data.RESEARCH_BY_ID[key]
            levels_map = info.get("levels") or {}
            top_level = max(int(info.get("max_table_level") or len(levels_map)), level)
            base_times_cache[key] = build_monotonic_base_times(levels_map, top_level)
        return float(base_times_cache[key].get(level, 0.0)) / 3600.0

    return _base_hours


def best_donor_travel_sec(
    dest_planet: PlanetLike,
    normal_planets: Sequence[PlanetLike],
    empire_ships: Mapping[str, int],
    researches: Mapping[str, int],
) -> float:
    """Return minimum one-way transport travel seconds from any donor normal planet to ``dest_planet``."""
    ship_donors = [
        p
        for p in normal_planets
        if p.planet_id != dest_planet.planet_id
        and sum(max(0, int((p.ships or {}).get(sk, 0) or 0)) for sk in ("s1", "s2", "s3")) > 0
    ]
    pool = ship_donors if ship_donors else [p for p in normal_planets if p.planet_id != dest_planet.planet_id]
    best_s = PATH_NEVER
    for src in pool:
        ow_s, _, _ = estimate_interplanet_transport_seconds(
            str(src.coords),
            str(dest_planet.coords),
            origin_ships=src.ships,
            empire_ships=empire_ships,
            researches=researches,
        )
        if ow_s < best_s:
            best_s = ow_s
    return 0.0 if not math.isfinite(best_s) else float(best_s)


def planet_hourly_rates(
    planet: PlanetLike,
    *,
    speedup: float,
    research_now: Mapping[str, int],
) -> tuple[float, float, float, float]:
    """Return projected hourly net production ``(pig_iron, metal, kryptonite, spice)`` for ``planet``."""
    if isinstance(planet, _RatesHolder) and isinstance(planet.rates, _NetPerHourHolder):
        net = planet.rates.net_per_hour
        if isinstance(net, ReadResourcesLike):
            vals = resources_float_list(net)
            if sum(vals) > 0.0:
                return (vals[0], vals[1], vals[2], vals[3])
    ctx = {
        "speedup_factor": speedup,
        "planet_temperature_c": int(planet.temperature),
        "r1_mining_tech": research_now.get("r1", 0),
        "r2_energy_tech": research_now.get("r2", 0),
    }
    totals = [0.0] * len(PRODUCTION_FLOW_KEYS)
    for bk in PATH_FLOW_BUILDING_KEYS:
        lvl = max(0, int((planet.buildings or {}).get(bk, 0) or 0))
        if lvl > 0:
            for pos, fv in enumerate(building_level_flows(bk, lvl, ctx)):
                totals[pos] += fv
    s9 = max(0, int((planet.ships or {}).get("s9", 0) or 0))
    if s9 > 0:
        totals[6] += (
            s9
            * solar_cell_energy_per_unit(int(planet.temperature))
            * technology_multiplier("r2", research_now.get("r2", 0))
        )
    out = planet_hourly_output(totals)
    return (max(0.0, out[1]), max(0.0, out[2]), max(0.0, out[3]), max(0.0, out[4]))


def storage_cap_ratio_for(planet: PlanetLike, cost_vals: Sequence[float]) -> float:
    """Compute effective storage fill ratio ceiling allowing shipments up to ``cost_vals``."""
    ratio = 0.85
    for pos, res_key in enumerate(RESOURCE_KEYS):
        cap = max(0, resource_value(planet.storage, res_key))
        if cap > 0 and cost_vals[pos] > cap * 0.85 and cost_vals[pos] <= cap:
            ratio = max(ratio, min(0.98, (cost_vals[pos] / float(cap)) + 0.02))
    return round(ratio, 4)


def merge_need_entry(
    needs: dict[int, dict[str, object]],
    pid: int,
    *,
    gap: Sequence[int],
    label: str,
    need_by_sec: float,
    lead_sec: float,
    cap_ratio: float,
) -> None:
    """Insert or max-merge a per-planet transport need dict."""
    prev = needs.get(pid)
    if prev is None:
        needs[pid] = {
            "pig_iron": gap[0],
            "metal": gap[1],
            "kryptonite": gap[2],
            "spice": gap[3],
            "label": label,
            "need_by_sec": round(need_by_sec, 1),
            "lead_sec": round(lead_sec, 1),
            "storage_cap_ratio": cap_ratio,
        }
        return
    for pos, res_key in enumerate(RESOURCE_KEYS):
        prev[res_key] = max(coerce_int(prev.get(res_key), 0), gap[pos])
    prev_ratio = prev.get("storage_cap_ratio")
    prev_ratio_f = float(prev_ratio) if isinstance(prev_ratio, (int, float)) else 0.0
    prev["storage_cap_ratio"] = max(prev_ratio_f, cap_ratio)


def extract_in_progress_maps(
    state: GameStateLike,
    normal_planets: Sequence[PlanetLike],
    *,
    speedup: float,
    research_now: Mapping[str, int],
    lab_idx: int,
    base_hours_fn: Callable[[str, int], float],
) -> tuple[dict[int, list[float]], dict[int, float], dict[int, dict[str, int]], float, dict[str, int]]:
    """Extract inbound cargo, active queue remaining times, and effective post-queue levels."""
    mini_planets: list[dict[str, object]] = [
        {
            "index": idx,
            "planet_id": int(p.planet_id),
            "coords": str(p.coords),
            "name": str(p.name or p.coords),
            "levels": {str(k): max(0, int(v or 0)) for k, v in (p.buildings or {}).items() if max(0, int(v or 0)) > 0},
        }
        for idx, p in enumerate(normal_planets)
    ]
    in_prog_builds, in_prog_research, in_flight_arrivals = _extract_live_in_progress_state(
        state,
        mini_planets,
        speedup=speedup,
        research_now=research_now,
        base_hours_fn=base_hours_fn,
        lab_planet_index=lab_idx,
    )
    inbound_by_pid: dict[int, list[float]] = {}
    for arr in in_flight_arrivals:
        pid = coerce_int(arr.get("planet_id"), 0)
        buf = inbound_by_pid.setdefault(pid, [0.0, 0.0, 0.0, 0.0])
        ct = arr.get("cargo_tuple")
        if isinstance(ct, Sequence) and len(ct) >= 4:
            for pos in range(4):
                buf[pos] += coerce_finite_float(ct[pos], 0.0)

    queue_rem_by_pid: dict[int, float] = {}
    eff_buildings_by_pid: dict[int, dict[str, int]] = {
        int(p.planet_id): {
            str(k): max(0, int(v or 0)) for k, v in (p.buildings or {}).items() if max(0, int(v or 0)) > 0
        }
        for p in normal_planets
    }
    for job in in_prog_builds:
        pid = coerce_int(job.get("planet_id"), 0)
        bkey = str(job.get("key") or "")
        lvl = coerce_int(job.get("level_to"), 0)
        rem_s = max(0.0, coerce_finite_float(job.get("remaining_seconds"), 0.0))
        queue_rem_by_pid[pid] = max(queue_rem_by_pid.get(pid, 0.0), rem_s)
        pmap = eff_buildings_by_pid.setdefault(pid, {})
        pmap[bkey] = max(pmap.get(bkey, 0), lvl)

    research_rem_sec = 0.0
    eff_researches = dict(research_now)
    for job in in_prog_research:
        rkey = str(job.get("key") or "")
        lvl = coerce_int(job.get("level_to"), 0)
        rem_s = max(0.0, coerce_finite_float(job.get("remaining_seconds"), 0.0))
        research_rem_sec = max(research_rem_sec, rem_s)
        eff_researches[rkey] = max(eff_researches.get(rkey, 0), lvl)
    return inbound_by_pid, queue_rem_by_pid, eff_buildings_by_pid, research_rem_sec, eff_researches


def process_rule_a_actions(
    ai_actions: Sequence[PlannedActionLike],
    *,
    planets_by_id: Mapping[int, PlanetLike],
    normal_planets: Sequence[PlanetLike],
    empire_ships: Mapping[str, int],
    researches: Mapping[str, int],
    inbound_cargo_by_pid: Mapping[int, Sequence[float]],
    eff_buildings_by_pid: dict[int, dict[str, int]],
    eff_researches: dict[str, int],
    dispatch_delay_sec: float,
    speedup: float,
    base_hours_fn: Callable[[str, int], float],
) -> tuple[dict[int, dict[str, object]], dict[int, list[float]], dict[int, float], float]:
    """Apply Rule A for stalled actions and track launching action costs/durations for Rule C."""
    needs: dict[int, dict[str, object]] = {}
    exec_cost_by_pid: dict[int, list[float]] = {}
    exec_dur_by_pid: dict[int, float] = {}
    exec_res_dur = 0.0

    for act in ai_actions:
        if act.kind not in ("BUILD", "WAIT_RESOURCES", "RESEARCH"):
            continue
        pid = int(act.planet_id)
        planet = planets_by_id.get(pid)
        if planet is None:
            continue
        if act.executable_now:
            buf = exec_cost_by_pid.setdefault(pid, [0.0, 0.0, 0.0, 0.0])
            for pos, val in enumerate(resources_float_list(act.cost)):
                buf[pos] += val
            if act.kind == "BUILD" and act.target_id is not None and act.target_level:
                bkey = f"b{int(act.target_id)}"
                eff_buildings_by_pid.setdefault(pid, {})[bkey] = max(
                    eff_buildings_by_pid.get(pid, {}).get(bkey, 0), int(act.target_level)
                )
                div = building_time_divisor(planet.building_level(7), planet.building_level(17), speedup)
                dur_s = (base_hours_fn(bkey, int(act.target_level)) / max(1e-9, div)) * 3600.0
                exec_dur_by_pid[pid] = max(exec_dur_by_pid.get(pid, 0.0), dur_s)
            elif act.kind == "RESEARCH" and act.target_id is not None and act.target_level:
                rkey = f"r{int(act.target_id)}"
                eff_researches[rkey] = max(eff_researches.get(rkey, 0), int(act.target_level))
                div = research_time_divisor(planet.building_level(8), speedup)
                dur_s = (base_hours_fn(rkey, int(act.target_level)) / max(1e-9, div)) * 3600.0
                exec_res_dur = max(exec_res_dur, dur_s)
            continue

        if resources_is_zero(act.missing):
            continue
        inb = inbound_cargo_by_pid.get(pid, (0.0, 0.0, 0.0, 0.0))
        missing_vals = resources_float_list(act.missing)
        gap = [max(0, int(math.ceil(max(0.0, missing_vals[pos] - inb[pos])))) for pos in range(4)]
        if sum(gap) <= 0:
            continue
        lead_s = dispatch_delay_sec + best_donor_travel_sec(planet, normal_planets, empire_ships, researches)
        cost_vals = resources_float_list(act.cost)
        merge_need_entry(
            needs,
            pid,
            gap=gap,
            label=f"{act.target_name} L{act.target_level}" if act.target_level else str(act.target_name),
            need_by_sec=0.0,
            lead_sec=lead_s,
            cap_ratio=storage_cap_ratio_for(planet, cost_vals),
        )
    return needs, exec_cost_by_pid, exec_dur_by_pid, exec_res_dur
