"""Catalog lookups, time divisors, warehouse sizing, and hourly building economy math."""

from __future__ import annotations

from collections.abc import Mapping
import math

from spaceinvasion.combat.fleet import SHIP_SLUG_BY_KEY
from spaceinvasion.core import game_data
from spaceinvasion.core.coercion import coerce_finite_float, coerce_int
from spaceinvasion.planner.config_opt.constants import (
    _MINE_BUILDING_TO_RESOURCE,
    _TECH_BONUS_PCT_BY_KEY,
    RESOURCE_KEYS,
)


def building_info(building_id: int) -> dict[str, object]:
    """Return typed metadata dictionary for building ``building_id``."""
    raw: object = game_data.BUILDING_BY_ID[int(building_id)]
    return dict(raw) if isinstance(raw, Mapping) else {}


def building_levels_map(building_id: int) -> dict[int, dict[str, object]]:
    """Return ``{level: row}`` table for building ``building_id``."""
    raw = building_info(building_id).get("levels")
    if not isinstance(raw, Mapping):
        return {}
    return {int(k): dict(v) for k, v in raw.items() if isinstance(v, Mapping)}


def research_info(key: str) -> dict[str, object]:
    """Return typed metadata dictionary for research ``key`` (e.g. ``'r1'``)."""
    raw: object = game_data.RESEARCH_BY_ID[key]
    return dict(raw) if isinstance(raw, Mapping) else {}


def ship_info(key: str) -> dict[str, object]:
    """Return typed metadata dictionary for ship ``key`` (e.g. ``'s16'``)."""
    raw: object = game_data.SHIPS[SHIP_SLUG_BY_KEY[key]]
    return dict(raw) if isinstance(raw, Mapping) else {}


def building_cost_map(building_id: int, level: int) -> dict[str, int]:
    """Return resource cost mapping for ``building_id`` at ``level``."""
    raw: object = game_data.building_cost(int(building_id), int(level))
    if not isinstance(raw, Mapping):
        return {r: 0 for r in RESOURCE_KEYS}
    return {str(k): coerce_int(v, 0) for k, v in raw.items()}


def spice_eq(cost: Mapping[str, object]) -> float:
    """Return SpiceEq valuation of a resource cost mapping."""
    return float(game_data.spice_equivalent(dict(cost)))


def fmt_duration(seconds: float) -> str:
    """Format seconds as ``45s``, ``3m 07s``, ``5h 35m``, or ``12d 04h``."""
    if not math.isfinite(seconds) or seconds <= 0.0:
        return "0s"
    sec_i = int(round(seconds))
    if sec_i < 60:
        return f"{sec_i}s"
    mins, rem_s = divmod(sec_i, 60)
    if mins < 60:
        return f"{mins}m {rem_s:02d}s"
    hrs, rem_m = divmod(mins, 60)
    if hrs < 48:
        return f"{hrs}h {rem_m:02d}m"
    days, rem_h = divmod(hrs, 24)
    return f"{days}d {rem_h:02d}h"


def research_catalog() -> list[tuple[str, dict[str, object]]]:
    """Researches as ``(key, info)`` in numeric key order (``r1``, ``r2``, ...)."""
    items: list[tuple[str, dict[str, object]]] = []
    for key, info in game_data.RESEARCH_BY_ID.items():
        items.append((str(key), dict(info) if isinstance(info, Mapping) else {}))
    items.sort(key=lambda entry: int(entry[0][1:]))
    return items


def unit_keys(slug_by_key: Mapping[str, str]) -> list[str]:
    """Ship or defense keys (``s1``, ``d1``, ...) in numeric order."""
    return sorted(slug_by_key, key=lambda key: int(key[1:]))


def full_cost(raw_cost: object) -> dict[str, int]:
    """Copy a cost mapping, adding 0 for any omitted primary resource."""
    src = raw_cost if isinstance(raw_cost, Mapping) else {}
    return {resource: max(0, coerce_int(src.get(resource, 0), 0)) for resource in RESOURCE_KEYS}


def build_monotonic_base_times(
    levels_map: Mapping[int, Mapping[str, object]],
    max_level: int,
) -> dict[int, float]:
    """Return ``{level: base_time_s}``, repairing truncated scientific notation at high levels."""
    times: dict[int, float] = {}
    previous_time = 0.0
    previous_cost = 0.0
    for level in range(1, max(0, int(max_level)) + 1):
        row = levels_map.get(level) or {}
        raw_time = coerce_finite_float(row.get("base_time_s"), 0.0)
        raw_cost = row.get("cost")
        cost = spice_eq(raw_cost if isinstance(raw_cost, Mapping) else {})
        if level > 1 and previous_time > 0.0 and raw_time < previous_time:
            ratio = (cost / previous_cost) if previous_cost > 0.0 else 1.51
            fixed_time = previous_time * max(1.05, ratio)
        else:
            fixed_time = max(0.0, raw_time)
        times[level] = fixed_time
        previous_time = fixed_time
        previous_cost = cost
    return times


def building_time_divisor(
    dev_center_level: int,
    microsystem_level: int,
    speedup_factor: float,
) -> float:
    """Divisor of a building's base time: ``(b7 + 1) * 2^b17 * speed``, never below 1."""
    return max(
        1.0,
        float(max(0, int(dev_center_level)) + 1)
        * float(2 ** min(max(0, int(microsystem_level)), 40))
        * float(speedup_factor),
    )


def research_time_divisor(research_lab_level: int, speedup_factor: float) -> float:
    """Divisor of a research's base time: ``(b8 + 1) * speed``, never below 1."""
    return max(1.0, float(max(0, int(research_lab_level)) + 1) * float(speedup_factor))


def shipyard_time_divisor(
    weapons_factory_level: int,
    microsystem_level: int,
    speedup_factor: float,
) -> float:
    """Divisor of a ship's or defense's base time: ``max(1, b9) * 2^b17 * speed``, never below 1."""
    return max(
        1.0,
        float(max(1, int(weapons_factory_level)))
        * float(2 ** min(max(0, int(microsystem_level)), 40))
        * float(speedup_factor),
    )


def warehouse_capacity(level: int) -> int:
    """Storage capacity of a warehouse at ``level``: ``100,000 * 2^level``."""
    return int(100_000 * (2 ** max(0, int(level))))


def warehouse_level_for(amount: float) -> int:
    """Lowest warehouse level whose capacity holds ``amount`` units of a resource."""
    target = float(amount)
    if not math.isfinite(target) or target <= 0.0:
        return 0
    level = 0
    while warehouse_capacity(level) < target:
        level += 1
        if level > 200:
            break
    return level


def solar_cell_energy_per_unit(temperature_c: float) -> float:
    """Base energy of one orbital Solar cell (``s9``), interpolated from the wiki curve."""
    raw_table = ship_info("s9").get("solar_cell_energy_by_temp")
    table: dict[int, int] = (
        {coerce_int(k, 0): coerce_int(v, 0) for k, v in raw_table.items()} if isinstance(raw_table, Mapping) else {}
    )
    return float(game_data.interpolate_temp_value(table, temperature_c))


def technology_multiplier(research_key: str, level: int) -> float:
    """Multiplier a research gives at ``level``: ``1 + per_level_bonus_pct / 100 * level``."""
    bonus_per_level = float(_TECH_BONUS_PCT_BY_KEY.get(str(research_key), 0.0)) / 100.0
    return 1.0 + bonus_per_level * float(max(0, int(level)))


def produced_resource(building_key: str) -> str:
    """Resource that a building produces (``'none'`` for non-mines)."""
    return _MINE_BUILDING_TO_RESOURCE.get(str(building_key), "none")


def building_level_economy(
    building_id: int,
    level: int,
    context: Mapping[str, object],
) -> dict[str, float]:
    """Hourly output, feedstock, net SpiceEq value, and energy of one building level."""
    lvl = max(0, int(level))
    row = building_levels_map(building_id).get(lvl) or {}
    merchant_rates = game_data.MERCHANT_RATES_IN_SPICE
    resource = produced_resource(f"b{int(building_id)}")
    speedup = coerce_finite_float(context["speedup_factor"], 1.0)

    if resource != "none":
        temperature = coerce_int(context["planet_temperature_c"], 0)
        production_1x = float(
            game_data.mine_production_per_hour(int(building_id), lvl, temperature=temperature, mining_tech_level=0)
        )
        production = speedup * float(
            game_data.mine_production_per_hour(
                int(building_id),
                lvl,
                temperature=temperature,
                mining_tech_level=coerce_int(context["r1_mining_tech"], 0),
            )
        )
    else:
        production_1x = 0.0
        production = 0.0

    pig_iron_used = coerce_finite_float(row.get("pig_iron_used"), 0.0) * speedup
    spice_used = coerce_finite_float(row.get("spice_used"), 0.0)
    gross_spice_eq_hr = production / float(merchant_rates[resource]) if resource in merchant_rates else 0.0
    feedstock_spice_eq_hr = pig_iron_used / float(merchant_rates["pig_iron"]) + spice_used / float(
        merchant_rates["spice"]
    )
    energy_base = coerce_finite_float(row.get("energy"), 0.0)
    energy_multiplier = technology_multiplier("r2", coerce_int(context["r2_energy_tech"], 0))
    return {
        "production_1x": production_1x,
        "production": production,
        "pig_iron_used": pig_iron_used,
        "spice_used": spice_used,
        "net_spice_eq_hr": gross_spice_eq_hr - feedstock_spice_eq_hr,
        "energy_base": energy_base,
        "energy": (energy_base * energy_multiplier) if energy_base > 0.0 else energy_base,
    }


def building_level_flows(
    building_key: str,
    level: int,
    context: Mapping[str, object],
) -> list[float]:
    """Hourly flows of one building level at full power in ``PRODUCTION_FLOW_KEYS`` order."""
    economy = building_level_economy(int(building_key[1:]), level, context)
    output = {produced_resource(building_key): economy["production"]}
    return [
        *(float(output.get(resource, 0.0)) for resource in RESOURCE_KEYS),
        economy["pig_iron_used"],
        economy["spice_used"],
        max(0.0, economy["energy"]),
        max(0.0, -economy["energy"]),
    ]


def catalog_top_level(key: str) -> int:
    """Last level of building ``key`` listed in ``game_data``."""
    info = building_info(int(key[1:]))
    levels = info.get("levels")
    fallback = len(levels) if isinstance(levels, Mapping) else 0
    return coerce_int(info.get("max_table_level"), fallback) or fallback


def last_level_build_time(
    key: str,
    planned_levels: Mapping[str, int],
    speedup_factor: float,
) -> float:
    """Seconds that the last level of building ``key`` takes at ``planned_levels``."""
    level = int(planned_levels.get(key, 0))
    if level <= 0:
        return 0.0
    base_times = build_monotonic_base_times(building_levels_map(int(key[1:])), level)
    dev_center_level = max(0, int(planned_levels.get("b7", 0)) - (1 if key == "b7" else 0))
    microsystem_level = max(0, int(planned_levels.get("b17", 0)) - (1 if key == "b17" else 0))
    return float(base_times[level]) / building_time_divisor(dev_center_level, microsystem_level, speedup_factor)
