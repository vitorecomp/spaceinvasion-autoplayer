"""Research and per-planet plan builders for the Config Optimizer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math

from spaceinvasion.core.coercion import coerce_int
from spaceinvasion.core.model import normalize_planet_type
from spaceinvasion.planner.config_opt._protocols import PlanetLike, as_float_dict, as_int_dict, as_str_dict
from spaceinvasion.planner.config_opt.catalog_math import (
    catalog_top_level,
    full_cost,
    research_catalog,
    ship_info,
    spice_eq,
)
from spaceinvasion.planner.config_opt.constants import (
    _MINE_BUILDING_TO_RESOURCE,
    RESOURCE_KEYS,
    SPECIALIZATION_ALLOWED_BUILDINGS,
    WAREHOUSE_KEYS,
)
from spaceinvasion.planner.config_opt.params import (
    _resolve_planet_display_name,
    _resolve_planet_reserved_floors,
    _resolve_planet_specialization,
    normalize_planet_name,
    normalize_planet_specialization,
)
from spaceinvasion.planner.config_opt.tech_graph import (
    building_upgrade_costs,
    largest_costs,
    same_planet_building_requirements,
)


def plan_research_levels(
    universe_max_levels: Mapping[str, int],
    own_levels: Mapping[str, int],
    required_levels: Mapping[str, int],
    percent_of_universe_max: float,
) -> list[dict[str, object]]:
    """Compute planned research levels: share of universe max, never below own or required level."""
    rows: list[dict[str, object]] = []
    pct = max(0.0, float(percent_of_universe_max))
    for key, info in research_catalog():
        levels_raw = info.get("levels")
        levels_len = len(levels_raw) if isinstance(levels_raw, Mapping) else 0
        catalog_max = coerce_int(info.get("max_table_level"), levels_len) or levels_len
        own_level = max(0, coerce_int(own_levels.get(key, 0), 0))
        universe_max = max(own_level, max(0, coerce_int(universe_max_levels.get(key, 0), 0)))
        percent_level = min(catalog_max, int(math.floor(universe_max * pct / 100.0 + 0.5)))
        required_level = max(0, coerce_int(required_levels.get(key, 0), 0))
        planned_level = max(own_level, percent_level, required_level)
        if planned_level == 0:
            set_by = "not needed"
        elif planned_level == own_level:
            set_by = "current level"
        elif planned_level == percent_level:
            set_by = "universe maximum"
        else:
            set_by = "prerequisite"
        rows.append(
            {
                "key": key,
                "name": str(info["name"]),
                "own_level": own_level,
                "universe_max_level": universe_max,
                "percent_level": percent_level,
                "required_level": required_level,
                "planned_level": planned_level,
                "set_by": set_by,
            }
        )
    return rows


def _compute_fixed_and_required(
    built: Mapping[str, int],
    reserved: Mapping[str, int],
    optimized_max_levels: Mapping[str, int],
    misc_levels: Mapping[str, int],
    min_fleet: Mapping[str, int],
    *,
    allow_demolition: bool,
) -> tuple[dict[str, int], dict[str, int]]:
    wanted = {
        key: (
            max(int(level), reserved[key])
            if (allow_demolition and key == "b6")
            else max(int(level), built[key], reserved[key])
        )
        for key, level in misc_levels.items()
    }
    required: dict[str, int] = {}
    for needs in (
        *(same_planet_building_requirements(ship) for ship, count in min_fleet.items() if count > 0),
        *(same_planet_building_requirements(key, level) for key, level in wanted.items() if level > 0),
        *(
            same_planet_building_requirements(key, reserved[key])
            for key in optimized_max_levels
            if reserved[key] > 0 and (allow_demolition or reserved[key] > built[key])
        ),
    ):
        for key, level in needs.items():
            required[key] = max(required.get(key, 0), level)
    fixed_levels = {key: max(level, required.get(key, 0)) for key, level in wanted.items() if key not in WAREHOUSE_KEYS}
    return fixed_levels, required


def _compute_opt_bounds(
    built: Mapping[str, int],
    reserved: Mapping[str, int],
    required: Mapping[str, int],
    fixed_levels: Mapping[str, int],
    optimized_max_levels: Mapping[str, int],
    allowed_for_spec: frozenset[str] | None,
    *,
    allow_demolition: bool,
) -> tuple[dict[str, int], dict[str, int], dict[str, str]]:
    min_levels: dict[str, int] = {}
    max_levels: dict[str, int] = {}
    locked_by: dict[str, str] = {}
    for key, max_level in optimized_max_levels.items():
        unmet = [
            f"{source} L{level}"
            for source, level in same_planet_building_requirements(key).items()
            if fixed_levels.get(source, level) < level
        ]
        hard_floor = reserved[key] if allow_demolition else max(built[key], reserved[key])
        min_levels[key] = max(required.get(key, 0), hard_floor)
        top_allowed = 0 if unmet else min(int(max_level), catalog_top_level(key))
        if allowed_for_spec is not None and key not in allowed_for_spec:
            if allow_demolition and key not in _MINE_BUILDING_TO_RESOURCE:
                top_allowed = max(min_levels[key], built[key]) if not unmet else min_levels[key]
            else:
                top_allowed = min_levels[key]
        max_levels[key] = max(min_levels[key], top_allowed)
        if unmet and max_levels[key] == 0:
            locked_by[key] = ", ".join(unmet)
    return min_levels, max_levels, locked_by


def _compute_warehouse_mins(
    built: Mapping[str, int],
    reserved: Mapping[str, int],
    misc_levels: Mapping[str, int],
    *,
    min_warehouse_level: int,
    central_min_warehouse_level: int | None,
) -> tuple[dict[str, int], dict[str, str]]:
    warehouse_min_levels: dict[str, int] = {}
    warehouse_min_set_by: dict[str, str] = {}
    for key in WAREHOUSE_KEYS:
        level, reason = max(
            [
                (built[key], "level already built"),
                (reserved[key], "reserved floor"),
                (int(central_min_warehouse_level or 0), "central planet minimum"),
                (int(min_warehouse_level or 0), "minimum warehouse level"),
                (int(misc_levels[key]), "MISC_BUILDING_LEVELS"),
            ],
            key=lambda candidate: candidate[0],
        )
        warehouse_min_levels[key] = level
        warehouse_min_set_by[key] = reason if level > 0 else "no minimum"
    return warehouse_min_levels, warehouse_min_set_by


def build_planet_plan(
    *,
    slot_index: int,
    planet: PlanetLike,
    is_placeholder: bool,
    optimized_max_levels: Mapping[str, int],
    misc_levels: Mapping[str, int],
    min_fleet: Mapping[str, int],
    reserved_floors: Mapping[str, int],
    min_warehouse_level: int = 0,
    central_min_warehouse_level: int | None = None,
    specialization: str = "auto",
    allow_demolition: bool = False,
    display_name: str | None = None,
    game_name: str | None = None,
) -> dict[str, object]:
    """Fixed part, built/reserved floors, specialization lock, and search range of one planet."""
    all_keys = (*misc_levels, *optimized_max_levels)
    built = {key: max(0, coerce_int(planet.buildings.get(key, 0), 0)) for key in all_keys}
    reserved = {key: max(0, coerce_int(reserved_floors.get(key, 0), 0)) for key in all_keys}
    reserved_nonzero = {key: lvl for key, lvl in reserved.items() if lvl > 0}
    locked_spec = normalize_planet_specialization(specialization)
    allowed_for_spec = SPECIALIZATION_ALLOWED_BUILDINGS.get(locked_spec)

    fixed_levels, required = _compute_fixed_and_required(
        built, reserved, optimized_max_levels, misc_levels, min_fleet, allow_demolition=allow_demolition
    )
    min_levels, max_levels, locked_by = _compute_opt_bounds(
        built,
        reserved,
        required,
        fixed_levels,
        optimized_max_levels,
        allowed_for_spec,
        allow_demolition=allow_demolition,
    )
    wh_min_levels, wh_min_set_by = _compute_warehouse_mins(
        built,
        reserved,
        misc_levels,
        min_warehouse_level=min_warehouse_level,
        central_min_warehouse_level=central_min_warehouse_level,
    )

    stationed = {ship: max(0, coerce_int(planet.ships.get(ship, 0), 0)) for ship in {*min_fleet, "s9"}}
    fleet_missing = {ship: max(0, int(count) - stationed[ship]) for ship, count in min_fleet.items()}
    cost_entries: list[tuple[str, Mapping[str, object]]] = [
        *(cost for key, level in fixed_levels.items() for cost in building_upgrade_costs(key, built[key], level)),
    ]
    for ship, missing in fleet_missing.items():
        if missing > 0:
            raw_bc = ship_info(ship).get("base_cost")
            cost_entries.append((ship, dict(raw_bc) if isinstance(raw_bc, Mapping) else {}))
    capacity_needed, capacity_needed_for = largest_costs(cost_entries)

    fixed_fields = sum(fixed_levels.values())
    fields_max = max(int(planet.fields_max), sum(built.values()))
    resolved_game_name = normalize_planet_name(game_name) or normalize_planet_name(planet.name) or str(planet.coords)
    resolved_name = (
        normalize_planet_name(display_name)
        or normalize_planet_name(planet.name)
        or resolved_game_name
        or str(planet.coords)
    )
    fleet_cost_spice_eq = float(
        sum(count * spice_eq(full_cost(ship_info(ship).get("base_cost"))) for ship, count in fleet_missing.items())
    )
    return {
        "slot_index": int(slot_index),
        "slot": int(slot_index),
        "planet_id": int(planet.planet_id),
        "coords": str(planet.coords),
        "name": resolved_name,
        "game_name": resolved_game_name,
        "planet_status": "empty_colony_slot" if is_placeholder else "colonized",
        "is_placeholder": bool(is_placeholder),
        "temperature_c": int(planet.temperature),
        "temperature": int(planet.temperature),
        "fields_max": fields_max,
        "fields_used_current": int(planet.fields_used),
        "fixed_fields": fixed_fields,
        "free_fields": fields_max - fixed_fields,
        "built_levels": built,
        "reserved_floors": reserved_nonzero,
        "locked_specialization": locked_spec,
        "fixed_levels": fixed_levels,
        "min_levels": min_levels,
        "max_levels": max_levels,
        "locked_by": locked_by,
        "warehouse_min_levels": wh_min_levels,
        "warehouse_min_set_by": wh_min_set_by,
        "capacity_needed": capacity_needed,
        "capacity_needed_for": capacity_needed_for,
        "is_central_planet": central_min_warehouse_level is not None,
        "pays_for_research": False,
        "solar_cells": max(int(min_fleet.get("s9", 0)), stationed["s9"]),
        "fleet_missing": fleet_missing,
        "fleet_cost_spice_eq": fleet_cost_spice_eq,
    }


def build_planet_plans(
    planets: Sequence[PlanetLike],
    *,
    owned_count: int,
    optimized_max_levels: Mapping[str, int],
    misc_levels: Mapping[str, int],
    misc_levels_by_slot: Mapping[int, Mapping[str, int]],
    min_fleet: Mapping[str, int],
    central_planet_slot: int,
    central_min_warehouse_level: int,
    research_costs: list[tuple[str, dict[str, object]]],
    reserved_floors_by_planet: Mapping[str, Mapping[str, int]],
    min_warehouse_level: int = 0,
    planet_specializations: Mapping[str, str] | None = None,
    allow_demolition: bool = False,
    planet_names: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Build per-planet plans for every normal planet in the roster."""
    plans: list[dict[str, object]] = []
    for slot_index, planet in enumerate(planets, start=1):
        if normalize_planet_type(planet.planet_type) != "normal":
            continue
        is_placeholder = slot_index > owned_count
        slot_misc = {**misc_levels, **misc_levels_by_slot.get(slot_index, {})}
        reserved = {} if is_placeholder else _resolve_planet_reserved_floors(planet, reserved_floors_by_planet)
        locked_spec = _resolve_planet_specialization(planet, slot_index, planet_specializations)
        resolved_name, resolved_game_name = _resolve_planet_display_name(
            planet, slot_index, None if is_placeholder else planet_names
        )
        plans.append(
            build_planet_plan(
                slot_index=slot_index,
                planet=planet,
                is_placeholder=is_placeholder,
                optimized_max_levels=optimized_max_levels,
                misc_levels=slot_misc,
                min_fleet=min_fleet,
                reserved_floors=reserved,
                min_warehouse_level=min_warehouse_level,
                central_min_warehouse_level=(
                    central_min_warehouse_level if slot_index == central_planet_slot else None
                ),
                specialization=locked_spec,
                allow_demolition=(False if is_placeholder else bool(allow_demolition)),
                display_name=resolved_name,
                game_name=resolved_game_name,
            )
        )
    if not plans:
        raise ValueError("No normal planets available to optimize")
    if not any(bool(plan["is_central_planet"]) for plan in plans):
        raise ValueError(
            f"central_planet_slot {central_planet_slot} is not a normal planet of the roster: "
            f"choose one of {[plan['slot_index'] for plan in plans]}"
        )
    lab_planet = max(plans, key=lambda plan: as_int_dict(plan["fixed_levels"]).get("b8", 0))
    if as_int_dict(lab_planet["fixed_levels"]).get("b8", 0) > 0:
        cap_needed = as_float_dict(lab_planet["capacity_needed"])
        cap_for = as_str_dict(lab_planet["capacity_needed_for"])
        combined_costs: list[tuple[str, Mapping[str, object]]] = [
            *((cap_for.get(resource, ""), {resource: cap_needed.get(resource, 0.0)}) for resource in RESOURCE_KEYS),
            *research_costs,
        ]
        lab_planet["capacity_needed"], lab_planet["capacity_needed_for"] = largest_costs(combined_costs)
        lab_planet["pays_for_research"] = True
    return plans
