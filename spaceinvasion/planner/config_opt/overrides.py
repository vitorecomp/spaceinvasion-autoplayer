"""Manual planet configuration overrides and exact empire re-evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import time

import numpy as np

from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.planner.config_opt._overrides_helpers import (
    OptimizerStateLike,
    StateInput,
    _evaluate_planet_map,
    _evaluate_planet_typed,
    _extract_planned_building_map_from_entry,
    _OverrideSetup,
    _prepare_override_setup,
)
from spaceinvasion.planner.config_opt._overrides_report import (
    _assemble_override_result,
    _build_limits_and_bottlenecks,
    _build_single_planet_record,
    _EmpireReportCtx,
    _EmpireTotals,
    _extract_baseline_maps,
    _ShipEvalSpec,
)
from spaceinvasion.planner.config_opt._params_norm import (
    normalize_planet_name,
    normalize_planet_specialization,
    normalize_planet_target_buildings,
)
from spaceinvasion.planner.config_opt._protocols import as_int_dict, as_mapping, as_str_dict, map_float, map_int
from spaceinvasion.planner.config_opt.catalog_math import catalog_top_level, full_cost, ship_info
from spaceinvasion.planner.config_opt.constants import RESOURCE_KEYS, FloatArray
from spaceinvasion.planner.config_opt.rows import _warehouse_requirement_rows
from spaceinvasion.planner.config_opt.tech_graph import same_planet_building_requirements


def _locate_target_planet_idx(
    planet_plans: Sequence[Mapping[str, object]],
    *,
    planet_id: int | None,
    slot: int | None,
    coords: str | None,
) -> int:
    """Find the target planet index by ``planet_id``, ``slot``, or ``coords``."""
    target_idx: int | None = None
    norm_req_coords = (
        normalize_coords(coords) if (coords and not str(coords).startswith("EMPTY:")) else str(coords or "").strip()
    )
    if planet_id is not None and int(planet_id) != 0:
        target_idx = next(
            (i for i, pl in enumerate(planet_plans) if map_int(pl, "planet_id", 0) == int(planet_id)), None
        )
    if target_idx is None and slot is not None and int(slot) > 0:
        target_idx = next((i for i, pl in enumerate(planet_plans) if map_int(pl, "slot_index", 0) == int(slot)), None)
    if target_idx is None and norm_req_coords:
        target_idx = next(
            (
                i
                for i, pl in enumerate(planet_plans)
                if str(pl["coords"]) == norm_req_coords
                or (
                    not str(pl["coords"]).startswith("EMPTY:")
                    and normalize_coords(str(pl["coords"])) == norm_req_coords
                )
            ),
            None,
        )
    if target_idx is None:
        raise ValueError(
            f"Target planet (planet_id={planet_id}, slot={slot}, coords={coords!r}) not found in normal planet roster"
        )
    return target_idx


def _planet_alias_keys(plan: Mapping[str, object]) -> tuple[str, ...]:
    coords = str(plan["coords"])
    norm_c = normalize_coords(coords) if not coords.startswith("EMPTY:") else ""
    return (str(plan["planet_id"]), coords, norm_c, f"slot:{map_int(plan, 'slot_index', 0)}")


def _planet_ident_key(plan: Mapping[str, object]) -> str:
    pid = map_int(plan, "planet_id", 0)
    return str(pid) if pid > 0 else f"slot:{map_int(plan, 'slot_index', 0)}"


def _build_ship_eval_spec(cfg: Mapping[str, object]) -> _ShipEvalSpec:
    target_ship_key = str(cfg["target_ship_key"])
    s_info = ship_info(target_ship_key)
    ship_base_cost = full_cost(s_info.get("base_cost"))
    cost_resources = tuple(r for r in RESOURCE_KEYS if float(ship_base_cost[r]) > 0.0)
    return _ShipEvalSpec(
        target_ship_key=target_ship_key,
        ship_name=str(s_info["name"]),
        ship_base_cost=ship_base_cost,
        cost_resources=cost_resources,
        ship_cost_vec=np.array([float(ship_base_cost[r]) for r in cost_resources], dtype=np.float64),
        hours_per_month=24.0 * map_float(cfg, "production_month_days", 30.0),
        base_ship_time_s=map_float(s_info, "base_build_time_s", 1.0),
        target_reqs=same_planet_building_requirements(target_ship_key),
        warehouse_hours=map_float(cfg, "warehouse_hours_of_production", 24.0),
    )


def _apply_target_metadata_edits(
    tplan: dict[str, object],
    cfg: dict[str, object],
    *,
    fields_max_override: int | None,
    locked_specialization: str | None,
    planet_name: str | None,
) -> str:
    """Apply optional fields cap, specialization lock, and display-name overrides on ``tplan``."""
    if fields_max_override is not None and int(fields_max_override) > 0:
        tplan["fields_max"] = max(1, min(2000, int(fields_max_override)))
    ident_key = _planet_ident_key(tplan)

    if locked_specialization is not None:
        norm_lock = normalize_planet_specialization(locked_specialization)
        tplan["locked_specialization"] = norm_lock
        specs_map = as_str_dict(cfg.get("planet_specializations"))
        for alias_k in _planet_alias_keys(tplan):
            if alias_k:
                specs_map.pop(alias_k, None)
        if norm_lock != "auto":
            specs_map[ident_key] = norm_lock
        cfg["planet_specializations"] = specs_map

    if planet_name is not None:
        clean_nm = normalize_planet_name(planet_name)
        tplan["name"] = clean_nm or str(tplan.get("game_name") or tplan["coords"])
        names_map = as_str_dict(cfg.get("planet_names"))
        if clean_nm:
            names_map[ident_key] = clean_nm
        else:
            names_map.pop(ident_key, None)
        cfg["planet_names"] = names_map

    return ident_key


def _apply_target_building_override(
    tplan: dict[str, object],
    tcontext: Mapping[str, object],
    baseline_map: Mapping[str, int],
    target_buildings: Mapping[str, object],
    ship_spec: _ShipEvalSpec,
    *,
    auto_warehouses: bool,
    allow_over_fields: bool,
) -> dict[str, int]:
    """Merge edited target levels, raise omitted prereqs, optionally auto-size warehouses, and validate fields."""
    normalized_override = normalize_planet_target_buildings(target_buildings)
    explicit_keys = {str(raw_bk).strip().lower() for raw_bk in target_buildings}
    merged_map = dict(baseline_map)
    for k in explicit_keys:
        if k in normalized_override:
            merged_map[k] = normalized_override[k]

    for bk_chk, lvl_chk in list(merged_map.items()):
        if lvl_chk > 0:
            for req_bk, req_lvl in same_planet_building_requirements(bk_chk, lvl_chk).items():
                if req_bk != bk_chk and req_bk not in explicit_keys:
                    merged_map[req_bk] = max(int(merged_map.get(req_bk, 0)), int(req_lvl))

    if auto_warehouses:
        tmp_eval = _evaluate_planet_typed(
            merged_map,
            tplan,
            tcontext,
            target_requirements=ship_spec.target_reqs,
            cost_resources=ship_spec.cost_resources,
            ship_cost=ship_spec.ship_cost_vec,
            hours_per_month=ship_spec.hours_per_month,
            base_ship_time_s=ship_spec.base_ship_time_s,
        )
        wh_rows_tmp = _warehouse_requirement_rows(
            tplan,
            merged_map,
            tmp_eval.net_per_hour,
            warehouse_hours=ship_spec.warehouse_hours,
            ship_built=ship_spec.target_ship_key if tmp_eval.shipyard_ships > 0.0 else None,
            central_reserve=np.zeros(len(RESOURCE_KEYS), dtype=np.float64),
        )
        built_lvls = as_int_dict(tplan["built_levels"])
        for wrow in wh_rows_tmp:
            wk = str(wrow["key"])
            req_lvl = max(
                map_int(wrow, "level_for_largest_cost", 0),
                map_int(wrow, "level_for_own_production", 0),
                map_int(wrow, "minimum_level", 0),
            )
            merged_map[wk] = min(catalog_top_level(wk), max(built_lvls.get(wk, 0), req_lvl))

    fields_used_edit = int(sum(merged_map.values()))
    fields_max_val = map_int(tplan, "fields_max", 0)
    if not allow_over_fields and fields_used_edit > fields_max_val:
        over = fields_used_edit - fields_max_val
        raise ValueError(
            f"Target configuration on {tplan['name']} [{tplan['coords']}] uses "
            f"{fields_used_edit}/{fields_max_val} fields ({over} over planet capacity)."
        )
    return merged_map


def _update_manual_pins(
    cfg: dict[str, object],
    tplan: Mapping[str, object],
    ident_key: str,
    merged_map: Mapping[str, int],
    *,
    fields_max_override: int | None,
    auto_warehouses: bool,
    lock_for_ga: bool,
) -> dict[str, object]:
    """Update ``cfg['manual_planet_configs']`` pin ledger for the edited planet."""
    manual_pins: dict[str, object] = dict(as_mapping(cfg.get("manual_planet_configs")))
    for alias_k in _planet_alias_keys(tplan):
        if alias_k:
            manual_pins.pop(alias_k, None)
    if lock_for_ga:
        manual_pins[ident_key] = {
            "target_buildings": dict(merged_map),
            "fields_max_override": (
                int(fields_max_override) if (fields_max_override is not None and int(fields_max_override) > 0) else None
            ),
            "auto_warehouses": bool(auto_warehouses),
        }
    cfg["manual_planet_configs"] = manual_pins
    return manual_pins


def _evaluate_override_empire(
    setup: _OverrideSetup,
    planned_maps: Sequence[Mapping[str, int]],
    planet_contexts: Sequence[Mapping[str, object]],
    prev_specs: Sequence[str | None],
    ship_spec: _ShipEvalSpec,
    manual_pins: Mapping[str, object],
    central_planet_idx: int,
) -> tuple[_EmpireReportCtx, list[dict[str, object]], list[dict[str, object]], _EmpireTotals, str]:
    """Evaluate all planets after override and compute report ctx, planet records, limits, totals, and bottleneck."""
    evals = [
        _evaluate_planet_typed(
            planned_maps[idx],
            setup.planet_plans[idx],
            planet_contexts[idx],
            target_requirements=ship_spec.target_reqs,
            cost_resources=ship_spec.cost_resources,
            ship_cost=ship_spec.ship_cost_vec,
            hours_per_month=ship_spec.hours_per_month,
            base_ship_time_s=ship_spec.base_ship_time_s,
        )
        for idx in range(len(setup.planet_plans))
    ]
    limit_names = (*ship_spec.cost_resources, "shipyards")
    limit_totals: FloatArray = np.stack([ev.planet_limits for ev in evals], axis=0).sum(axis=0)
    planet_net_mat: FloatArray = np.stack([ev.net_per_hour for ev in evals], axis=0)
    ships = max(0.0, float(limit_totals.min())) if limit_totals.size else 0.0
    shipyard_total = float(limit_totals[limit_names.index("shipyards")])
    central_reserve: FloatArray = ship_spec.warehouse_hours * np.maximum(0.0, planet_net_mat.sum(axis=0))

    ctx = _EmpireReportCtx(
        ships=ships,
        shipyard_total=shipyard_total,
        central_planet_idx=central_planet_idx,
        central_reserve=central_reserve,
        ship_spec=ship_spec,
        speedup_factor=setup.speedup_factor,
        manual_pins=manual_pins,
    )
    totals = _EmpireTotals()
    planet_records = [
        _build_single_planet_record(idx, plan, planned_maps[idx], evals[idx], prev_specs[idx], ctx, totals)
        for idx, plan in enumerate(setup.planet_plans)
    ]
    limits_records, limiting_factor = _build_limits_and_bottlenecks(
        limit_names, ship_spec.ship_cost_vec, limit_totals, ships
    )
    return ctx, planet_records, limits_records, totals, limiting_factor


def apply_manual_planet_override(
    state: StateInput,
    *,
    target_buildings: Mapping[str, object],
    base_result: Mapping[str, object] | None = None,
    slot: int | None = None,
    planet_id: int | None = None,
    coords: str | None = None,
    fields_max_override: int | None = None,
    locked_specialization: str | None = None,
    planet_name: str | None = None,
    lock_for_ga: bool = True,
    auto_warehouses: bool = False,
    allow_over_fields: bool = False,
    universe_research_max: Mapping[str, int] | None = None,
    params: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Apply an operator's custom target building configuration for one planet and recompute empire metrics."""
    t0 = time.perf_counter()
    setup = _prepare_override_setup(
        state, base_result=base_result, universe_research_max=universe_research_max, params=params
    )
    target_idx = _locate_target_planet_idx(setup.planet_plans, planet_id=planet_id, slot=slot, coords=coords)
    planned_maps, prev_specs = _extract_baseline_maps(setup.planet_plans, base_result)
    tplan = setup.planet_plans[target_idx]
    ident_key = _apply_target_metadata_edits(
        tplan,
        setup.cfg,
        fields_max_override=fields_max_override,
        locked_specialization=locked_specialization,
        planet_name=planet_name,
    )
    ship_spec = _build_ship_eval_spec(setup.cfg)
    central_planet_idx = next((i for i, pl in enumerate(setup.planet_plans) if bool(pl["is_central_planet"])), -1)
    planet_contexts: list[dict[str, object]] = [
        {
            **setup.plan_account_context,
            "slot_index": pl["slot_index"],
            "planet_id": pl["planet_id"],
            "coords": pl["coords"],
            "planet_status": pl["planet_status"],
            "planet_temperature_c": pl["temperature_c"],
        }
        for pl in setup.planet_plans
    ]
    planned_maps[target_idx] = _apply_target_building_override(
        tplan,
        planet_contexts[target_idx],
        planned_maps[target_idx],
        target_buildings,
        ship_spec,
        auto_warehouses=auto_warehouses,
        allow_over_fields=allow_over_fields,
    )
    manual_pins = _update_manual_pins(
        setup.cfg,
        tplan,
        ident_key,
        planned_maps[target_idx],
        fields_max_override=fields_max_override,
        auto_warehouses=auto_warehouses,
        lock_for_ga=lock_for_ga,
    )
    ctx, planet_records, limits_records, totals, limiting_factor = _evaluate_override_empire(
        setup, planned_maps, planet_contexts, prev_specs, ship_spec, manual_pins, central_planet_idx
    )
    return _assemble_override_result(
        setup,
        ctx,
        planet_records,
        limits_records,
        totals,
        limiting_factor=limiting_factor,
        override_slot=map_int(tplan, "slot_index", 0),
        base_result=base_result,
        elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 2),
    )


__all__ = [
    "OptimizerStateLike",
    "_evaluate_planet_map",
    "_extract_planned_building_map_from_entry",
    "apply_manual_planet_override",
]
