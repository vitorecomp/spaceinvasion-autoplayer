"""Shared reporting rows and specialization detection helpers for ``config_opt``."""

from __future__ import annotations

from collections.abc import Mapping

from spaceinvasion.core import game_data
from spaceinvasion.core.coercion import coerce_int
from spaceinvasion.planner.config_opt._protocols import as_float_dict, as_int_dict, as_str_dict
from spaceinvasion.planner.config_opt.catalog_math import (
    building_info,
    fmt_duration,
    last_level_build_time,
    ship_info,
    warehouse_capacity,
    warehouse_level_for,
)
from spaceinvasion.planner.config_opt.constants import (
    RESOURCE_KEYS,
    SPECIALIZATION_ALLOWED_BUILDINGS,
    WAREHOUSE_KEYS,
    FloatArray,
)
from spaceinvasion.planner.config_opt.params import normalize_planet_specialization
from spaceinvasion.planner.config_opt.tech_graph import (
    building_upgrade_cost_spice_eq,
    building_upgrade_costs,
    largest_costs,
)


def _warehouse_requirement_rows(
    plan: Mapping[str, object],
    planned_levels: Mapping[str, int],
    net_per_hour: FloatArray,
    *,
    warehouse_hours: float,
    ship_built: str | None,
    central_reserve: FloatArray,
) -> list[dict[str, object]]:
    """Build per-resource warehouse sizing explanation rows for a planet."""
    cap_needed = as_float_dict(plan["capacity_needed"])
    cap_for = as_str_dict(plan["capacity_needed_for"])
    built_levels = as_int_dict(plan["built_levels"])
    min_levels = as_int_dict(plan["min_levels"])
    wh_min_levels = as_int_dict(plan["warehouse_min_levels"])
    wh_min_set_by = as_str_dict(plan["warehouse_min_set_by"])

    cost_items: list[tuple[str, Mapping[str, object]]] = [
        *((cap_for[resource], {resource: cap_needed[resource]}) for resource in RESOURCE_KEYS),
        *(
            cost
            for key in (*min_levels, *WAREHOUSE_KEYS)
            for cost in building_upgrade_costs(key, built_levels[key], planned_levels[key])
        ),
    ]
    if ship_built:
        raw_bc = ship_info(ship_built).get("base_cost")
        cost_items.append((ship_built, dict(raw_bc) if isinstance(raw_bc, Mapping) else {}))

    largest_cost, largest_cost_of = largest_costs(cost_items)
    rows: list[dict[str, object]] = []
    for index, (resource, key) in enumerate(zip(RESOURCE_KEYS, WAREHOUSE_KEYS, strict=True)):
        asked = [
            (warehouse_level_for(largest_cost[resource]), f"cost of {largest_cost_of[resource]}"),
            (warehouse_level_for(warehouse_hours * max(0.0, float(net_per_hour[index]))), "own production"),
            (warehouse_level_for(float(central_reserve[index])), "empire production"),
            (wh_min_levels[key], wh_min_set_by[key]),
        ]
        level, set_by = max(asked, key=lambda rule: rule[0])
        rows.append(
            {
                "resource": resource,
                "key": key,
                "built_level": int(built_levels[key]),
                "level": int(planned_levels[key]),
                "capacity": float(warehouse_capacity(planned_levels[key])),
                "largest_cost": float(largest_cost[resource]),
                "largest_cost_of": str(largest_cost_of[resource]),
                "level_for_largest_cost": int(asked[0][0]),
                "level_for_own_production": int(asked[1][0]),
                "level_for_empire_production": int(asked[2][0]),
                "minimum_level": int(asked[3][0]),
                "set_by": set_by if level > 0 else "nothing to store",
            }
        )
    return rows


def _planet_building_rows(
    plan: Mapping[str, object],
    planned_levels: Mapping[str, int],
    speedup_factor: float,
) -> list[dict[str, object]]:
    """Build per-building target breakdown rows for a planet."""
    rows: list[dict[str, object]] = []
    reserved = as_int_dict(plan.get("reserved_floors"))
    built_levels = as_int_dict(plan["built_levels"])
    min_levels = as_int_dict(plan["min_levels"])
    for building_id_raw in sorted(game_data.BUILDING_BY_ID):
        building_id = int(building_id_raw)
        key = f"b{building_id}"
        built_level = int(built_levels[key])
        target_level = int(planned_levels[key])
        if built_level == 0 and target_level == 0:
            continue
        if key in min_levels:
            role = "optimized"
        else:
            role = "warehouse" if key in WAREHOUSE_KEYS else "fixed"
        build_time = last_level_build_time(key, planned_levels, speedup_factor) if target_level > built_level else 0.0
        rows.append(
            {
                "key": key,
                "building": str(building_info(building_id)["name"]),
                "role": role,
                "built_level": built_level,
                "reserved_floor": int(reserved.get(key, 0)),
                "target_level": target_level,
                "levels_to_build": max(0, target_level - built_level),
                "levels_to_demolish": max(0, built_level - target_level),
                "upgrade_cost_spice_eq": round(building_upgrade_cost_spice_eq(key, built_level, target_level), 2),
                "last_level_build_time_s": round(build_time, 2),
                "last_level_build_time_text": fmt_duration(build_time),
            }
        )
    return rows


def detect_planet_specialization(
    planned_levels: Mapping[str, int],
    *,
    min_levels: Mapping[str, int] | None = None,
    built_levels: Mapping[str, int] | None = None,
    shipyard_ships: float = 0.0,
    locked_specialization: str = "auto",
) -> str:
    """Classify a planet's target configuration into its primary SpaceInvasion Classic role."""
    locked_norm = normalize_planet_specialization(locked_specialization)
    if locked_norm in SPECIALIZATION_ALLOWED_BUILDINGS:
        return locked_norm

    floor_map = min_levels if min_levels is not None else (built_levels or {})
    b1 = max(0, coerce_int(planned_levels.get("b1", 0), 0))
    b2 = max(0, coerce_int(planned_levels.get("b2", 0), 0))
    b3 = max(0, coerce_int(planned_levels.get("b3", 0), 0))
    b4 = max(0, coerce_int(planned_levels.get("b4", 0), 0))
    b9 = max(0, coerce_int(planned_levels.get("b9", 0), 0))
    b17 = max(0, coerce_int(planned_levels.get("b17", 0), 0))

    d_b1 = max(0, b1 - max(0, coerce_int(floor_map.get("b1", 0), 0)))
    d_b2 = max(0, b2 - max(0, coerce_int(floor_map.get("b2", 0), 0)))
    d_b3 = max(0, b3 - max(0, coerce_int(floor_map.get("b3", 0), 0)))
    d_b4 = max(0, b4 - max(0, coerce_int(floor_map.get("b4", 0), 0)))
    d_b9 = max(0, b9 - max(0, coerce_int(floor_map.get("b9", 0), 0)))
    d_b17 = max(0, b17 - max(0, coerce_int(floor_map.get("b17", 0), 0)))

    metal_lvl = float(b2) if (b2 > 0 and b1 >= max(1, b2 - 8)) else 0.5 * float(b1 + b2)
    mine_scores = {
        "metal": (metal_lvl, max(float(d_b2), 0.75 * float(d_b1))),
        "kryptonite": (float(b3), float(d_b3)),
        "spice": (float(b4), float(d_b4)),
    }
    top_mine_role, (top_mine_lvl, top_mine_delta) = max(mine_scores.items(), key=lambda kv: kv[1])
    best_mine_delta = max(v[1] for v in mine_scores.values())
    shipyard_delta = float(d_b9) + 3.0 * float(d_b17)
    active_shipyard_growth = (float(shipyard_ships) > 0.0) and (d_b17 >= 1 or d_b9 >= 3)
    static_shipyard_role = (
        (float(shipyard_ships) > 0.0) and (best_mine_delta <= 0.0) and (b17 >= 2 or (b9 >= 12 and top_mine_lvl < 15.0))
    )

    if active_shipyard_growth or static_shipyard_role:
        if b4 >= 12 and (top_mine_role == "spice" or d_b4 >= 4 or float(b4) >= top_mine_lvl - 6.0):
            return "spice_shipyard"
        if (
            top_mine_lvl <= 15.0
            or best_mine_delta <= 2.0
            or (shipyard_delta >= best_mine_delta and top_mine_delta <= 8.0)
        ):
            return "shipyard"

    if top_mine_lvl > 0.0:
        return top_mine_role
    return "balanced"
