"""Baseline lookup, planet record builders, and summary assembly for manual overrides."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import datetime

import numpy as np

from spaceinvasion.core import game_data
from spaceinvasion.core.coercion import coerce_int
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.planner.config_optimizer._overrides_helpers import (
    PlanetEvalResult,
    _extract_planned_building_map_from_entry,
    _OverrideSetup,
    json_ready_dict,
)
from spaceinvasion.planner.config_optimizer._params_norm import (
    _resolve_manual_planet_config,
    normalize_planet_specialization,
)
from spaceinvasion.planner.config_optimizer._protocols import as_int_dict, as_mapping, map_float, map_int
from spaceinvasion.planner.config_optimizer.catalog_math import spice_eq
from spaceinvasion.planner.config_optimizer.constants import (
    RESOURCE_KEYS,
    SPECIALIZATION_ALLOWED_BUILDINGS,
    WAREHOUSE_KEYS,
    FloatArray,
)
from spaceinvasion.planner.config_optimizer.rows import (
    _planet_building_rows,
    _warehouse_requirement_rows,
    detect_planet_specialization,
)
from spaceinvasion.planner.config_optimizer.tech_graph import (
    building_upgrade_cost_spice_eq,
    research_upgrade_cost_spice_eq,
)


@dataclass(frozen=True)
class _ShipEvalSpec:
    target_ship_key: str
    ship_name: str
    ship_base_cost: dict[str, int]
    cost_resources: tuple[str, ...]
    ship_cost_vec: FloatArray
    hours_per_month: float
    base_ship_time_s: float
    target_reqs: dict[str, int]
    warehouse_hours: float


@dataclass
class _EmpireTotals:
    fields_used: int = 0
    fields_free: int = 0
    warehouse_fields: int = 0
    levels_to_build: int = 0
    levels_to_demolish: int = 0
    demolitions_when_full: int = 0
    buildings_cost: float = 0.0
    fleet_cost: float = 0.0
    pig_iron_hr: float = 0.0
    locked_planets: int = 0
    roles_preserved: int = 0
    roles_changed: int = 0


@dataclass(frozen=True)
class _EmpireReportCtx:
    ships: float
    shipyard_total: float
    central_planet_idx: int
    central_reserve: FloatArray
    ship_spec: _ShipEvalSpec
    speedup_factor: float
    manual_pins: Mapping[str, object]


def _extract_baseline_maps(
    planet_plans: list[dict[str, object]],
    base_result: Mapping[str, object] | None,
) -> tuple[list[dict[str, int]], list[str | None]]:
    """Match baseline entries from ``base_result`` and extract baseline building maps and prior roles."""
    raw_base = base_result.get("planets") if isinstance(base_result, Mapping) else None
    base_by_pid: dict[int, Mapping[str, object]] = {}
    base_by_coords: dict[str, Mapping[str, object]] = {}
    base_by_slot: dict[int, Mapping[str, object]] = {}
    for entry in raw_base if isinstance(raw_base, (list, tuple)) else ():
        if not isinstance(entry, Mapping):
            continue
        if (epid := coerce_int(entry.get("planet_id"), 0)) > 0:
            base_by_pid[epid] = entry
        if ec := str(entry.get("coords") or "").strip():
            base_by_coords[ec] = entry
            if not ec.startswith("EMPTY:"):
                base_by_coords[normalize_coords(ec)] = entry
        if (eslot := coerce_int(entry.get("slot", entry.get("slot_index")), 0)) > 0:
            base_by_slot[eslot] = entry

    all_bkeys = tuple(f"b{int(bid)}" for bid in sorted(game_data.BUILDING_BY_ID))
    planned_maps: list[dict[str, int]] = []
    prev_specs: list[str | None] = []
    for plan in planet_plans:
        pid, pc = map_int(plan, "planet_id", 0), str(plan.get("coords") or "").strip()
        npc = normalize_coords(pc) if pc and not pc.startswith("EMPTY:") else pc
        matched = (
            base_by_pid.get(pid)
            if pid > 0 and pid in base_by_pid
            else base_by_coords.get(pc) or base_by_coords.get(npc) or base_by_slot.get(map_int(plan, "slot_index", 0))
        )
        lvl_maps = [
            as_int_dict(plan[k]) for k in ("built_levels", "fixed_levels", "min_levels", "warehouse_min_levels")
        ]
        planned_maps.append(
            _extract_planned_building_map_from_entry(
                matched, {bk: max(m.get(bk, 0) for m in lvl_maps) for bk in all_bkeys}
            )
        )
        if matched is not None:
            raw_fm = matched.get("fields_max")
            if isinstance(raw_fm, (int, float)) and not isinstance(raw_fm, bool):
                plan["fields_max"] = max(map_int(plan, "fields_max", 0), int(raw_fm))
        prev_sp = normalize_planet_specialization(matched.get("detected_specialization")) if matched else None
        prev_specs.append(prev_sp if prev_sp in SPECIALIZATION_ALLOWED_BUILDINGS else None)
    return planned_maps, prev_specs


def _compute_planet_production_info(
    plan: Mapping[str, object],
    pmap: Mapping[str, int],
    ev: PlanetEvalResult,
    built_lvls: Mapping[str, int],
    ctx: _EmpireReportCtx,
    totals: _EmpireTotals,
) -> dict[str, object]:
    """Compute one planet's production/cost metrics and update empire-wide totals."""
    fields_used_t, fields_max = int(sum(pmap.values())), map_int(plan, "fields_max", 0)
    warehouse_fields = int(sum(int(pmap.get(wk, 0)) for wk in WAREHOUSE_KEYS))
    levels_to_build = int(sum(max(0, int(pmap[bk]) - built_lvls[bk]) for bk in pmap))
    levels_to_demolish = int(sum(max(0, built_lvls[bk] - int(pmap[bk])) for bk in pmap))
    demolitions_when_full = min(
        levels_to_demolish, max(0, int(sum(built_lvls.values())) + levels_to_build - fields_max)
    )
    upgrade_cost_sp = round(sum(building_upgrade_cost_spice_eq(bk, built_lvls[bk], int(pmap[bk])) for bk in pmap), 2)
    fleet_cost_sp = round(map_float(plan, "fleet_cost_spice_eq", 0.0), 2)

    totals.fields_used += fields_used_t
    totals.fields_free += fields_max - fields_used_t
    totals.warehouse_fields += warehouse_fields
    totals.levels_to_build += levels_to_build
    totals.levels_to_demolish += levels_to_demolish
    totals.demolitions_when_full += demolitions_when_full
    totals.buildings_cost += upgrade_cost_sp
    totals.fleet_cost += fleet_cost_sp
    totals.pig_iron_hr += round(float(ev.net_per_hour[0]), 1)

    ship_share = round(ev.shipyard_ships * ctx.ships / ctx.shipyard_total, 2) if ctx.shipyard_total > 0.0 else 0.0
    return {
        "energy_produced": round(ev.energy_produced, 1),
        "energy_used": round(ev.energy_used, 1),
        "power_ratio": round(ev.power_ratio, 4),
        "smelting_ratio": round(ev.smelting_ratio, 4),
        **{f"{resource}_per_hour": round(float(ev.net_per_hour[col]), 1) for col, resource in enumerate(RESOURCE_KEYS)},
        "shipyard_ships_per_month": round(ev.shipyard_ships, 2),
        "ships_built_per_month": ship_share,
        "warehouse_fields": warehouse_fields,
        "levels_to_build": levels_to_build,
        "levels_to_demolish": levels_to_demolish,
        "demolitions_when_full": demolitions_when_full,
        "upgrade_cost_spice_eq": upgrade_cost_sp,
        "fleet_cost_spice_eq": fleet_cost_sp,
    }


def _build_single_planet_record(
    idx: int,
    plan: Mapping[str, object],
    pmap: Mapping[str, int],
    ev: PlanetEvalResult,
    prev_spec: str | None,
    ctx: _EmpireReportCtx,
    totals: _EmpireTotals,
) -> dict[str, object]:
    """Build one planet report dictionary and accumulate its empire totals."""
    fields_used_t = int(sum(pmap.values()))
    fields_max = map_int(plan, "fields_max", 0)
    built_lvls = as_int_dict(plan["built_levels"])
    locked_spec = normalize_planet_specialization(plan.get("locked_specialization"))
    if locked_spec != "auto":
        totals.locked_planets += 1
    detected_spec = detect_planet_specialization(
        pmap,
        min_levels=as_int_dict(plan["min_levels"]),
        built_levels=built_lvls,
        shipyard_ships=ev.shipyard_ships,
        locked_specialization=locked_spec,
    )
    spec_changed = bool(prev_spec in SPECIALIZATION_ALLOWED_BUILDINGS and detected_spec != prev_spec)
    if prev_spec in SPECIALIZATION_ALLOWED_BUILDINGS:
        if spec_changed:
            totals.roles_changed += 1
        else:
            totals.roles_preserved += 1

    production_info = _compute_planet_production_info(plan, pmap, ev, built_lvls, ctx, totals)
    central_res = (
        ctx.central_reserve if idx == ctx.central_planet_idx else np.zeros(len(RESOURCE_KEYS), dtype=np.float64)
    )
    warehouse_rows = _warehouse_requirement_rows(
        plan,
        pmap,
        ev.net_per_hour,
        warehouse_hours=ctx.ship_spec.warehouse_hours,
        ship_built=ctx.ship_spec.target_ship_key if ev.shipyard_ships > 0.0 else None,
        central_reserve=central_res,
    )
    return {
        "slot": map_int(plan, "slot_index", 0),
        "slot_index": map_int(plan, "slot_index", 0),
        "planet_id": map_int(plan, "planet_id", 0),
        "coords": str(plan["coords"]),
        "name": str(plan["name"]),
        "game_name": str(plan.get("game_name") or plan["name"]),
        "is_placeholder": bool(plan["is_placeholder"]),
        "planet_status": str(plan["planet_status"]),
        "is_central_planet": bool(plan["is_central_planet"]),
        "pays_for_research": bool(plan["pays_for_research"]),
        "manual_pinned": _resolve_manual_planet_config(plan, ctx.manual_pins) is not None,
        "temperature": map_int(plan, "temperature_c", 0),
        "temperature_c": map_int(plan, "temperature_c", 0),
        "fields_max": fields_max,
        "fields_used_current": map_int(plan, "fields_used_current", 0),
        "fields_used_target": fields_used_t,
        "fields_used": fields_used_t,
        "fields_free": fields_max - fields_used_t,
        "built_floors": {f"b{int(bid)}": int(built_lvls[f"b{int(bid)}"]) for bid in sorted(game_data.BUILDING_BY_ID)},
        "reserved_floors": as_int_dict(plan["reserved_floors"]),
        "detected_specialization": detected_spec,
        "locked_specialization": locked_spec,
        "previous_specialization": prev_spec,
        "specialization_changed": spec_changed,
        "production": production_info,
        "warehouses": warehouse_rows,
        "buildings": _planet_building_rows(plan, pmap, ctx.speedup_factor),
    }


def _build_limits_and_bottlenecks(
    limit_names: tuple[str, ...],
    ship_cost_vec: FloatArray,
    limit_totals: FloatArray,
    ships: float,
) -> tuple[list[dict[str, object]], str]:
    """Build empire limit rows and comma-separated bottleneck label."""
    needed_per_ship = [*ship_cost_vec.tolist(), 1.0]
    min_limit = float(limit_totals.min()) if limit_totals.size else 0.0
    limits_records: list[dict[str, object]] = []
    bottleneck_names: list[str] = []
    for lname, needed, total_sh in zip(limit_names, needed_per_ship, limit_totals.tolist(), strict=True):
        total_f, needed_f = float(total_sh), float(needed)
        is_bn = bool(total_f == min_limit)
        if is_bn:
            bottleneck_names.append(str(lname))
        limits_records.append(
            {
                "limit": str(lname),
                "needed_per_ship": needed_f,
                "ships_per_month": round(total_f, 2),
                "available_per_month": round(total_f * needed_f, 1),
                "used_percent": round(100.0 * ships / total_f, 2) if total_f > 0.0 else 0.0,
                "is_bottleneck": is_bn,
            }
        )
    return limits_records, ", ".join(bottleneck_names)


def _build_override_summary(
    setup: _OverrideSetup,
    ctx: _EmpireReportCtx,
    totals: _EmpireTotals,
    central_rec: Mapping[str, object] | None,
    *,
    planet_count: int,
    limiting_factor: str,
    elapsed_ms: float,
) -> dict[str, object]:
    """Build the ``summary`` dictionary for a manual planet override payload."""
    ship_spec, ships = ctx.ship_spec, ctx.ships
    ship_cost_sp = spice_eq(ship_spec.ship_base_cost)
    research_cost_sp = float(
        sum(
            research_upgrade_cost_spice_eq(k, setup.own_researches.get(k, 0), lvl)
            for k, lvl in setup.planned_research_levels.items()
        )
    )
    investment = totals.buildings_cost + totals.fleet_cost + research_cost_sp
    monthly_output = ships * ship_cost_sp
    c_lbl = f"{central_rec['name']} [{central_rec['coords']}] (slot {central_rec['slot_index']})" if central_rec else ""
    return {
        "target_ship_key": ship_spec.target_ship_key,
        "target_ship": ship_spec.ship_name,
        "ships_per_month": round(ships, 2),
        "whole_ships_per_month": int(ships),
        "bottleneck": limiting_factor,
        "limiting_factor": limiting_factor,
        "hours_per_month": float(ship_spec.hours_per_month),
        "planets": int(planet_count),
        "central_planet": c_lbl,
        "central_planet_name": str(central_rec["name"]) if central_rec is not None else "",
        "central_planet_coords": str(central_rec["coords"]) if central_rec is not None else "",
        "fields_used": totals.fields_used,
        "fields_free": totals.fields_free,
        "warehouse_fields": totals.warehouse_fields,
        "pig_iron_left_over_per_month": round(totals.pig_iron_hr * ship_spec.hours_per_month, 1),
        "levels_to_build": totals.levels_to_build,
        "levels_to_demolish": totals.levels_to_demolish,
        "demolitions_when_full": totals.demolitions_when_full,
        "allow_demolition": bool(setup.cfg["allow_demolition"] or totals.levels_to_demolish > 0),
        "buildings_cost_spice_eq": round(totals.buildings_cost, 2),
        "fleet_cost_spice_eq": round(totals.fleet_cost, 2),
        "research_cost_spice_eq": round(research_cost_sp, 2),
        "investment_spice_eq": round(investment, 2),
        "ship_cost_spice_eq": round(ship_cost_sp, 2),
        "monthly_output_spice_eq": round(monthly_output, 2),
        "investment_in_months_of_output": round(investment / monthly_output, 2) if monthly_output > 0.0 else None,
        "sticky_plan_applied": False,
        "preserve_previous_specializations": bool(setup.cfg["preserve_previous_specializations"]),
        "specialization_switch_threshold_pct": map_float(setup.cfg, "specialization_switch_threshold_pct", 0.5),
        "locked_planets_count": int(totals.locked_planets),
        "roles_preserved_count": int(totals.roles_preserved),
        "roles_changed_count": int(totals.roles_changed),
        "manual_override_applied": True,
        "elapsed_ms": elapsed_ms,
    }


def _assemble_override_result(
    setup: _OverrideSetup,
    ctx: _EmpireReportCtx,
    planet_records: Sequence[Mapping[str, object]],
    limits_records: list[dict[str, object]],
    totals: _EmpireTotals,
    *,
    limiting_factor: str,
    override_slot: int,
    base_result: Mapping[str, object] | None,
    elapsed_ms: float,
) -> dict[str, object]:
    """Assemble the full JSON-ready manual override response payload."""
    c_idx = ctx.central_planet_idx
    central_rec = planet_records[c_idx] if 0 <= c_idx < len(planet_records) else None
    resolved_names = {
        str(rec["planet_id"]): str(rec["name"]) for rec in planet_records if map_int(rec, "planet_id", 0) > 0
    }
    prev_search = dict(as_mapping(base_result.get("search"))) if isinstance(base_result, Mapping) else {}
    search_summary: dict[str, object] = {
        **prev_search,
        "polished_ships_per_month": round(ctx.ships, 2),
        "locked_planets_count": int(totals.locked_planets),
        "manual_override_applied": True,
        "manual_override_slot": int(override_slot),
    }
    summary = _build_override_summary(
        setup,
        ctx,
        totals,
        central_rec,
        planet_count=len(planet_records),
        limiting_factor=limiting_factor,
        elapsed_ms=elapsed_ms,
    )
    return json_ready_dict(
        {
            "description": "Config Optimizer result with operator manual planet configuration override.",
            "schema_version": 1,
            "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "meta_parameters": setup.cfg,
            "inputs": {
                "universe_speedup_factor": setup.speedup_factor,
                "owned_planets": setup.owned_count,
                "roster_planets": len(planet_records),
                "pad_generic_colonies": bool(setup.cfg["pad_generic_colonies"]),
                "own_research_levels": setup.own_researches,
                "universe_research_max_levels": setup.uni_max,
            },
            "planned_research_levels": setup.planned_research_levels,
            "planned_research_rows": setup.planned_research_rows,
            "planet_names": resolved_names,
            "summary": summary,
            "search": search_summary,
            "limits": limits_records,
            "planets": list(planet_records),
            "elapsed_ms": elapsed_ms,
        }
    )
