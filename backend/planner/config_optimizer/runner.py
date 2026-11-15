"""Top-level ``run_config_optimizer`` entry point and setup/stability orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import math
import time
from typing import Protocol, runtime_checkable

from backend.core.coercion import coerce_finite_float, coerce_int, coerce_optional_int
from backend.core.model import normalize_planet_type
from backend.planner.config_optimizer._protocols import (
    PlanetLike,
    as_int_dict,
    as_mapping,
    as_str_dict,
    map_float,
    map_int,
    map_str,
)
from backend.planner.config_optimizer._runner_report import (
    PreparedInputs,
    ReportInputs,
    StabilitySelection,
    assemble_optimizer_result,
)
from backend.planner.config_optimizer.catalog_math import research_catalog
from backend.planner.config_optimizer.constants import SPECIALIZATION_ALLOWED_BUILDINGS, IntArray
from backend.planner.config_optimizer.model import EmpireProductionModel, build_planet_plans, plan_research_levels
from backend.planner.config_optimizer.params import (
    _apply_manual_planet_pins,
    _empty_colony_slot,
    _normalize_params,
    check_optimizer_settings,
    normalize_planet_names_map,
)
from backend.planner.config_optimizer.search import search_in_rounds
from backend.planner.config_optimizer.stability import (
    _align_unlocked_planet_roles_for_sunk_cost,
    _build_sticky_candidate,
    _evaluate_stability_metrics,
    _extract_previous_planet_state,
)
from backend.planner.config_optimizer.tech_graph import research_requirements, research_upgrade_costs


@runtime_checkable
class OptimizerStateLike(Protocol):
    """Read-only game state contract satisfied by both ``core.model.GameState`` and ``core.state.GameState``."""

    @property
    def planets(self) -> Sequence[PlanetLike]: ...

    @property
    def researches(self) -> Mapping[str, int]: ...

    @property
    def speedup_factor(self) -> float | None: ...


def _resolve_speedup(state: OptimizerStateLike, params: Mapping[str, object]) -> float:
    for candidate in (state.speedup_factor, params.get("speedup_factor")):
        if candidate is None or isinstance(candidate, bool):
            continue
        val = coerce_finite_float(candidate, 0.0)
        if math.isfinite(val) and val > 0.0:
            return val
    return 1.0


def _extract_reserved_floors(raw: object) -> dict[str, dict[str, int]]:
    if not isinstance(raw, Mapping):
        return {}
    return {str(pk): as_int_dict(bmap) for pk, bmap in raw.items() if isinstance(bmap, Mapping)}


def _extract_step_sizes(raw: object) -> tuple[int, ...]:
    if isinstance(raw, (list, tuple)):
        steps = tuple(max(1, coerce_int(item, 1)) for item in raw)
        if steps:
            return steps
    return (1, 2, 4, 8)


def _prepare_normalized_config(
    params: Mapping[str, object] | None,
    planet_names: Mapping[str, str] | None,
) -> tuple[dict[str, object], dict[int, dict[str, int]]]:
    cfg = _normalize_params(params)
    if isinstance(planet_names, Mapping) and planet_names:
        cfg["planet_names"] = {**as_str_dict(cfg["planet_names"]), **normalize_planet_names_map(planet_names)}
    misc_by_slot_int: dict[int, dict[str, int]] = {}
    raw_by_slot = cfg.get("misc_building_levels_by_slot")
    if isinstance(raw_by_slot, Mapping):
        for slot_k, bmap in raw_by_slot.items():
            slot_i = coerce_optional_int(slot_k, None)
            if slot_i is not None and isinstance(bmap, Mapping):
                misc_by_slot_int[slot_i] = {str(k): coerce_int(v, 0) for k, v in bmap.items()}
    check_optimizer_settings(
        as_int_dict(cfg["optimized_building_max_levels"]),
        as_int_dict(cfg["misc_building_levels"]),
        misc_by_slot_int,
        as_int_dict(cfg["min_fleet_per_planet"]),
        map_str(cfg, "target_ship_key", "s16"),
        map_float(cfg, "warehouse_hours_of_production", 24.0),
        map_int(cfg, "central_planet_min_warehouse_level", 15),
        map_int(cfg, "min_warehouse_level", 0),
    )
    return cfg, misc_by_slot_int


def _build_roster_and_research(
    state: OptimizerStateLike,
    cfg: Mapping[str, object],
    misc_by_slot_int: Mapping[int, Mapping[str, int]],
    universe_research_max: Mapping[str, int] | None,
) -> PreparedInputs:
    owned_normal: list[PlanetLike] = [
        p for p in (state.planets or ()) if normalize_planet_type(p.planet_type) == "normal"
    ]
    roster_planets: list[PlanetLike] = list(owned_normal)
    max_planets = map_int(cfg, "max_planets", 11)
    if bool(cfg["pad_generic_colonies"]) and len(owned_normal) < max_planets:
        empty_temp = map_int(cfg, "empty_colony_temperature_c", 0)
        empty_fields = map_int(cfg, "empty_colony_fields", 300)
        for slot_num in range(1, max_planets - len(owned_normal) + 1):
            roster_planets.append(_empty_colony_slot(slot_num, empty_fields, empty_temp))
    if not roster_planets:
        raise ValueError("No normal planets available in GameState (enable pad_generic_colonies for preview)")

    state_res = state.researches or {}
    uni_src = universe_research_max or {}
    own_researches = {key: max(0, coerce_int(state_res.get(key, 0), 0)) for key, _ in research_catalog()}
    uni_max = {key: max(own_researches[key], max(0, coerce_int(uni_src.get(key, 0), 0))) for key in own_researches}
    min_fleet = as_int_dict(cfg["min_fleet_per_planet"])
    opt_max = as_int_dict(cfg["optimized_building_max_levels"])
    misc_lvls = as_int_dict(cfg["misc_building_levels"])
    required_research = research_requirements(
        [
            map_str(cfg, "target_ship_key", "s16"),
            *(ship for ship, count in min_fleet.items() if count > 0),
            *opt_max,
            *(key for key, level in misc_lvls.items() if level > 0),
            *(key for levels in misc_by_slot_int.values() for key, level in levels.items() if level > 0),
        ]
    )
    planned_rows = plan_research_levels(
        uni_max, own_researches, required_research, map_float(cfg, "research_percent_of_universe_max", 100.0)
    )
    planned_levels = {map_str(row, "key", ""): map_int(row, "planned_level", 0) for row in planned_rows}
    return PreparedInputs(roster_planets, len(owned_normal), own_researches, uni_max, planned_rows, planned_levels)


def _build_production_model(
    state: OptimizerStateLike,
    cfg: Mapping[str, object],
    misc_by_slot_int: Mapping[int, Mapping[str, int]],
    prepared: PreparedInputs,
) -> tuple[float, list[dict[str, object]], EmpireProductionModel]:
    speedup = _resolve_speedup(state, cfg)
    acct_ctx: dict[str, object] = {
        "speedup_factor": speedup,
        "r1_mining_tech": prepared.planned_research_levels.get("r1", 0),
        "r2_energy_tech": prepared.planned_research_levels.get("r2", 0),
    }
    opt_max = as_int_dict(cfg["optimized_building_max_levels"])
    planet_plans = build_planet_plans(
        prepared.roster_planets,
        owned_count=prepared.owned_count,
        optimized_max_levels=opt_max,
        misc_levels=as_int_dict(cfg["misc_building_levels"]),
        misc_levels_by_slot=misc_by_slot_int,
        min_fleet=as_int_dict(cfg["min_fleet_per_planet"]),
        central_planet_slot=map_int(cfg, "central_planet_slot", 1),
        central_min_warehouse_level=map_int(cfg, "central_planet_min_warehouse_level", 15),
        research_costs=research_upgrade_costs(prepared.own_researches, prepared.planned_research_levels),
        reserved_floors_by_planet=_extract_reserved_floors(cfg.get("reserved_floors_by_planet")),
        min_warehouse_level=map_int(cfg, "min_warehouse_level", 0),
        planet_specializations=as_str_dict(cfg["planet_specializations"]),
        allow_demolition=bool(cfg["allow_demolition"]),
        planet_names=as_str_dict(cfg["planet_names"]),
    )
    _apply_manual_planet_pins(planet_plans, as_mapping(cfg.get("manual_planet_configs")), opt_max)
    planet_contexts: list[dict[str, object]] = [
        {
            **acct_ctx,
            "slot_index": plan["slot_index"],
            "planet_id": plan["planet_id"],
            "coords": plan["coords"],
            "planet_status": plan["planet_status"],
            "planet_temperature_c": plan["temperature_c"],
        }
        for plan in planet_plans
    ]
    model = EmpireProductionModel(
        planet_plans,
        planet_contexts,
        optimized_keys=tuple(opt_max),
        target_ship_key=map_str(cfg, "target_ship_key", "s16"),
        month_days=map_float(cfg, "production_month_days", 30.0),
        warehouse_hours=map_float(cfg, "warehouse_hours_of_production", 24.0),
    )
    return speedup, planet_plans, model


def _should_choose_sticky(
    *,
    fresh_ships: float,
    fresh_changes: int,
    fresh_cost: float,
    fresh_levels: IntArray,
    sticky_ships: float,
    sticky_changes: int,
    sticky_cost: float,
    sticky_levels: IntArray,
    threshold_pct: float,
) -> bool:
    if sticky_ships <= 0.0:
        return False
    threshold = max(0.0, threshold_pct) / 100.0
    rel_gain = (fresh_ships - sticky_ships) / sticky_ships
    if fresh_changes > sticky_changes:
        return rel_gain <= threshold
    if fresh_changes == sticky_changes:
        return (sticky_ships, -sticky_cost, -int(sticky_levels.sum())) > (
            fresh_ships,
            -fresh_cost,
            -int(fresh_levels.sum()),
        )
    return False


def _select_stable_configuration(
    best_model: EmpireProductionModel,
    best_levels: IntArray,
    planet_plans: Sequence[Mapping[str, object]],
    cfg: Mapping[str, object],
    previous_result: Mapping[str, object] | None,
) -> StabilitySelection:
    prev_targets, prev_roles = _extract_previous_planet_state(planet_plans, best_model.optimized_keys, previous_result)
    has_prev_roles = any(role in SPECIALIZATION_ALLOWED_BUILDINGS for role in prev_roles)
    has_built = any(any(lvl > 0 for lvl in as_int_dict(plan["built_levels"]).values()) for plan in planet_plans)
    cost_cache: dict[tuple[str, int, int], float] = {}
    step_sizes = _extract_step_sizes(cfg.get("hill_climb_step_sizes"))

    fresh_levels = (
        _align_unlocked_planet_roles_for_sunk_cost(
            best_model, best_levels, planet_plans, prev_roles, cost_cache, step_sizes=step_sizes
        )
        if (has_prev_roles or has_built)
        else best_levels
    )
    fresh_ships, fresh_changes, fresh_cost, _, _ = _evaluate_stability_metrics(
        best_model, fresh_levels, planet_plans, prev_roles, cost_cache
    )
    chosen_model, chosen_levels = best_model, fresh_levels
    sticky_plan_applied = False
    sticky_ships_out: float | None = None

    if bool(cfg["preserve_previous_specializations"]) and has_prev_roles:
        sticky_model, sticky_levels, sticky_ships_raw = _build_sticky_candidate(
            best_model, fresh_levels, planet_plans, prev_targets, prev_roles, step_sizes
        )
        if sticky_model is not None and sticky_levels is not None and sticky_ships_raw is not None:
            s_ships, s_changes, s_cost, _, _ = _evaluate_stability_metrics(
                sticky_model, sticky_levels, planet_plans, prev_roles, cost_cache
            )
            sticky_ships_out = s_ships
            if _should_choose_sticky(
                fresh_ships=fresh_ships,
                fresh_changes=fresh_changes,
                fresh_cost=fresh_cost,
                fresh_levels=fresh_levels,
                sticky_ships=s_ships,
                sticky_changes=s_changes,
                sticky_cost=s_cost,
                sticky_levels=sticky_levels,
                threshold_pct=map_float(cfg, "specialization_switch_threshold_pct", 0.5),
            ):
                chosen_model, chosen_levels, sticky_plan_applied = sticky_model, sticky_levels, True

    return StabilitySelection(
        chosen_model, chosen_levels, prev_roles, fresh_ships, sticky_ships_out, sticky_plan_applied
    )


def run_config_optimizer(
    state: OptimizerStateLike,
    *,
    universe_research_max: Mapping[str, int] | None = None,
    params: Mapping[str, object] | None = None,
    planet_names: Mapping[str, str] | None = None,
    previous_result: Mapping[str, object] | None = None,
    progress_cb: Callable[[str, float], None] | None = None,
) -> dict[str, object]:
    """Optimize target building levels per planet to maximize monthly production of ``target_ship_key``."""
    t0 = time.perf_counter()
    if progress_cb is not None:
        progress_cb("preparing", 0.02)

    cfg, misc_by_slot_int = _prepare_normalized_config(params, planet_names)
    prepared = _build_roster_and_research(state, cfg, misc_by_slot_int, universe_research_max)
    speedup, planet_plans, production_model = _build_production_model(state, cfg, misc_by_slot_int, prepared)

    if progress_cb is not None:
        progress_cb("searching", 0.08)

    best_model, reserve_rounds, catalogues, _gd, ga_history, ga_levels, best_levels = search_in_rounds(
        production_model,
        progress_cb=progress_cb,
        grid_size=map_int(cfg, "catalogue_grid_size", 25000),
        priority_steps=map_int(cfg, "catalogue_priority_steps", 4),
        step_sizes=_extract_step_sizes(cfg.get("hill_climb_step_sizes")),
        population_size=map_int(cfg, "ga_population_size", 200),
        generations=map_int(cfg, "ga_generations", 150),
        tournament_size=map_int(cfg, "ga_tournament_size", 3),
        crossover_rate=map_float(cfg, "ga_crossover_rate", 0.9),
        mutation_rate=map_float(cfg, "ga_mutation_rate", 0.1),
        elite_count=map_int(cfg, "ga_elite_count", 4),
        seed=map_int(cfg, "ga_random_seed", 20261117),
    )

    if progress_cb is not None:
        progress_cb("finalizing", 0.96)

    selection = _select_stable_configuration(best_model, best_levels, planet_plans, cfg, previous_result)
    result = assemble_optimizer_result(
        ReportInputs(
            t0=t0,
            cfg=cfg,
            speedup_factor=speedup,
            prepared=prepared,
            planet_plans=planet_plans,
            selection=selection,
            reserve_rounds=reserve_rounds,
            catalogues=catalogues,
            ga_history=ga_history,
            ga_levels=ga_levels,
        )
    )
    if progress_cb is not None:
        progress_cb("completed", 1.0)
    return result


__all__ = ["OptimizerStateLike", "run_config_optimizer"]
