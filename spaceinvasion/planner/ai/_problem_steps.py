"""Step generation, prerequisite validation, and transport matrix helpers for ``build_path_problem``."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import numpy as np

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.catalog import SHIP_SLUG_BY_KEY
from spaceinvasion.core.coercion import coerce_int
from spaceinvasion.core.coords import flight_distance as _flight_distance_units
from spaceinvasion.planner.ai._problem_queues import estimate_interplanet_transport_seconds
from spaceinvasion.planner.ai._types import GameStateLike, PlanetLike, is_valid_coords, resources_tuple
from spaceinvasion.planner.config_optimizer import (
    RESOURCE_KEYS,
    TECH_GRAPH,
    WAREHOUSE_KEYS,
    full_cost,
    research_catalog,
    warehouse_level_for,
)


def _path_requirements(key: str, building_owner: int) -> list[tuple[int, str, int]]:
    return [
        (building_owner if TECH_GRAPH.nodes[source]["category"] == "buildings" else -1, str(source), int(level))
        for source, level in TECH_GRAPH.prereqs_of[key]
    ]


def _cost_tuple(raw_cost: object) -> tuple[float, float, float, float]:
    cost = full_cost(raw_cost)
    return (float(cost["pig_iron"]), float(cost["metal"]), float(cost["kryptonite"]), float(cost["spice"]))


def _append_step(
    steps: list[dict[str, object]], step: dict[str, object], cost_t: tuple[float, float, float, float]
) -> None:
    step["id"] = len(steps)
    step["cost"] = cost_t
    step["cost_spice_eq"] = float(game_data.spice_equivalent(dict(zip(RESOURCE_KEYS, cost_t, strict=False))))
    step["warehouse_levels"] = tuple(int(warehouse_level_for(amount)) for amount in cost_t)
    steps.append(step)


def _add_building_steps(
    steps: list[dict[str, object]],
    problem_planets: Sequence[Mapping[str, object]],
    *,
    in_progress_b_max: Mapping[tuple[int, str], int],
    allow_demolition: bool,
    demolition_time_factor: float,
    base_hours: Callable[[str, int], float],
) -> None:
    for planet in problem_planets:
        idx = coerce_int(planet["index"], 0)
        lvls = planet["levels"]
        tlvl = planet["target_levels"]
        lvls_m: Mapping[str, int] = lvls if isinstance(lvls, Mapping) else {}
        tlvl_m: Mapping[str, int] = tlvl if isinstance(tlvl, Mapping) else {}
        for bid in sorted(game_data.BUILDING_BY_ID):
            key, name = f"b{bid}", str(game_data.BUILDING_BY_ID[bid]["name"])
            start_after = max(int(lvls_m.get(key, 0)), int(in_progress_b_max.get((idx, key), 0)))
            wanted = int(tlvl_m.get(key, 0))
            for level in range(start_after + 1, wanted + 1):
                _append_step(
                    steps,
                    {
                        "kind": "build",
                        "planet": idx,
                        "key": key,
                        "name": name,
                        "level_from": level - 1,
                        "level_to": level,
                        "base_hours": base_hours(key, level),
                        "queue": idx,
                        "pay_planet": idx,
                        "requires": _path_requirements(key, idx),
                        "chain": ("build", idx, key),
                    },
                    _cost_tuple(game_data.building_cost(bid, level)),
                )
            if allow_demolition and start_after > wanted:
                for level in range(start_after, wanted, -1):
                    _append_step(
                        steps,
                        {
                            "kind": "demolish",
                            "planet": idx,
                            "key": key,
                            "name": name,
                            "level_from": level,
                            "level_to": level - 1,
                            "base_hours": max(0.0, float(demolition_time_factor)) * base_hours(key, level),
                            "queue": idx,
                            "pay_planet": idx,
                            "requires": [],
                            "chain": ("demolish", idx, key),
                        },
                        (0.0, 0.0, 0.0, 0.0),
                    )


def _add_research_and_colonize_steps(
    steps: list[dict[str, object]],
    problem_planets: Sequence[Mapping[str, object]],
    *,
    research_now: Mapping[str, int],
    research_target: Mapping[str, int],
    in_progress_r_max: Mapping[str, int],
    colony_founding_hours: float,
    lab_planet: int,
    central_planet: int,
    base_hours: Callable[[str, int], float],
) -> None:
    p_count = len(problem_planets)
    for key, info in research_catalog():
        start_after = max(int(research_now[key]), int(in_progress_r_max.get(key, 0)))
        for level in range(start_after + 1, int(research_target[key]) + 1):
            if lab_planet < 0:
                raise ValueError(
                    f"{info['name']} {level} is planned, but no planet in target_config has a Research lab (b8)"
                )
            _append_step(
                steps,
                {
                    "kind": "research",
                    "planet": -1,
                    "key": str(key),
                    "name": str(info["name"]),
                    "level_from": level - 1,
                    "level_to": level,
                    "base_hours": base_hours(str(key), level),
                    "queue": p_count,
                    "pay_planet": lab_planet,
                    "requires": _path_requirements(str(key), lab_planet),
                    "chain": ("research", -1, str(key)),
                },
                _cost_tuple(((info.get("levels") or {}).get(level) or {}).get("cost")),
            )
    colony_ship = game_data.SHIPS[SHIP_SLUG_BY_KEY["s8"]]
    for planet in problem_planets:
        if not bool(planet["founded"]):
            idx = coerce_int(planet["index"], 0)
            _append_step(
                steps,
                {
                    "kind": "colonize",
                    "planet": idx,
                    "key": "s8",
                    "name": str(colony_ship["name"]),
                    "level_from": 0,
                    "level_to": 1,
                    "base_hours": max(0.0, float(colony_founding_hours)),
                    "queue": -1,
                    "pay_planet": central_planet,
                    "requires": [],
                    "chain": ("colonize", idx, "s8"),
                },
                _cost_tuple(colony_ship.get("base_cost")),
            )


def generate_and_validate_steps(
    problem_planets: Sequence[Mapping[str, object]],
    *,
    research_now: Mapping[str, int],
    research_target: Mapping[str, int],
    in_progress_b_max: Mapping[tuple[int, str], int],
    in_progress_r_max: Mapping[str, int],
    allow_demolition: bool,
    demolition_time_factor: float,
    colony_founding_hours: float,
    lab_planet: int,
    central_planet: int,
    base_hours: Callable[[str, int], float],
) -> list[dict[str, object]]:
    """Generate causal build/demolish/research/colonize steps and validate reachability."""
    steps: list[dict[str, object]] = []
    _add_building_steps(
        steps,
        problem_planets,
        in_progress_b_max=in_progress_b_max,
        allow_demolition=allow_demolition,
        demolition_time_factor=demolition_time_factor,
        base_hours=base_hours,
    )
    _add_research_and_colonize_steps(
        steps,
        problem_planets,
        research_now=research_now,
        research_target=research_target,
        in_progress_r_max=in_progress_r_max,
        colony_founding_hours=colony_founding_hours,
        lab_planet=lab_planet,
        central_planet=central_planet,
        base_hours=base_hours,
    )
    _validate_step_reachability(steps, problem_planets, research_target)
    return steps


def _validate_step_reachability(
    steps: Sequence[Mapping[str, object]],
    problem_planets: Sequence[Mapping[str, object]],
    research_target: Mapping[str, int],
) -> None:
    for step in steps:
        p_idx = coerce_int(step["planet"], -1)
        label = f"{step['name']} {step['level_to']}" + (f" on {problem_planets[p_idx]['coords']}" if p_idx >= 0 else "")
        reqs = step["requires"]
        if isinstance(reqs, Sequence):
            for req in reqs:
                owner, req_key, req_lvl = int(req[0]), str(req[1]), int(req[2])
                if owner < 0:
                    final_level, where = int(research_target.get(req_key, 0)), "planned research"
                else:
                    ot = problem_planets[owner]["target_levels"]
                    final_level = int(ot.get(req_key, 0)) if isinstance(ot, Mapping) else 0
                    where = f"plan of {problem_planets[owner]['coords']}"
                if final_level < req_lvl:
                    raise ValueError(
                        f"{label} needs {TECH_GRAPH.nodes[req_key]['name']} {req_lvl}, but {where} ends at {final_level}"
                    )
        pay_idx = coerce_int(step["pay_planet"], 0)
        raw_pl = problem_planets[pay_idx]["target_levels"]
        pay_levels: Mapping[str, int] = raw_pl if isinstance(raw_pl, Mapping) else {}
        wh_lvls = step["warehouse_levels"]
        if isinstance(wh_lvls, Sequence):
            for resource, wh_key, needed in zip(RESOURCE_KEYS, WAREHOUSE_KEYS, wh_lvls, strict=False):
                if int(needed) > int(pay_levels.get(wh_key, 0)):
                    raise ValueError(
                        f"{label} costs more {resource} than {TECH_GRAPH.nodes[wh_key]['name']} on "
                        f"{problem_planets[pay_idx]['coords']} holds at target level "
                        f"{pay_levels.get(wh_key, 0)} (level {needed} needed)"
                    )


def compute_transport_matrices(
    problem_planets: Sequence[Mapping[str, object]],
    owned_normal: Sequence[PlanetLike],
    research_now: Mapping[str, int],
) -> tuple[dict[str, int], float, list[list[float]], list[list[float]], list[list[float]], list[str], float, float]:
    """Precompute pairwise distance, one-way travel seconds/hours, and representative transport hulls."""
    empire_ships: dict[str, int] = {"s1": 0, "s2": 0, "s3": 0}
    for p in owned_normal:
        for sk in ("s1", "s2", "s3"):
            empire_ships[sk] += max(0, int(p.ships.get(sk, 0) or 0))
    n = len(problem_planets)
    real_dists: list[float] = []
    for i in range(n):
        ci = str(problem_planets[i]["coords"])
        if not is_valid_coords(ci):
            continue
        for j in range(i + 1, n):
            cj = str(problem_planets[j]["coords"])
            if is_valid_coords(cj):
                real_dists.append(float(_flight_distance_units(ci, cj)))
    fallback_dist = float(np.median(np.asarray(real_dists, dtype=np.float64))) if real_dists else 1175.0
    t_sec = [[0.0] * n for _ in range(n)]
    t_hrs = [[0.0] * n for _ in range(n)]
    d_units = [[0.0] * n for _ in range(n)]
    hulls = ["s2"] * n
    off_diag: list[float] = []
    for i in range(n):
        raw_ships = problem_planets[i].get("ships")
        src_ships: Mapping[str, object] = raw_ships if isinstance(raw_ships, Mapping) else {}
        src_coords = str(problem_planets[i]["coords"])
        for j in range(n):
            if i == j:
                continue
            ow, du, hull = estimate_interplanet_transport_seconds(
                src_coords,
                str(problem_planets[j]["coords"]),
                origin_ships=src_ships,
                empire_ships=empire_ships,
                researches=research_now,
                fallback_distance_units=fallback_dist,
            )
            t_sec[i][j], t_hrs[i][j], d_units[i][j], hulls[i] = ow, ow / 3600.0, du, hull
            off_diag.append(ow)
    avg_sec = float(np.mean(np.asarray(off_diag, dtype=np.float64))) if off_diag else 0.0
    max_sec = float(max(off_diag)) if off_diag else 0.0
    return empire_ships, fallback_dist, t_sec, t_hrs, d_units, hulls, avg_sec, max_sec


def apply_in_progress_floors(
    problem_planets: Sequence[dict[str, object]],
    research_target: dict[str, int],
    in_prog_builds: Sequence[Mapping[str, object]],
    in_prog_res: Sequence[Mapping[str, object]],
) -> tuple[dict[tuple[int, str], int], dict[str, int]]:
    """Raise planet target floors and field caps to cover already-running queues."""
    b_max: dict[tuple[int, str], int] = {}
    b_cnt: dict[int, int] = {}
    for job in in_prog_builds:
        p_idx, bkey, lvl_to = coerce_int(job["planet"], 0), str(job["key"]), coerce_int(job["level_to"], 0)
        b_max[(p_idx, bkey)] = max(b_max.get((p_idx, bkey), 0), lvl_to)
        b_cnt[p_idx] = b_cnt.get(p_idx, 0) + 1
        tl = problem_planets[p_idx]["target_levels"]
        if isinstance(tl, dict) and lvl_to > coerce_int(tl.get(bkey, 0), 0):
            tl[bkey] = lvl_to
    for p_idx, cnt in b_cnt.items():
        lvls = problem_planets[p_idx]["levels"]
        used_plus = (sum(coerce_int(v, 0) for v in lvls.values()) if isinstance(lvls, Mapping) else 0) + cnt
        if used_plus > coerce_int(problem_planets[p_idx]["fields_max"], 0):
            problem_planets[p_idx]["fields_max"] = used_plus
    r_max: dict[str, int] = {}
    for job in in_prog_res:
        rkey, lvl_to = str(job["key"]), coerce_int(job["level_to"], 0)
        r_max[rkey] = max(r_max.get(rkey, 0), lvl_to)
        if lvl_to > research_target.get(rkey, 0):
            research_target[rkey] = lvl_to
    return b_max, r_max


def aggregate_start_stock(
    state: GameStateLike,
    problem_planets: Sequence[dict[str, object]],
    used_ids: set[int],
    central_planet: int,
) -> list[float]:
    """Sum live planet resources across owned planets, crediting unmatched planets to ``central_planet``."""
    stock = [0.0, 0.0, 0.0, 0.0]
    extra = [0.0, 0.0, 0.0, 0.0]
    for planet in state.planets:
        is_bound = int(planet.planet_id) in used_ids
        r_tup = resources_tuple(planet.resources)
        for pos in range(4):
            stock[pos] += r_tup[pos]
            if not is_bound:
                extra[pos] += r_tup[pos]
    if any(v > 0.0 for v in extra):
        cp_res = problem_planets[central_planet]["resources"]
        if isinstance(cp_res, tuple) and len(cp_res) == 4:
            problem_planets[central_planet]["resources"] = tuple(float(cp_res[i]) + extra[i] for i in range(4))
    return stock
