"""Result report builders for ``run_config_optimizer``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import datetime
import math
import time
from typing import NamedTuple

import numpy as np

from backend.core import catalog as game_data
from backend.planner.config_optimizer._protocols import PlanetLike, as_int_dict, map_float, map_int, map_str
from backend.planner.config_optimizer._search_reserve import GAHistoryRow, RoundRow
from backend.planner.config_optimizer.catalog_math import full_cost, ship_info, spice_eq
from backend.planner.config_optimizer.constants import (
    RESOURCE_KEYS,
    SPECIALIZATION_ALLOWED_BUILDINGS,
    WAREHOUSE_KEYS,
    FloatArray,
    IntArray,
)
from backend.planner.config_optimizer.model import EmpireProductionModel
from backend.planner.config_optimizer.params import _json_ready, normalize_planet_specialization
from backend.planner.config_optimizer.rows import (
    _planet_building_rows,
    _warehouse_requirement_rows,
    detect_planet_specialization,
)
from backend.planner.config_optimizer.tech_graph import (
    building_upgrade_cost_spice_eq,
    research_upgrade_cost_spice_eq,
)


@dataclass(frozen=True)
class PreparedInputs:
    roster_planets: list[PlanetLike]
    owned_count: int
    own_researches: dict[str, int]
    uni_max: dict[str, int]
    planned_research_rows: list[dict[str, object]]
    planned_research_levels: dict[str, int]


@dataclass(frozen=True)
class StabilitySelection:
    model: EmpireProductionModel
    levels: IntArray
    prev_roles: list[str | None]
    fresh_ships: float
    sticky_ships_out: float | None
    sticky_plan_applied: bool


@dataclass(frozen=True)
class ReportInputs:
    t0: float
    cfg: Mapping[str, object]
    speedup_factor: float
    prepared: PreparedInputs
    planet_plans: Sequence[Mapping[str, object]]
    selection: StabilitySelection
    reserve_rounds: Sequence[RoundRow]
    catalogues: Sequence[IntArray]
    ga_history: Sequence[GAHistoryRow]
    ga_levels: IntArray


@dataclass
class _PlanetTotals:
    fields_used: int = 0
    fields_free: int = 0
    warehouse_fields: int = 0
    levels_to_build: int = 0
    levels_to_demolish: int = 0
    demolitions_when_full: int = 0
    buildings_cost: float = 0.0
    fleet_cost: float = 0.0
    pig_iron_hr: float = 0.0
    locked_planets_count: int = 0
    roles_preserved_count: int = 0
    roles_changed_count: int = 0


class _SingleOutcome(NamedTuple):
    limits: FloatArray
    net: FloatArray
    energy_prod: FloatArray
    energy_used: FloatArray
    power_ratio: FloatArray
    smelting_ratio: FloatArray
    warehouses: IntArray
    ships: float
    shipyard_col: int
    shipyard_total: float


class _PlanetComputed(NamedTuple):
    built: dict[str, int]
    planned_map: dict[str, int]
    fields_max: int
    fields_used_target: int
    shipyard_ships: float
    locked_spec: str
    detected_spec: str
    spec_changed: bool
    production: dict[str, object]


def _compute_planet_metrics(
    index: int, plan: Mapping[str, object], inp: ReportInputs, out: _SingleOutcome, totals: _PlanetTotals
) -> _PlanetComputed:
    sel = inp.selection
    built = as_int_dict(plan["built_levels"])
    wh_map = dict(zip(WAREHOUSE_KEYS, (int(lvl) for lvl in out.warehouses[index]), strict=True))
    planned_map = {
        **as_int_dict(plan["fixed_levels"]),
        **wh_map,
        **dict(zip(sel.model.optimized_keys, (int(lvl) for lvl in sel.levels[index]), strict=True)),
    }
    fields_max = map_int(plan, "fields_max", 0)
    fields_used = int(sum(planned_map.values()))
    shipyard_ships = float(out.limits[index, out.shipyard_col])
    locked_spec = normalize_planet_specialization(plan.get("locked_specialization"))
    totals.locked_planets_count += int(locked_spec != "auto")
    detected_spec = detect_planet_specialization(
        planned_map,
        min_levels=as_int_dict(plan["min_levels"]),
        built_levels=built,
        shipyard_ships=shipyard_ships,
        locked_specialization=locked_spec,
    )
    prev_spec = sel.prev_roles[index]
    spec_changed = bool(prev_spec in SPECIALIZATION_ALLOWED_BUILDINGS and detected_spec != prev_spec)
    if prev_spec in SPECIALIZATION_ALLOWED_BUILDINGS:
        totals.roles_changed_count += int(spec_changed)
        totals.roles_preserved_count += int(not spec_changed)

    wh_fields = int(sum(wh_map.values()))
    lvls_build = int(sum(max(0, planned_map[k] - built[k]) for k in planned_map))
    lvls_demolish = int(sum(max(0, built[k] - planned_map[k]) for k in planned_map))
    demolish_full = min(lvls_demolish, max(0, int(sum(built.values())) + lvls_build - fields_max))
    up_cost = round(sum(building_upgrade_cost_spice_eq(k, built[k], planned_map[k]) for k in planned_map), 2)
    fl_cost = round(map_float(plan, "fleet_cost_spice_eq", 0.0), 2)

    totals.fields_used += fields_used
    totals.fields_free += fields_max - fields_used
    totals.warehouse_fields += wh_fields
    totals.levels_to_build += lvls_build
    totals.levels_to_demolish += lvls_demolish
    totals.demolitions_when_full += demolish_full
    totals.buildings_cost += up_cost
    totals.fleet_cost += fl_cost
    totals.pig_iron_hr += round(float(out.net[index, 0]), 1)

    production: dict[str, object] = {
        "energy_produced": round(float(out.energy_prod[index]), 1),
        "energy_used": round(float(out.energy_used[index]), 1),
        "power_ratio": round(float(out.power_ratio[index]), 4),
        "smelting_ratio": round(float(out.smelting_ratio[index]), 4),
        **{f"{res}_per_hour": round(float(out.net[index, col]), 1) for col, res in enumerate(RESOURCE_KEYS)},
        "shipyard_ships_per_month": round(shipyard_ships, 2),
        "ships_built_per_month": (
            round(shipyard_ships * out.ships / out.shipyard_total, 2) if out.shipyard_total > 0.0 else 0.0
        ),
        "warehouse_fields": wh_fields,
        "levels_to_build": lvls_build,
        "levels_to_demolish": lvls_demolish,
        "demolitions_when_full": demolish_full,
        "upgrade_cost_spice_eq": up_cost,
        "fleet_cost_spice_eq": fl_cost,
    }
    return _PlanetComputed(
        built,
        planned_map,
        fields_max,
        fields_used,
        shipyard_ships,
        locked_spec,
        detected_spec,
        spec_changed,
        production,
    )


def _build_single_planet_record(
    index: int, plan: Mapping[str, object], inp: ReportInputs, out: _SingleOutcome, totals: _PlanetTotals
) -> dict[str, object]:
    m = _compute_planet_metrics(index, plan, inp, out, totals)
    bm = inp.selection.model
    slot_idx = map_int(plan, "slot_index", 0)
    temp_c = map_int(plan, "temperature_c", 0)
    pname = map_str(plan, "name", "")
    return {
        "slot": slot_idx,
        "slot_index": slot_idx,
        "planet_id": map_int(plan, "planet_id", 0),
        "coords": map_str(plan, "coords", ""),
        "name": pname,
        "game_name": str(plan.get("game_name") or pname),
        "is_placeholder": bool(plan["is_placeholder"]),
        "planet_status": map_str(plan, "planet_status", ""),
        "is_central_planet": bool(plan["is_central_planet"]),
        "pays_for_research": bool(plan["pays_for_research"]),
        "temperature": temp_c,
        "temperature_c": temp_c,
        "fields_max": m.fields_max,
        "fields_used_current": map_int(plan, "fields_used_current", 0),
        "fields_used_target": m.fields_used_target,
        "fields_used": m.fields_used_target,
        "fields_free": m.fields_max - m.fields_used_target,
        "built_floors": {f"b{int(bid)}": int(m.built[f"b{int(bid)}"]) for bid in sorted(game_data.BUILDING_BY_ID)},
        "reserved_floors": as_int_dict(plan["reserved_floors"]),
        "detected_specialization": m.detected_spec,
        "locked_specialization": m.locked_spec,
        "previous_specialization": inp.selection.prev_roles[index],
        "specialization_changed": m.spec_changed,
        "production": m.production,
        "warehouses": _warehouse_requirement_rows(
            plan,
            m.planned_map,
            out.net[index],
            warehouse_hours=bm.warehouse_hours,
            ship_built=bm.target_ship_key if m.shipyard_ships > 0.0 else None,
            central_reserve=(
                bm.central_reserve if index == bm.central_planet else np.zeros(len(RESOURCE_KEYS), dtype=np.float64)
            ),
        ),
        "buildings": _planet_building_rows(plan, m.planned_map, inp.speedup_factor),
    }


def _build_limits_records(
    best_model: EmpireProductionModel, limit_totals: FloatArray, ships: float
) -> tuple[list[dict[str, object]], str]:
    needed_per_ship: list[float] = [float(v) for v in best_model.ship_cost.tolist()] + [1.0]
    min_limit = float(limit_totals.min())
    records: list[dict[str, object]] = [
        {
            "limit": str(name),
            "needed_per_ship": float(needed),
            "ships_per_month": round(float(raw_total), 2),
            "available_per_month": round(float(raw_total) * float(needed), 1),
            "used_percent": round(100.0 * ships / float(raw_total), 2) if float(raw_total) > 0.0 else 0.0,
            "is_bottleneck": float(raw_total) == min_limit,
        }
        for name, needed, raw_total in zip(best_model.limit_names, needed_per_ship, limit_totals.tolist(), strict=True)
    ]
    return records, ", ".join(str(r["limit"]) for r in records if r["is_bottleneck"])


def _build_search_summary(inp: ReportInputs, ships: float, totals: _PlanetTotals) -> dict[str, object]:
    sel = inp.selection
    final_best_hist = float(inp.ga_history[-1]["best_ships_per_month"]) if inp.ga_history else 0.0
    first_best_gen = next(
        (int(row["generation"]) for row in inp.ga_history if float(row["best_ships_per_month"]) >= final_best_hist), 0
    )
    pop_size = map_int(inp.cfg, "ga_population_size", 200)
    generations = map_int(inp.cfg, "ga_generations", 150)
    return {
        "search_rounds": int(len(inp.reserve_rounds)),
        "rounds": list(inp.reserve_rounds),
        "designs_per_planet": [int(len(cat)) for cat in inp.catalogues],
        "possible_choices_log10": round(float(sum(math.log10(max(1, len(cat))) for cat in inp.catalogues)), 2),
        "chromosomes_evaluated": int(pop_size * (generations + 1) * len(inp.reserve_rounds)),
        "first_generation_with_the_final_best": first_best_gen,
        "ga_ships_per_month": round(float(sel.model.ships_per_month(inp.ga_levels[None])[0]), 2),
        "polished_ships_per_month": round(ships, 2),
        "fresh_ships_per_month": round(float(sel.fresh_ships), 2),
        "sticky_ships_per_month": round(float(sel.sticky_ships_out), 2) if sel.sticky_ships_out is not None else None,
        "sticky_plan_applied": bool(sel.sticky_plan_applied),
        "locked_planets_count": int(totals.locked_planets_count),
        "roles_preserved_count": int(totals.roles_preserved_count),
        "roles_changed_count": int(totals.roles_changed_count),
        "levels_changed_by_polish": int(np.abs(sel.levels - inp.ga_levels).sum()),
    }


def _build_result_summary(
    inp: ReportInputs,
    planet_records: Sequence[Mapping[str, object]],
    ships: float,
    limiting_factor: str,
    totals: _PlanetTotals,
    elapsed_ms: float,
) -> dict[str, object]:
    sel = inp.selection
    bm = sel.model
    s_info = ship_info(bm.target_ship_key)
    ship_cost_sp = spice_eq(full_cost(s_info.get("base_cost")))
    own_res = inp.prepared.own_researches
    research_cost_sp = float(
        sum(
            research_upgrade_cost_spice_eq(k, own_res.get(k, 0), lv)
            for k, lv in inp.prepared.planned_research_levels.items()
        )
    )
    investment = totals.buildings_cost + totals.fleet_cost + research_cost_sp
    monthly_output = ships * ship_cost_sp
    central_rec = planet_records[bm.central_planet] if 0 <= bm.central_planet < len(planet_records) else None
    return {
        "target_ship_key": bm.target_ship_key,
        "target_ship": str(s_info["name"]),
        "ships_per_month": round(ships, 2),
        "whole_ships_per_month": int(ships),
        "bottleneck": limiting_factor,
        "limiting_factor": limiting_factor,
        "hours_per_month": float(bm.hours_per_month),
        "planets": int(len(planet_records)),
        "central_planet": bm.planet_names[bm.central_planet] if bm.central_planet >= 0 else "",
        "central_planet_name": str(central_rec["name"]) if central_rec is not None else "",
        "central_planet_coords": str(central_rec["coords"]) if central_rec is not None else "",
        "fields_used": totals.fields_used,
        "fields_free": totals.fields_free,
        "warehouse_fields": totals.warehouse_fields,
        "pig_iron_left_over_per_month": round(totals.pig_iron_hr * bm.hours_per_month, 1),
        "levels_to_build": totals.levels_to_build,
        "levels_to_demolish": totals.levels_to_demolish,
        "demolitions_when_full": totals.demolitions_when_full,
        "allow_demolition": bool(inp.cfg["allow_demolition"]),
        "buildings_cost_spice_eq": round(totals.buildings_cost, 2),
        "fleet_cost_spice_eq": round(totals.fleet_cost, 2),
        "research_cost_spice_eq": round(research_cost_sp, 2),
        "investment_spice_eq": round(investment, 2),
        "ship_cost_spice_eq": round(ship_cost_sp, 2),
        "monthly_output_spice_eq": round(monthly_output, 2),
        "investment_in_months_of_output": round(investment / monthly_output, 2) if monthly_output > 0.0 else None,
        "sticky_plan_applied": bool(sel.sticky_plan_applied),
        "preserve_previous_specializations": bool(inp.cfg["preserve_previous_specializations"]),
        "specialization_switch_threshold_pct": map_float(inp.cfg, "specialization_switch_threshold_pct", 0.5),
        "locked_planets_count": int(totals.locked_planets_count),
        "roles_preserved_count": int(totals.roles_preserved_count),
        "roles_changed_count": int(totals.roles_changed_count),
        "fresh_ships_per_month": round(float(sel.fresh_ships), 2),
        "sticky_ships_per_month": round(float(sel.sticky_ships_out), 2) if sel.sticky_ships_out is not None else None,
        "elapsed_ms": elapsed_ms,
    }


def assemble_optimizer_result(inp: ReportInputs) -> dict[str, object]:
    """Assemble and JSON-normalize the complete Config Optimizer payload."""
    bm = inp.selection.model
    sim = bm.simulate(inp.selection.levels[None])
    limits = np.asarray(sim["planet_limits"], dtype=np.float64)[0]
    limit_totals: FloatArray = np.asarray(limits.sum(axis=0), dtype=np.float64)
    ships = float(limit_totals.min())
    shipyard_col = bm.limit_names.index("shipyards")
    out = _SingleOutcome(
        limits,
        np.asarray(sim["planet_net_per_hour"], dtype=np.float64)[0],
        np.asarray(sim["planet_energy_produced"], dtype=np.float64)[0],
        np.asarray(sim["planet_energy_used"], dtype=np.float64)[0],
        np.asarray(sim["planet_power_ratio"], dtype=np.float64)[0],
        np.asarray(sim["planet_smelting_ratio"], dtype=np.float64)[0],
        np.asarray(sim["planet_warehouse_levels"], dtype=np.int64)[0],
        ships,
        shipyard_col,
        float(limit_totals[shipyard_col]),
    )
    totals = _PlanetTotals()
    planet_records = [
        _build_single_planet_record(idx, plan, inp, out, totals) for idx, plan in enumerate(inp.planet_plans)
    ]
    limits_records, limiting_factor = _build_limits_records(bm, limit_totals, ships)
    elapsed_ms = round((time.perf_counter() - inp.t0) * 1000.0, 2)
    raw_result: dict[str, object] = {
        "description": (
            "Pure-Python Config Optimizer result (ported from notebooks/config_optimizer.ipynb "
            "with hard built-level and reserved-floor preservation)."
        ),
        "schema_version": 1,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "meta_parameters": dict(inp.cfg),
        "inputs": {
            "universe_speedup_factor": inp.speedup_factor,
            "owned_planets": inp.prepared.owned_count,
            "roster_planets": len(planet_records),
            "pad_generic_colonies": bool(inp.cfg["pad_generic_colonies"]),
            "own_research_levels": dict(inp.prepared.own_researches),
            "universe_research_max_levels": dict(inp.prepared.uni_max),
        },
        "planned_research_levels": dict(inp.prepared.planned_research_levels),
        "planned_research_rows": list(inp.prepared.planned_research_rows),
        "planet_names": {
            str(rec["planet_id"]): str(rec["name"]) for rec in planet_records if map_int(rec, "planet_id", 0) > 0
        },
        "summary": _build_result_summary(inp, planet_records, ships, limiting_factor, totals, elapsed_ms),
        "search": _build_search_summary(inp, ships, totals),
        "limits": limits_records,
        "planets": planet_records,
        "elapsed_ms": elapsed_ms,
    }
    ready = _json_ready(raw_result)
    return {str(k): v for k, v in ready.items()} if isinstance(ready, Mapping) else raw_result


__all__ = ["PreparedInputs", "ReportInputs", "StabilitySelection", "assemble_optimizer_result"]
