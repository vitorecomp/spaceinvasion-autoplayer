"""Causal path-problem construction from live ``GameState`` to ``target_config``."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from spaceinvasion.core import game_data
from spaceinvasion.core.coercion import coerce_finite_float, coerce_int
from spaceinvasion.core.model import normalize_planet_type
from spaceinvasion.planner.ai._problem_in_progress import _extract_live_in_progress_state
from spaceinvasion.planner.ai._problem_planets import (
    _extract_explicit_demolition_targets,
    _extract_target_buildings,
    _match_live_planet,
    resolve_problem_planets,
)
from spaceinvasion.planner.ai._problem_queues import summarize_blast_furnace_entries
from spaceinvasion.planner.ai._problem_steps import (
    _path_requirements,
    aggregate_start_stock,
    apply_in_progress_floors,
    compute_transport_matrices,
    generate_and_validate_steps,
)
from spaceinvasion.planner.ai._types import GameStateLike, safe_speedup
from spaceinvasion.planner.config_optimizer import (
    build_monotonic_base_times,
    normalize_planet_names_map,
    research_catalog,
)


def planet_hourly_output(totals: Sequence[float]) -> tuple[float, float, float, float, float, float, float]:
    """Return ``(pig_iron_mined, pig_iron_left, metal, kryptonite, spice, power_ratio, smelting_ratio)``."""
    pig_iron, metal, kryptonite, spice, pig_iron_used, spice_used, energy_produced, energy_used = totals
    power_ratio = min(1.0, energy_produced / energy_used) if energy_used > 0.0 else 1.0
    mined = pig_iron * power_ratio
    wanted = pig_iron_used * power_ratio
    smelting_ratio = min(1.0, mined / wanted) if wanted > 0.0 else 1.0
    return (
        mined,
        mined - wanted * smelting_ratio,
        metal * power_ratio * smelting_ratio,
        kryptonite * power_ratio,
        spice * power_ratio - spice_used,
        power_ratio,
        smelting_ratio,
    )


def _resolve_target_meta(
    target_config: Mapping[str, object], planet_names: Mapping[str, str] | None
) -> tuple[dict[str, str], bool]:
    merged: dict[str, str] = {}
    tc_meta, tc_summary = target_config.get("meta_parameters"), target_config.get("summary")
    allow_demo = False
    if isinstance(tc_meta, Mapping):
        merged.update(normalize_planet_names_map(tc_meta.get("planet_names")))
        merged.update(normalize_planet_names_map(tc_meta.get("custom_planet_names")))
        allow_demo = bool(tc_meta.get("allow_demolition", False) or tc_meta.get("ALLOW_DEMOLITION", False))
    if not allow_demo and isinstance(tc_summary, Mapping):
        allow_demo = bool(tc_summary.get("allow_demolition", False))
    merged.update(normalize_planet_names_map(target_config.get("planet_names")))
    merged.update(normalize_planet_names_map(target_config.get("custom_planet_names")))
    if isinstance(planet_names, Mapping):
        merged.update(normalize_planet_names_map(planet_names))
    return merged, allow_demo


def _select_central_and_lab_planets(problem_planets: Sequence[dict[str, object]]) -> tuple[int, int]:
    central = next((coerce_int(p["index"], 0) for p in problem_planets if bool(p["is_central_planet"])), 0)
    problem_planets[central]["is_central_planet"] = True
    lab = next((coerce_int(p["index"], -1) for p in problem_planets if bool(p["pays_for_research"])), -1)
    if lab < 0:
        best_lab = max(
            problem_planets,
            key=lambda p: (
                coerce_int(p["target_levels"].get("b8", 0) if isinstance(p["target_levels"], Mapping) else 0, 0),
                coerce_int(p["levels"].get("b8", 0) if isinstance(p["levels"], Mapping) else 0, 0),
            ),
        )
        b_tl = best_lab["target_levels"]
        if isinstance(b_tl, Mapping) and coerce_int(b_tl.get("b8", 0), 0) > 0:
            lab = coerce_int(best_lab["index"], 0)
            problem_planets[lab]["pays_for_research"] = True
    return central, lab


def _summarize_problem_meta(
    problem_planets: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], dict[str, dict[str, int]]]:
    init_bf = summarize_blast_furnace_entries(
        [
            {
                "planet_index": coerce_int(p["index"], 0),
                "planet_id": coerce_int(p["planet_id"], 0),
                "coords": str(p["coords"]),
                "name": str(p["name"]),
                "b2_level": coerce_int(p["levels"].get("b2", 0) if isinstance(p["levels"], Mapping) else 0, 0),
                "initial_pct": coerce_int(p.get("blast_furnace_pct"), 0),
                "rate_known": bool(p.get("blast_furnace_rate_known", False)),
            }
            for p in problem_planets
            if bool(p.get("founded"))
        ]
    )
    demo_by_pid = {
        str(coerce_int(p["planet_id"], 0)): {str(bk): max(0, coerce_int(lvl, 0)) for bk, lvl in df.items()}
        for p in problem_planets
        if isinstance((df := p.get("demolish_floors")), Mapping) and df and coerce_int(p["planet_id"], 0) > 0
    }
    return init_bf, demo_by_pid


def _make_base_hours_fn() -> Callable[[str, int], float]:
    base_times: dict[str, dict[int, float]] = {}

    def _base_hours(key: str, level: int) -> float:
        if key not in base_times:
            info = game_data.BUILDING_BY_ID[int(key[1:])] if key.startswith("b") else game_data.RESEARCH_BY_ID[key]
            lmap = info.get("levels") or {}
            base_times[key] = build_monotonic_base_times(
                lmap, max(int(info.get("max_table_level") or len(lmap)), level)
            )
        return float(base_times[key].get(level, 0.0)) / 3600.0

    return _base_hours


def build_path_problem(
    state: GameStateLike,
    target_config: Mapping[str, object],
    *,
    pad_generic_colonies: bool = False,
    colony_founding_hours: float = 0.0,
    demolition_time_factor: float = 1.0,
    planet_names: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Build the causal step problem from live ``state`` to ``target_config``."""
    if not isinstance(target_config, Mapping) or target_config.get("schema_version") != 1:
        found = (
            target_config.get("schema_version") if isinstance(target_config, Mapping) else type(target_config).__name__
        )
        raise ValueError(f"target_config must be a schema_version=1 dict, got {found!r}")
    raw_planets = target_config.get("planets")
    if not isinstance(raw_planets, Sequence) or isinstance(raw_planets, (str, bytes)) or not raw_planets:
        raise ValueError("target_config['planets'] must be a non-empty sequence")

    merged_names, allow_demo = _resolve_target_meta(target_config, planet_names)
    raw_inputs = target_config.get("inputs")
    speed_in = raw_inputs.get("universe_speedup_factor") if isinstance(raw_inputs, Mapping) else None
    speedup = safe_speedup(state, {"speedup_factor": speed_in})
    owned_normal = [p for p in state.planets if normalize_planet_type(p) == "normal"]

    research_now = {str(k): max(0, int(state.researches.get(str(k), 0) or 0)) for k, _ in research_catalog()}
    raw_pr = target_config.get("planned_research_levels")
    pr_map: Mapping[str, object] = raw_pr if isinstance(raw_pr, Mapping) else {}
    research_target = {k: max(research_now[k], max(0, coerce_int(pr_map.get(k, 0), 0))) for k in research_now}

    problem_planets, used_ids = resolve_problem_planets(
        raw_planets,
        owned_normal,
        allow_demolition=allow_demo,
        pad_generic_colonies=pad_generic_colonies,
        merged_planet_names=merged_names,
    )
    central_planet, lab_planet = _select_central_and_lab_planets(problem_planets)
    base_hours = _make_base_hours_fn()
    in_prog_builds, in_prog_res, in_flight = _extract_live_in_progress_state(
        state,
        problem_planets,
        speedup=speedup,
        research_now=research_now,
        base_hours_fn=base_hours,
        lab_planet_index=lab_planet,
    )
    in_prog_b_max, in_prog_r_max = apply_in_progress_floors(
        problem_planets, research_target, in_prog_builds, in_prog_res
    )
    steps = generate_and_validate_steps(
        problem_planets,
        research_now=research_now,
        research_target=research_target,
        in_progress_b_max=in_prog_b_max,
        in_progress_r_max=in_prog_r_max,
        allow_demolition=allow_demo,
        demolition_time_factor=demolition_time_factor,
        colony_founding_hours=colony_founding_hours,
        lab_planet=lab_planet,
        central_planet=central_planet,
        base_hours=base_hours,
    )
    stock = aggregate_start_stock(state, problem_planets, used_ids, central_planet)
    emp_ships, fb_dist, t_sec, t_hrs, d_units, hulls, avg_sec, max_sec = compute_transport_matrices(
        problem_planets, owned_normal, research_now
    )
    init_bf, demo_by_pid = _summarize_problem_meta(problem_planets)
    return {
        "speedup_factor": speedup,
        "planets": problem_planets,
        "allow_demolition": bool(allow_demo),
        "demolish_targets": demo_by_pid,
        "initial_blast_furnace": init_bf,
        "research_levels": research_now,
        "research_target": research_target,
        "lab_planet": lab_planet,
        "central_planet": central_planet,
        "start_stock": tuple(stock),
        "steps": steps,
        "in_progress_builds": in_prog_builds,
        "in_progress_research": in_prog_res,
        "in_flight_arrivals": in_flight,
        "empire_transport_ships": emp_ships,
        "fallback_distance_units": fb_dist,
        "travel_seconds": t_sec,
        "travel_hours": t_hrs,
        "distance_units": d_units,
        "transport_hull_by_planet": hulls,
        "avg_transport_travel_seconds": round(avg_sec, 2),
        "max_transport_travel_seconds": round(max_sec, 2),
        "live_snapshot_ts": max(0.0, coerce_finite_float(state.timestamp, 0.0)),
        "started_from_live_state": True,
    }


__all__ = [
    "_extract_explicit_demolition_targets",
    "_extract_target_buildings",
    "_match_live_planet",
    "_path_requirements",
    "build_path_problem",
    "planet_hourly_output",
]
