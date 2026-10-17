"""Pure-Python Empire Configuration Optimizer (Genetic Algorithm + Polish).

Direct, zero-pandas/Jupyter/Selenium port of `notebooks/config_optimizer.ipynb`
with hard built-level and operator-reserved building floors so target plans never
drop existing structures on owned colonies.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping
import copy
import datetime
import heapq
import math
import time
from typing import Any, Optional

import numpy as np

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.catalog import DEFENSE_SLUG_BY_KEY, SHIP_SLUG_BY_KEY
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import (
    EnergyBalance,
    GameState,
    PlanetState,
    Resources,
    is_building_allowed_on_planet_type,
    normalize_planet_type,
)

RESOURCE_KEYS: tuple[str, ...] = ("pig_iron", "metal", "kryptonite", "spice")
WAREHOUSE_KEYS: tuple[str, ...] = ("b11", "b12", "b13", "b14")
PRODUCTION_FLOW_KEYS: tuple[str, ...] = (
    *RESOURCE_KEYS,
    "pig_iron_used",
    "spice_used",
    "energy_produced",
    "energy_used",
)

VALID_PLANET_SPECIALIZATIONS: tuple[str, ...] = (
    "auto",
    "metal",
    "kryptonite",
    "spice",
    "shipyard",
    "spice_shipyard",
)
SPECIALIZATION_ALLOWED_BUILDINGS: dict[str, frozenset[str]] = {
    "metal": frozenset({"b1", "b2", "b5"}),
    "kryptonite": frozenset({"b3", "b5"}),
    "spice": frozenset({"b4", "b5"}),
    "shipyard": frozenset({"b9", "b17", "b5"}),
    "spice_shipyard": frozenset({"b4", "b5", "b9", "b17"}),
}
_SPECIALIZATION_ALIASES: dict[str, str] = {
    "auto": "auto",
    "metal": "metal",
    "metal_mine": "metal",
    "blast_furnace": "metal",
    "pig_iron_metal": "metal",
    "kryptonite": "kryptonite",
    "kryptonite_mine": "kryptonite",
    "krypt": "kryptonite",
    "spice": "spice",
    "spice_mine": "spice",
    "shipyard": "shipyard",
    "weapons_factory": "shipyard",
    "fleet": "shipyard",
    "spice_shipyard": "spice_shipyard",
    "spice+shipyard": "spice_shipyard",
    "spice-shipyard": "spice_shipyard",
    "shipyard_spice": "spice_shipyard",
}

_MINE_BUILDING_TO_RESOURCE: dict[str, str] = {
    "b1": "pig_iron",
    "b2": "metal",
    "b3": "kryptonite",
    "b4": "spice",
}

_TECH_BONUS_PCT_BY_KEY: dict[str, float] = {
    "r1": 2.0,
    "r2": 1.0,
    "r4": 10.0,
    "r5": 10.0,
    "r6": 10.0,
    "r8": 10.0,
    "r9": 10.0,
    "r10": 10.0,
}

DEFAULT_CONFIG_OPTIMIZER_PARAMS: dict[str, Any] = {
    "target_ship_key": "s16",
    "production_month_days": 30.0,
    "research_percent_of_universe_max": 100.0,
    "min_fleet_per_planet": {"s2": 10, "s10": 10, "s11": 10},
    "misc_building_levels": {
        "b6": 0,
        "b7": 12,
        "b8": 0,
        "b10": 5,
        "b11": 0,
        "b12": 0,
        "b13": 0,
        "b14": 0,
        "b15": 0,
        "b16": 0,
        "b18": 0,
        "b19": 0,
        "b20": 0,
        "b21": 0,
    },
    "misc_building_levels_by_slot": {"1": {"b8": 12}},
    "warehouse_hours_of_production": 24.0,
    "min_warehouse_level": 0,
    "central_planet_slot": 1,
    "central_planet_min_warehouse_level": 15,
    "optimized_building_max_levels": {
        "b1": 100,
        "b2": 100,
        "b3": 100,
        "b4": 100,
        "b5": 100,
        "b9": 100,
        "b17": 100,
    },
    "catalogue_grid_size": 25000,
    "catalogue_priority_steps": 4,
    "hill_climb_step_sizes": [1, 2, 4, 8],
    "ga_population_size": 200,
    "ga_generations": 150,
    "ga_tournament_size": 3,
    "ga_crossover_rate": 0.9,
    "ga_mutation_rate": 0.1,
    "ga_elite_count": 4,
    "ga_random_seed": 20261117,
    "pad_generic_colonies": False,
    "allow_demolition": False,
    "max_planets": 11,
    "empty_colony_fields": 300,
    "reserved_floors_by_planet": {},
    "planet_specializations": {},
    "planet_names": {},
    "manual_planet_configs": {},
    "preserve_previous_specializations": True,
    "specialization_switch_threshold_pct": 0.5,
}

MAX_PLANET_NAME_LENGTH = 48

_UPPER_PARAM_ALIASES: dict[str, str] = {
    "TARGET_SHIP_KEY": "target_ship_key",
    "PRODUCTION_MONTH_DAYS": "production_month_days",
    "RESEARCH_PERCENT_OF_UNIVERSE_MAX": "research_percent_of_universe_max",
    "MIN_FLEET_PER_PLANET": "min_fleet_per_planet",
    "MISC_BUILDING_LEVELS": "misc_building_levels",
    "MISC_BUILDING_LEVELS_BY_SLOT": "misc_building_levels_by_slot",
    "WAREHOUSE_HOURS_OF_PRODUCTION": "warehouse_hours_of_production",
    "MIN_WAREHOUSE_LEVEL": "min_warehouse_level",
    "CENTRAL_PLANET_SLOT": "central_planet_slot",
    "CENTRAL_PLANET_MIN_WAREHOUSE_LEVEL": "central_planet_min_warehouse_level",
    "OPTIMIZED_BUILDING_MAX_LEVELS": "optimized_building_max_levels",
    "CATALOGUE_GRID_SIZE": "catalogue_grid_size",
    "CATALOGUE_PRIORITY_STEPS": "catalogue_priority_steps",
    "HILL_CLIMB_STEP_SIZES": "hill_climb_step_sizes",
    "GA_POPULATION_SIZE": "ga_population_size",
    "GA_GENERATIONS": "ga_generations",
    "GA_TOURNAMENT_SIZE": "ga_tournament_size",
    "GA_CROSSOVER_RATE": "ga_crossover_rate",
    "GA_MUTATION_RATE": "ga_mutation_rate",
    "GA_ELITE_COUNT": "ga_elite_count",
    "GA_RANDOM_SEED": "ga_random_seed",
    "PAD_GENERIC_COLONIES": "pad_generic_colonies",
    "ALLOW_DEMOLITION": "allow_demolition",
    "PLANET_SPECIALIZATIONS": "planet_specializations",
    "PLANET_NAMES": "planet_names",
    "CUSTOM_PLANET_NAMES": "planet_names",
    "custom_planet_names": "planet_names",
    "MANUAL_PLANET_CONFIGS": "manual_planet_configs",
    "PRESERVE_PREVIOUS_SPECIALIZATIONS": "preserve_previous_specializations",
    "SPECIALIZATION_SWITCH_THRESHOLD_PCT": "specialization_switch_threshold_pct",
}


# ---------------------------------------------------------------------------
# Shared catalog & economy helpers (imported by ai_planner.py as well)
# ---------------------------------------------------------------------------


def fmt_duration(seconds: float) -> str:
    """Format seconds as `45s`, `3m 07s`, `5h 35m`, or `12d 04h`."""
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


def research_catalog() -> list[tuple[str, dict[str, Any]]]:
    """Researches as `(key, info)` in numeric key order (`r1`, `r2`, ...)."""
    return sorted(game_data.RESEARCH_BY_ID.items(), key=lambda entry: int(entry[0][1:]))


def unit_keys(slug_by_key: Mapping[str, str]) -> list[str]:
    """Ship or defense keys (`s1`, `d1`, ...) in numeric order."""
    return sorted(slug_by_key, key=lambda key: int(key[1:]))


def full_cost(raw_cost: Any) -> dict[str, int]:
    """Copy a cost mapping, adding 0 for any omitted primary resource."""
    src = raw_cost if isinstance(raw_cost, Mapping) else {}
    return {resource: max(0, game_data.safe_int(src.get(resource, 0), 0)) for resource in RESOURCE_KEYS}


def build_monotonic_base_times(
    levels_map: Mapping[int, Mapping[str, Any]],
    max_level: int,
) -> dict[int, float]:
    """Return `{level: base_time_s}`, repairing truncated scientific notation at high levels."""
    times: dict[int, float] = {}
    previous_time = 0.0
    previous_cost = 0.0
    for level in range(1, max(0, int(max_level)) + 1):
        row = levels_map.get(level) or {}
        raw_time = float(row.get("base_time_s") or 0.0)
        cost = float(game_data.spice_equivalent(row.get("cost") or {}))
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
    """Divisor of a building's base time: `(b7 + 1) * 2^b17 * speed`, never below 1."""
    return max(
        1.0,
        float(max(0, int(dev_center_level)) + 1)
        * float(2 ** min(max(0, int(microsystem_level)), 40))
        * float(speedup_factor),
    )


def research_time_divisor(research_lab_level: int, speedup_factor: float) -> float:
    """Divisor of a research's base time: `(b8 + 1) * speed`, never below 1."""
    return max(1.0, float(max(0, int(research_lab_level)) + 1) * float(speedup_factor))


def shipyard_time_divisor(
    weapons_factory_level: int,
    microsystem_level: int,
    speedup_factor: float,
) -> float:
    """Divisor of a ship's or defense's base time: `max(1, b9) * 2^b17 * speed`, never below 1."""
    return max(
        1.0,
        float(max(1, int(weapons_factory_level)))
        * float(2 ** min(max(0, int(microsystem_level)), 40))
        * float(speedup_factor),
    )


def warehouse_capacity(level: int) -> int:
    """Storage capacity of a warehouse at `level`: `100,000 * 2^level`."""
    return int(100_000 * (2 ** max(0, int(level))))


def warehouse_level_for(amount: float) -> int:
    """Lowest warehouse level whose capacity holds `amount` units of a resource."""
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
    """Base energy of one orbital Solar cell (`s9`), interpolated from the wiki curve."""
    energy_by_temperature = game_data.SHIPS[SHIP_SLUG_BY_KEY["s9"]]["solar_cell_energy_by_temp"]
    return float(game_data.interpolate_temp_value(energy_by_temperature, temperature_c))


def technology_multiplier(research_key: str, level: int) -> float:
    """Multiplier a research gives at `level`: `1 + per_level_bonus_pct / 100 * level`."""
    bonus_per_level = float(_TECH_BONUS_PCT_BY_KEY.get(str(research_key), 0.0)) / 100.0
    return 1.0 + bonus_per_level * float(max(0, int(level)))


def produced_resource(building_key: str) -> str:
    """Resource that a building produces (`'none'` for non-mines)."""
    return _MINE_BUILDING_TO_RESOURCE.get(str(building_key), "none")


def building_level_economy(
    building_id: int,
    level: int,
    context: Mapping[str, Any],
) -> dict[str, float]:
    """Hourly output, feedstock, net SpiceEq value, and energy of one building level."""
    lvl = max(0, int(level))
    row = (game_data.BUILDING_BY_ID[int(building_id)].get("levels") or {}).get(lvl) or {}
    merchant_rates = game_data.MERCHANT_RATES_IN_SPICE
    resource = produced_resource(f"b{int(building_id)}")
    speedup = float(context["speedup_factor"])

    if resource != "none":
        temperature = int(context["planet_temperature_c"])
        production_1x = float(
            game_data.mine_production_per_hour(int(building_id), lvl, temperature=temperature, mining_tech_level=0)
        )
        production = speedup * float(
            game_data.mine_production_per_hour(
                int(building_id),
                lvl,
                temperature=temperature,
                mining_tech_level=int(context["r1_mining_tech"]),
            )
        )
    else:
        production_1x = 0.0
        production = 0.0

    pig_iron_used = float(row.get("pig_iron_used") or 0.0) * speedup
    spice_used = float(row.get("spice_used") or 0.0)
    gross_spice_eq_hr = production / float(merchant_rates[resource]) if resource in merchant_rates else 0.0
    feedstock_spice_eq_hr = pig_iron_used / float(merchant_rates["pig_iron"]) + spice_used / float(
        merchant_rates["spice"]
    )
    energy_base = float(row.get("energy") or 0.0)
    energy_multiplier = technology_multiplier("r2", int(context["r2_energy_tech"]))
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
    context: Mapping[str, Any],
) -> list[float]:
    """Hourly flows of one building level at full power in `PRODUCTION_FLOW_KEYS` order."""
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
    """Last level of building `key` listed in `game_data`."""
    info = game_data.BUILDING_BY_ID[int(key[1:])]
    return int(info.get("max_table_level") or len(info.get("levels") or {}))


def last_level_build_time(
    key: str,
    planned_levels: Mapping[str, int],
    speedup_factor: float,
) -> float:
    """Seconds that the last level of building `key` takes at `planned_levels`."""
    level = int(planned_levels.get(key, 0))
    if level <= 0:
        return 0.0
    levels_map = game_data.BUILDING_BY_ID[int(key[1:])].get("levels") or {}
    base_times = build_monotonic_base_times(levels_map, level)
    dev_center_level = max(0, int(planned_levels.get("b7", 0)) - (1 if key == "b7" else 0))
    microsystem_level = max(0, int(planned_levels.get("b17", 0)) - (1 if key == "b17" else 0))
    return float(base_times[level]) / building_time_divisor(dev_center_level, microsystem_level, speedup_factor)


# ---------------------------------------------------------------------------
# Pure-Python Technology Requirement Graph
# ---------------------------------------------------------------------------


class TechnologyRequirementGraph:
    """In-memory DAG of SpaceInvasion prerequisites across buildings, research, ships, and defenses."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self._requirements: dict[str, list[dict[str, Any]]] = {}
        self._base_times: dict[str, dict[int, float]] = {}
        for building_id in sorted(game_data.BUILDING_BY_ID):
            self._add_node(f"b{building_id}", "buildings", game_data.BUILDING_BY_ID[building_id])
        for key, info in research_catalog():
            self._add_node(key, "research", info)
        for key in unit_keys(SHIP_SLUG_BY_KEY):
            self._add_node(key, "ships", game_data.SHIPS[SHIP_SLUG_BY_KEY[key]])
        for key in unit_keys(DEFENSE_SLUG_BY_KEY):
            self._add_node(key, "defense", game_data.DEFENSES[DEFENSE_SLUG_BY_KEY[key]])

        self.prereqs_of, self.unlocks_of = self._link_requirements()
        self.topological_order, self.topological_layer = self._sort_topologically()
        self._topological_rank = {key: rank for rank, key in enumerate(self.topological_order)}
        self.ancestors_of, self.descendants_of = self._close_transitively()

    def _add_node(self, key: str, category: str, info: Mapping[str, Any]) -> None:
        levels = info.get("levels") or {}
        max_level = int(info.get("max_table_level") or len(levels))
        self.nodes[key] = {
            "key": key,
            "category": category,
            "entity_id": int(key[1:]),
            "slug": str(info["slug"]),
            "name": str(info["name"]),
            "max_level": max_level,
        }
        self._requirements[key] = list(info.get("requirements") or [])
        self._base_times[key] = build_monotonic_base_times(levels, max_level)

    def _link_requirements(
        self,
    ) -> tuple[dict[str, tuple[tuple[str, int], ...]], dict[str, tuple[tuple[str, int], ...]]]:
        key_by_slug = {(node["category"], node["slug"]): key for key, node in self.nodes.items()}
        prereqs: dict[str, list[tuple[str, int]]] = {key: [] for key in self.nodes}
        unlocks: dict[str, list[tuple[str, int]]] = {key: [] for key in self.nodes}
        for target, requirements in self._requirements.items():
            for requirement in requirements:
                source = key_by_slug.get((str(requirement["category"]), str(requirement["slug"])))
                if source is None:
                    raise KeyError(f"Unresolved tech-tree requirement {requirement!r} on {target}")
                level = int(requirement["level"])
                prereqs[target].append((source, level))
                unlocks[source].append((target, level))
        return (
            {key: tuple(edges) for key, edges in prereqs.items()},
            {key: tuple(edges) for key, edges in unlocks.items()},
        )

    def _sort_topologically(self) -> tuple[tuple[str, ...], dict[str, int]]:
        open_prereqs = {key: len(self.prereqs_of[key]) for key in self.nodes}
        layer = {key: 0 for key in self.nodes}
        queue: deque[str] = deque(key for key, count in open_prereqs.items() if count == 0)
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for dependent, _level in self.unlocks_of[node]:
                layer[dependent] = max(layer[dependent], layer[node] + 1)
                open_prereqs[dependent] -= 1
                if open_prereqs[dependent] == 0:
                    queue.append(dependent)
        if len(order) != len(self.nodes):
            raise ValueError(f"Cycle detected in technology requirement graph: {sorted(set(self.nodes) - set(order))}")
        return tuple(order), layer

    def _close_transitively(self) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
        ancestors: dict[str, set[str]] = {key: set() for key in self.nodes}
        for node in self.topological_order:
            for prerequisite, _level in self.prereqs_of[node]:
                ancestors[node].add(prerequisite)
                ancestors[node].update(ancestors[prerequisite])
        descendants: dict[str, set[str]] = {key: set() for key in self.nodes}
        for node in reversed(self.topological_order):
            for dependent, _level in self.unlocks_of[node]:
                descendants[node].add(dependent)
                descendants[node].update(descendants[dependent])
        return (
            {key: frozenset(found) for key, found in ancestors.items()},
            {key: frozenset(found) for key, found in descendants.items()},
        )

    def transitive_requirements(
        self,
        target_key: str,
        *,
        include_target_level: int = 0,
    ) -> dict[str, int]:
        if target_key not in self.nodes:
            raise KeyError(f"Unknown node key: {target_key!r}")
        required: dict[str, int] = {}
        if include_target_level > 0 and self.nodes[target_key]["category"] in ("buildings", "research"):
            required[target_key] = int(include_target_level)
        for source, level in self.prereqs_of[target_key]:
            required[source] = max(required.get(source, 0), int(level))
        for node in reversed(self.topological_order):
            if node not in required:
                continue
            for source, level in self.prereqs_of[node]:
                if level > required.get(source, 0):
                    required[source] = int(level)
        return {key: required[key] for key in sorted(required, key=self._topological_rank.__getitem__)}


TECH_GRAPH: TechnologyRequirementGraph = TechnologyRequirementGraph()


def same_planet_building_requirements(key: str, level: int = 0) -> dict[str, int]:
    """Building levels a planet needs before it can hold `key`, following building prereqs only."""
    required: dict[str, int] = {}
    if level > 0 and TECH_GRAPH.nodes[key]["category"] == "buildings":
        required[key] = int(level)
    pending = [key]
    while pending:
        for source, source_level in TECH_GRAPH.prereqs_of[pending.pop()]:
            if TECH_GRAPH.nodes[source]["category"] == "buildings" and source_level > required.get(source, 0):
                required[source] = int(source_level)
                pending.append(source)
    return required


def research_requirements(keys: Iterable[str]) -> dict[str, int]:
    """Highest research level that any of `keys` needs, directly or indirectly."""
    required: dict[str, int] = {}
    for key in keys:
        for node, level in TECH_GRAPH.transitive_requirements(key).items():
            if TECH_GRAPH.nodes[node]["category"] == "research":
                required[node] = max(required.get(node, 0), int(level))
    return required


def largest_costs(
    costs: Iterable[tuple[str, Mapping[str, Any]]],
) -> tuple[dict[str, float], dict[str, str]]:
    """Per resource, the largest amount requested by any labelled cost and its label."""
    amounts = {resource: 0.0 for resource in RESOURCE_KEYS}
    labels = {resource: "" for resource in RESOURCE_KEYS}
    for label, cost in costs:
        for resource in RESOURCE_KEYS:
            amount = float(cost.get(resource, 0) or 0)
            if amount > amounts[resource]:
                amounts[resource], labels[resource] = amount, label
    return amounts, labels


def building_upgrade_costs(key: str, from_level: int, to_level: int) -> list[tuple[str, dict[str, int]]]:
    """`(label, cost)` for every level of building `key` from `from_level + 1` to `to_level`."""
    return [
        (f"{key} L{level}", game_data.building_cost(int(key[1:]), level))
        for level in range(int(from_level) + 1, int(to_level) + 1)
    ]


def research_upgrade_costs(
    own_levels: Mapping[str, int],
    planned_levels: Mapping[str, int],
) -> list[tuple[str, dict[str, Any]]]:
    """`(label, cost)` for every research level between current and planned levels."""
    return [
        (
            f"{key} L{level}",
            ((game_data.RESEARCH_BY_ID[key].get("levels") or {}).get(level) or {}).get("cost") or {},
        )
        for key, planned_level in planned_levels.items()
        for level in range(max(0, int(own_levels.get(key, 0) or 0)) + 1, int(planned_level) + 1)
    ]


def building_upgrade_cost_spice_eq(key: str, from_level: int, to_level: int) -> float:
    """SpiceEq cost of raising building `key` from `from_level` to `to_level`."""
    return float(
        sum(
            game_data.spice_equivalent(game_data.building_cost(int(key[1:]), level))
            for level in range(int(from_level) + 1, int(to_level) + 1)
        )
    )


def research_upgrade_cost_spice_eq(key: str, from_level: int, to_level: int) -> float:
    """SpiceEq cost of raising research `key` from `from_level` to `to_level`."""
    levels = game_data.RESEARCH_BY_ID[key].get("levels") or {}
    return float(
        sum(
            game_data.spice_equivalent(full_cost((levels.get(level) or {}).get("cost")))
            for level in range(int(from_level) + 1, int(to_level) + 1)
        )
    )


# ---------------------------------------------------------------------------
# Planet roster, floors & settings validation
# ---------------------------------------------------------------------------


def _json_ready(value: Any) -> Any:
    """Convert numpy/Python values into strictly finite JSON-safe structures."""
    if isinstance(value, Mapping):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_ready(value.tolist())
    if isinstance(value, np.generic):
        return _json_ready(value.item())
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int)):
        return value
    return str(value)


def _safe_speedup(state: GameState, params: Mapping[str, Any]) -> float:
    for candidate in (getattr(state, "speedup_factor", None), params.get("speedup_factor")):
        if candidate is None or isinstance(candidate, bool):
            continue
        try:
            val = float(candidate)
        except Exception:
            continue
        if math.isfinite(val) and val > 0.0:
            return val
    return 1.0


def normalize_planet_specialization(raw: Any) -> str:
    """Normalize a single planet specialization value into `VALID_PLANET_SPECIALIZATIONS`."""
    if raw is None:
        return "auto"
    norm = str(raw).strip().lower().replace(" ", "_")
    if not norm:
        return "auto"
    mapped = _SPECIALIZATION_ALIASES.get(norm, norm)
    return mapped if mapped in VALID_PLANET_SPECIALIZATIONS else "auto"


def normalize_planet_specializations_map(raw: Any) -> dict[str, str]:
    """Normalize `{planet_key: specialization}` keeping only explicit non-`auto` locks."""
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, str] = {}
    for pk_raw, spec_raw in raw.items():
        pk = str(pk_raw).strip()
        if not pk:
            continue
        spec = normalize_planet_specialization(spec_raw)
        if spec in SPECIALIZATION_ALLOWED_BUILDINGS:
            out[pk] = spec
    return out


def normalize_planet_name(raw: Any) -> str:
    """Sanitize a custom planet display name (control-char free, single-line, max 48 chars)."""
    if raw is None or isinstance(raw, bool):
        return ""
    text = "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in str(raw))
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return ""
    return cleaned[:MAX_PLANET_NAME_LENGTH].strip()


def normalize_planet_names_map(raw: Any) -> dict[str, str]:
    """Normalize `{planet_key: custom_name}` dropping empty keys or blank names."""
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, str] = {}
    for pk_raw, name_raw in raw.items():
        pk = str(pk_raw).strip()
        if not pk:
            continue
        cleaned = normalize_planet_name(name_raw)
        if cleaned:
            out[pk] = cleaned
    return out


def normalize_planet_target_buildings(raw: Any) -> dict[str, int]:
    """Normalize `{b1..b21: level}` clamping normal-planet buildings to `0..catalog_top_level`."""
    out: dict[str, int] = {}
    src = raw if isinstance(raw, Mapping) else {}
    for bid in sorted(game_data.BUILDING_BY_ID):
        bk = f"b{bid}"
        if not is_building_allowed_on_planet_type("normal", bid):
            out[bk] = 0
            continue
        lvl = max(0, game_data.safe_int(src.get(bk, 0), 0))
        out[bk] = min(lvl, catalog_top_level(bk))
    return out


def normalize_manual_planet_configs_map(raw: Any) -> dict[str, dict[str, Any]]:
    """Normalize `{planet_key: {target_buildings, fields_max_override, auto_warehouses}}`."""
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for pk_raw, entry in raw.items():
        pk = str(pk_raw).strip()
        if not pk or not isinstance(entry, Mapping):
            continue
        raw_blds = entry.get("target_buildings") if isinstance(entry.get("target_buildings"), Mapping) else entry
        blds = normalize_planet_target_buildings(raw_blds)
        raw_fm = game_data.safe_optional_int(entry.get("fields_max_override"), None)
        fm_override = max(1, min(2000, int(raw_fm))) if (raw_fm is not None and raw_fm > 0) else None
        out[pk] = {
            "target_buildings": blds,
            "fields_max_override": fm_override,
            "auto_warehouses": bool(entry.get("auto_warehouses", False)),
        }
    return out


def _resolve_manual_planet_config(
    plan: Mapping[str, Any],
    manual_configs: Mapping[str, Any] | None,
) -> Optional[dict[str, Any]]:
    """Match a planet plan against `manual_planet_configs` by `planet_id`, `coords`, or `slot:<n>`."""
    if not isinstance(manual_configs, Mapping) or not manual_configs:
        return None
    pid = int(plan.get("planet_id", 0) or 0)
    pid_str = str(pid) if pid > 0 else ""
    raw_coords = str(plan.get("coords") or "").strip()
    norm_coords = normalize_coords(raw_coords) if raw_coords and not raw_coords.startswith("EMPTY:") else raw_coords
    slot_idx = int(plan.get("slot_index", plan.get("slot", 0)) or 0)
    slot_key = f"slot:{slot_idx}"

    if pid_str and isinstance(manual_configs.get(pid_str), Mapping):
        return dict(manual_configs[pid_str])
    if raw_coords and isinstance(manual_configs.get(raw_coords), Mapping):
        return dict(manual_configs[raw_coords])
    for lk_raw, entry in manual_configs.items():
        if not isinstance(entry, Mapping):
            continue
        lk = str(lk_raw).strip()
        if not lk:
            continue
        if ":" in lk and not lk.startswith("EMPTY:") and not lk.lower().startswith("slot:"):
            if norm_coords and normalize_coords(lk) == norm_coords:
                return dict(entry)
        elif lk.lower() == slot_key:
            return dict(entry)
    return None


def _apply_manual_planet_pins(
    planet_plans: list[dict[str, Any]],
    manual_configs: Mapping[str, Any] | None,
    optimized_keys: Iterable[str],
) -> None:
    """Pin manually configured planets in-place before constructing `EmpireProductionModel`."""
    if not isinstance(manual_configs, Mapping) or not manual_configs:
        return
    opt_set = set(optimized_keys)
    for plan in planet_plans:
        pin = _resolve_manual_planet_config(plan, manual_configs)
        if pin is None:
            continue
        blds = normalize_planet_target_buildings(pin.get("target_buildings"))
        fm_ov = game_data.safe_optional_int(pin.get("fields_max_override"), None)
        if fm_ov is not None and fm_ov > 0:
            plan["fields_max"] = max(int(fm_ov), sum(blds.values()))
        for fk in list(plan["fixed_levels"]):
            if fk in blds:
                plan["fixed_levels"][fk] = int(blds[fk])
        plan["fixed_fields"] = int(sum(plan["fixed_levels"].values()))
        plan["free_fields"] = int(plan["fields_max"]) - int(plan["fixed_fields"])
        for ok in opt_set:
            if ok in blds:
                lvl = int(blds[ok])
                plan["min_levels"][ok] = lvl
                plan["max_levels"][ok] = lvl
        for wk in WAREHOUSE_KEYS:
            if wk in blds and not bool(pin.get("auto_warehouses", False)):
                plan["warehouse_min_levels"][wk] = max(int(plan["warehouse_min_levels"].get(wk, 0)), int(blds[wk]))
                if int(blds[wk]) > 0:
                    plan["warehouse_min_set_by"][wk] = "manual planet config"
        plan["manual_pinned"] = True


def _normalize_params(raw_params: Mapping[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG_OPTIMIZER_PARAMS)
    if isinstance(raw_params, Mapping):
        for raw_key, raw_val in raw_params.items():
            key = _UPPER_PARAM_ALIASES.get(str(raw_key), str(raw_key))
            if raw_val is None and key in merged:
                continue
            if key in ("misc_building_levels", "optimized_building_max_levels") and isinstance(raw_val, Mapping):
                # Allow partial overrides or full replacements cleanly
                if set(raw_val.keys()) == set(merged[key].keys()):
                    merged[key] = {str(k): int(v) for k, v in raw_val.items()}
                else:
                    merged[key] = {**merged[key], **{str(k): int(v) for k, v in raw_val.items()}}
            elif key == "min_fleet_per_planet" and isinstance(raw_val, Mapping):
                merged[key] = {str(k): int(v) for k, v in raw_val.items()}
            elif key == "misc_building_levels_by_slot" and isinstance(raw_val, Mapping):
                merged[key] = {
                    str(slot): {str(bk): int(lvl) for bk, lvl in bmap.items()}
                    for slot, bmap in raw_val.items()
                    if isinstance(bmap, Mapping)
                }
            elif key == "reserved_floors_by_planet" and isinstance(raw_val, Mapping):
                merged[key] = {
                    str(pk): {str(bk): max(0, int(lvl)) for bk, lvl in bmap.items()}
                    for pk, bmap in raw_val.items()
                    if isinstance(bmap, Mapping)
                }
            elif key == "planet_specializations" and isinstance(raw_val, Mapping):
                merged[key] = normalize_planet_specializations_map(raw_val)
            elif key == "planet_names" and isinstance(raw_val, Mapping):
                # Merge cleanly so custom_planet_names + planet_names both apply (later explicit keys win)
                existing_names = merged.get("planet_names") if isinstance(merged.get("planet_names"), dict) else {}
                merged[key] = {**existing_names, **normalize_planet_names_map(raw_val)}
            elif key == "manual_planet_configs" and isinstance(raw_val, Mapping):
                merged[key] = normalize_manual_planet_configs_map(raw_val)
            elif key == "hill_climb_step_sizes" and isinstance(raw_val, (list, tuple)):
                steps = [max(1, int(x)) for x in raw_val]
                merged[key] = steps if steps else [1, 2, 4, 8]
            else:
                merged[key] = raw_val

    merged["target_ship_key"] = str(merged["target_ship_key"])
    merged["production_month_days"] = float(merged["production_month_days"])
    merged["research_percent_of_universe_max"] = float(merged["research_percent_of_universe_max"])
    merged["warehouse_hours_of_production"] = float(merged["warehouse_hours_of_production"])
    merged["min_warehouse_level"] = int(merged.get("min_warehouse_level", 0) or 0)
    merged["central_planet_slot"] = int(merged["central_planet_slot"])
    merged["central_planet_min_warehouse_level"] = int(merged["central_planet_min_warehouse_level"])
    merged["catalogue_grid_size"] = max(16, int(merged["catalogue_grid_size"]))
    merged["catalogue_priority_steps"] = max(1, int(merged["catalogue_priority_steps"]))
    merged["ga_population_size"] = max(2, int(merged["ga_population_size"]))
    merged["ga_generations"] = max(0, int(merged["ga_generations"]))
    merged["ga_tournament_size"] = max(1, int(merged["ga_tournament_size"]))
    merged["ga_crossover_rate"] = float(merged["ga_crossover_rate"])
    merged["ga_mutation_rate"] = float(merged["ga_mutation_rate"])
    merged["ga_elite_count"] = max(1, min(int(merged["ga_elite_count"]), merged["ga_population_size"] - 1))
    merged["ga_random_seed"] = int(merged["ga_random_seed"])
    merged["pad_generic_colonies"] = bool(merged["pad_generic_colonies"])
    merged["allow_demolition"] = bool(merged.get("allow_demolition", False))
    merged["max_planets"] = max(1, int(merged["max_planets"]))
    merged["empty_colony_fields"] = max(1, int(merged["empty_colony_fields"]))
    merged["planet_specializations"] = normalize_planet_specializations_map(merged.get("planet_specializations"))
    merged["planet_names"] = normalize_planet_names_map(merged.get("planet_names"))
    merged["manual_planet_configs"] = normalize_manual_planet_configs_map(merged.get("manual_planet_configs"))
    merged["preserve_previous_specializations"] = bool(merged.get("preserve_previous_specializations", True))
    try:
        raw_thresh = float(merged.get("specialization_switch_threshold_pct", 0.5))
    except Exception:
        raw_thresh = 0.5
    merged["specialization_switch_threshold_pct"] = (
        max(0.0, min(100.0, raw_thresh)) if math.isfinite(raw_thresh) else 0.5
    )
    return merged


def _empty_colony_slot(slot_number: int, fields: int, temperature_c: int = 0) -> PlanetState:
    base_storage = warehouse_capacity(0)
    return PlanetState(
        planet_id=-slot_number,
        name=f"Empty Colony Slot {slot_number}",
        coords=f"EMPTY:{slot_number:02d}",
        planet_type="normal",
        temperature=int(temperature_c),
        fields_used=0,
        fields_max=int(fields),
        resources=Resources(0, 0, 0, 0),
        storage=Resources(base_storage, base_storage, base_storage, base_storage),
        energy=EnergyBalance(0, 0),
        buildings={f"b{building_id}": 0 for building_id in sorted(game_data.BUILDING_BY_ID)},
        ships={},
        defense={},
        economy_rates={},
    )


def _resolve_planet_reserved_floors(
    planet: PlanetState,
    reserved_floors_by_planet: Mapping[str, Mapping[str, int]],
) -> dict[str, int]:
    """Merge reserved floors matching `str(planet.planet_id)`, `planet.coords`, or normalized coords."""
    if not reserved_floors_by_planet:
        return {}
    pid_str = str(int(planet.planet_id))
    raw_coords = str(planet.coords or "").strip()
    norm_coords = normalize_coords(raw_coords) if raw_coords and not raw_coords.startswith("EMPTY:") else raw_coords
    merged: dict[str, int] = {}
    for lookup_key, bmap in reserved_floors_by_planet.items():
        if not isinstance(bmap, Mapping):
            continue
        lk = str(lookup_key).strip()
        lk_norm = normalize_coords(lk) if (":" in lk and not lk.startswith("EMPTY:")) else lk
        if lk == pid_str or (raw_coords and lk == raw_coords) or (norm_coords and lk_norm == norm_coords):
            for bkey, lvl in bmap.items():
                bk = str(bkey).strip()
                if bk.startswith("b") and bk[1:].isdigit() and int(bk[1:]) in game_data.BUILDING_BY_ID:
                    val = max(0, game_data.safe_int(lvl, 0))
                    if val > merged.get(bk, 0):
                        merged[bk] = val
    return merged


def _resolve_planet_specialization(
    planet: PlanetState,
    slot_index: int,
    planet_specializations: Mapping[str, str] | None,
) -> str:
    """Resolve an explicit specialization lock matching `planet_id`, `coords`, or `slot:<n>`."""
    if not planet_specializations:
        return "auto"
    pid = int(planet.planet_id)
    pid_str = str(pid) if pid > 0 else ""
    raw_coords = str(planet.coords or "").strip()
    norm_coords = normalize_coords(raw_coords) if raw_coords and not raw_coords.startswith("EMPTY:") else raw_coords
    slot_key = f"slot:{int(slot_index)}"

    # Priority: exact planet_id > exact coords > normalized coords > slot:<n>
    if pid_str and pid_str in planet_specializations:
        spec = normalize_planet_specialization(planet_specializations[pid_str])
        if spec != "auto":
            return spec
    if raw_coords and raw_coords in planet_specializations:
        spec = normalize_planet_specialization(planet_specializations[raw_coords])
        if spec != "auto":
            return spec
    for lookup_key, raw_spec in planet_specializations.items():
        lk = str(lookup_key).strip()
        if not lk:
            continue
        lk_norm = (
            normalize_coords(lk) if (":" in lk and not lk.startswith("EMPTY:") and not lk.startswith("slot:")) else lk
        )
        if (norm_coords and lk_norm == norm_coords) or lk.lower() == slot_key:
            spec = normalize_planet_specialization(raw_spec)
            if spec != "auto":
                return spec
    return "auto"


def _resolve_planet_display_name(
    planet: PlanetState,
    slot_index: int,
    planet_names: Mapping[str, str] | None,
) -> tuple[str, str]:
    """Resolve `(display_name, game_name)` from `planet_names` override + `PlanetState`.

    Lookup priority for explicit overrides: `planet_id` > exact `coords` >
    normalized `coords` > `slot:<n>`. Falls back cleanly to `planet.name` and
    raw in-game `planet.game_name` (or `planet.coords`).
    """
    raw_coords = str(planet.coords or "").strip()
    state_name = normalize_planet_name(getattr(planet, "name", None))
    state_game_name = normalize_planet_name(getattr(planet, "game_name", None)) or state_name or raw_coords

    override_name = ""
    if planet_names:
        pid = int(planet.planet_id)
        pid_str = str(pid) if pid > 0 else ""
        norm_coords = normalize_coords(raw_coords) if raw_coords and not raw_coords.startswith("EMPTY:") else raw_coords
        slot_key = f"slot:{int(slot_index)}"
        if pid_str and pid_str in planet_names:
            override_name = normalize_planet_name(planet_names[pid_str])
        if not override_name and raw_coords and raw_coords in planet_names:
            override_name = normalize_planet_name(planet_names[raw_coords])
        if not override_name:
            slot_fallback = ""
            for lookup_key, raw_name in planet_names.items():
                lk = str(lookup_key).strip()
                if not lk:
                    continue
                if ":" in lk and not lk.startswith("EMPTY:") and not lk.lower().startswith("slot:"):
                    if norm_coords and normalize_coords(lk) == norm_coords:
                        cand = normalize_planet_name(raw_name)
                        if cand:
                            override_name = cand
                            break
                elif lk.lower() == slot_key and not slot_fallback:
                    cand = normalize_planet_name(raw_name)
                    if cand:
                        slot_fallback = cand
            if not override_name and slot_fallback:
                override_name = slot_fallback

    display_name = override_name or state_name or state_game_name or raw_coords
    return display_name, state_game_name


def check_optimizer_settings(
    optimized_max_levels: Mapping[str, int],
    misc_levels: Mapping[str, int],
    misc_levels_by_slot: Mapping[int, Mapping[str, int]],
    min_fleet: Mapping[str, int],
    target_ship_key: str,
    warehouse_hours: float,
    central_min_warehouse_level: int,
    min_warehouse_level: int = 0,
) -> None:
    """Raise `ValueError` when optimizer parameters contradict `game_data` or each other."""
    building_keys = {f"b{building_id}" for building_id in game_data.BUILDING_BY_ID}
    optimized, fixed = set(optimized_max_levels), set(misc_levels)
    if (optimized | fixed) != building_keys or (optimized & fixed):
        raise ValueError(
            "optimized_building_max_levels and misc_building_levels must list every building exactly once: "
            f"missing {sorted(building_keys - optimized - fixed)}, "
            f"unknown {sorted((optimized | fixed) - building_keys)}, in both {sorted(optimized & fixed)}"
        )
    if optimized & set(WAREHOUSE_KEYS):
        raise ValueError(
            f"Warehouses {sorted(optimized & set(WAREHOUSE_KEYS))} belong in misc_building_levels: "
            "the optimizer sets their level to what each planet needs"
        )
    for slot, levels in misc_levels_by_slot.items():
        if not set(levels) <= fixed:
            raise ValueError(
                f"misc_building_levels_by_slot[{slot}] sets {sorted(set(levels) - fixed)}, "
                "which misc_building_levels does not list"
            )
    if target_ship_key not in SHIP_SLUG_BY_KEY or not set(min_fleet) <= set(SHIP_SLUG_BY_KEY):
        raise ValueError(
            f"target_ship_key and min_fleet_per_planet need valid ship keys: {sorted({target_ship_key, *min_fleet})}"
        )
    counts = [
        *optimized_max_levels.values(),
        *misc_levels.values(),
        *(level for levels in misc_levels_by_slot.values() for level in levels.values()),
        *min_fleet.values(),
        central_min_warehouse_level,
        min_warehouse_level,
    ]
    if any(int(count) != count or count < 0 for count in counts):
        raise ValueError("Building levels and fleet sizes must be whole numbers of 0 or more")
    top_wh = min(catalog_top_level(key) for key in WAREHOUSE_KEYS)
    if int(min_warehouse_level) > top_wh or int(central_min_warehouse_level) > top_wh:
        raise ValueError(f"Warehouse minimum levels cannot exceed catalog top level {top_wh}")
    if not (math.isfinite(warehouse_hours) and warehouse_hours >= 0.0):
        raise ValueError("warehouse_hours_of_production must be a finite number >= 0")
    for key in optimized_max_levels:
        linked = sorted((set(same_planet_building_requirements(key)) & optimized) - {key})
        if linked:
            raise ValueError(
                f"{key} needs {linked} on the same planet, and both are optimized: "
                "move one of them to misc_building_levels"
            )


def plan_research_levels(
    universe_max_levels: Mapping[str, int],
    own_levels: Mapping[str, int],
    required_levels: Mapping[str, int],
    percent_of_universe_max: float,
) -> list[dict[str, Any]]:
    """Compute planned research levels: share of universe max, never below own or required level."""
    rows: list[dict[str, Any]] = []
    pct = max(0.0, float(percent_of_universe_max))
    for key, info in research_catalog():
        catalog_max = int(info.get("max_table_level") or len(info.get("levels") or {}))
        own_level = max(0, int(own_levels.get(key, 0) or 0))
        universe_max = max(own_level, max(0, int(universe_max_levels.get(key, 0) or 0)))
        percent_level = min(catalog_max, int(math.floor(universe_max * pct / 100.0 + 0.5)))
        required_level = max(0, int(required_levels.get(key, 0) or 0))
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


def build_planet_plan(
    *,
    slot_index: int,
    planet: PlanetState,
    is_placeholder: bool,
    optimized_max_levels: Mapping[str, int],
    misc_levels: Mapping[str, int],
    min_fleet: Mapping[str, int],
    reserved_floors: Mapping[str, int],
    min_warehouse_level: int = 0,
    central_min_warehouse_level: Optional[int] = None,
    specialization: str = "auto",
    allow_demolition: bool = False,
    display_name: Optional[str] = None,
    game_name: Optional[str] = None,
) -> dict[str, Any]:
    """Fixed part, built/reserved floors, specialization lock, and search range of one planet."""
    all_keys = (*misc_levels, *optimized_max_levels)
    built = {key: max(0, int(planet.buildings.get(key, 0) or 0)) for key in all_keys}
    reserved = {key: max(0, int(reserved_floors.get(key, 0) or 0)) for key in all_keys}
    reserved_nonzero = {key: lvl for key, lvl in reserved.items() if lvl > 0}
    locked_spec = normalize_planet_specialization(specialization)
    allowed_for_spec = SPECIALIZATION_ALLOWED_BUILDINGS.get(locked_spec)

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

    min_levels: dict[str, int] = {}
    max_levels: dict[str, int] = {}
    locked_by: dict[str, str] = {}
    for key, max_level in optimized_max_levels.items():
        unmet = [
            f"{source} L{level}"
            for source, level in same_planet_building_requirements(key).items()
            if fixed_levels.get(source, level) < level
        ]
        # Always enforce explicit operator `reserved` floors; enforce `built` floors unless `allow_demolition` is enabled.
        hard_floor = reserved[key] if allow_demolition else max(built[key], reserved[key])
        min_levels[key] = max(required.get(key, 0), hard_floor)
        top_allowed = 0 if unmet else min(int(max_level), catalog_top_level(key))
        if allowed_for_spec is not None and key not in allowed_for_spec:
            if allow_demolition and key not in _MINE_BUILDING_TO_RESOURCE:
                # Non-mine off-role structures (b9/b17) cap at built_level and only demolish if fields bind.
                top_allowed = max(min_levels[key], built[key]) if not unmet else min_levels[key]
            else:
                top_allowed = min_levels[key]
        max_levels[key] = max(min_levels[key], top_allowed)
        if unmet and max_levels[key] == 0:
            locked_by[key] = ", ".join(unmet)

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

    stationed = {ship: max(0, int(planet.ships.get(ship, 0) or 0)) for ship in {*min_fleet, "s9"}}
    fleet_missing = {ship: max(0, int(count) - stationed[ship]) for ship, count in min_fleet.items()}
    capacity_needed, capacity_needed_for = largest_costs(
        [
            *(cost for key, level in fixed_levels.items() for cost in building_upgrade_costs(key, built[key], level)),
            *(
                (ship, game_data.SHIPS[SHIP_SLUG_BY_KEY[ship]].get("base_cost") or {})
                for ship, missing in fleet_missing.items()
                if missing > 0
            ),
        ]
    )
    fixed_fields = sum(fixed_levels.values())
    fields_max = max(int(planet.fields_max), sum(built.values()))
    resolved_game_name = (
        normalize_planet_name(game_name)
        or normalize_planet_name(getattr(planet, "game_name", None))
        or normalize_planet_name(getattr(planet, "name", None))
        or str(planet.coords)
    )
    resolved_name = (
        normalize_planet_name(display_name)
        or normalize_planet_name(getattr(planet, "name", None))
        or resolved_game_name
        or str(planet.coords)
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
        "warehouse_min_levels": warehouse_min_levels,
        "warehouse_min_set_by": warehouse_min_set_by,
        "capacity_needed": capacity_needed,
        "capacity_needed_for": capacity_needed_for,
        "is_central_planet": central_min_warehouse_level is not None,
        "pays_for_research": False,
        "solar_cells": max(int(min_fleet.get("s9", 0)), stationed["s9"]),
        "fleet_missing": fleet_missing,
        "fleet_cost_spice_eq": float(
            sum(
                count * game_data.spice_equivalent(full_cost(game_data.SHIPS[SHIP_SLUG_BY_KEY[ship]].get("base_cost")))
                for ship, count in fleet_missing.items()
            )
        ),
    }


def build_planet_plans(
    planets: list[PlanetState],
    *,
    owned_count: int,
    optimized_max_levels: Mapping[str, int],
    misc_levels: Mapping[str, int],
    misc_levels_by_slot: Mapping[int, Mapping[str, int]],
    min_fleet: Mapping[str, int],
    central_planet_slot: int,
    central_min_warehouse_level: int,
    research_costs: list[tuple[str, dict[str, Any]]],
    reserved_floors_by_planet: Mapping[str, Mapping[str, int]],
    min_warehouse_level: int = 0,
    planet_specializations: Mapping[str, str] | None = None,
    allow_demolition: bool = False,
    planet_names: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build per-planet plans for every normal planet in the roster."""
    plans: list[dict[str, Any]] = []
    for slot_index, planet in enumerate(planets, start=1):
        if normalize_planet_type(planet) != "normal":
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
    if not any(plan["is_central_planet"] for plan in plans):
        raise ValueError(
            f"central_planet_slot {central_planet_slot} is not a normal planet of the roster: "
            f"choose one of {[plan['slot_index'] for plan in plans]}"
        )
    lab_planet = max(plans, key=lambda plan: plan["fixed_levels"].get("b8", 0))
    if lab_planet["fixed_levels"].get("b8", 0) > 0:
        lab_planet["capacity_needed"], lab_planet["capacity_needed_for"] = largest_costs(
            [
                *(
                    (lab_planet["capacity_needed_for"][resource], {resource: lab_planet["capacity_needed"][resource]})
                    for resource in RESOURCE_KEYS
                ),
                *research_costs,
            ]
        )
        lab_planet["pays_for_research"] = True
    return plans


# ---------------------------------------------------------------------------
# Vectorized Empire Production Model
# ---------------------------------------------------------------------------


def upgrade_cost_table(key: str, built_level: int, top_level: int) -> np.ndarray:
    """`table[level, resource]`: largest single-level cost from `built_level + 1` to `level`."""
    table = np.zeros((top_level + 1, len(RESOURCE_KEYS)), dtype=np.float64)
    for level in range(int(built_level) + 1, int(top_level) + 1):
        cost = full_cost(game_data.building_cost(int(key[1:]), level))
        table[level] = np.maximum(table[level - 1], [float(cost[resource]) for resource in RESOURCE_KEYS])
    return table


def upgrade_cost_tables(
    planet_plans: list[dict[str, Any]],
    keys: tuple[str, ...],
    top_level: int,
) -> np.ndarray:
    """`tables[planet, building, level, resource]` across all planets and `keys`."""
    tables = {
        (key, built_level): upgrade_cost_table(key, built_level, top_level)
        for key in keys
        for built_level in {plan["built_levels"][key] for plan in planet_plans}
    }
    return np.array([[tables[key, plan["built_levels"][key]] for key in keys] for plan in planet_plans])


class EmpireProductionModel:
    """Vectorized steady-state empire output model matching `config_optimizer.ipynb`."""

    def __init__(
        self,
        planet_plans: list[dict[str, Any]],
        planet_contexts: list[dict[str, Any]],
        *,
        optimized_keys: tuple[str, ...],
        target_ship_key: str,
        month_days: float,
        warehouse_hours: float,
    ) -> None:
        self.optimized_keys = tuple(optimized_keys)
        self.target_ship_key = target_ship_key
        self.building_index = {key: index for index, key in enumerate(self.optimized_keys)}
        self.planet_names = [
            (
                f"{plan['name']} [{plan['coords']}] (slot {plan['slot_index']})"
                if plan.get("name")
                and str(plan["name"]).strip()
                and str(plan["name"]).strip() != str(plan["coords"]).strip()
                else f"{plan['coords']} (slot {plan['slot_index']})"
            )
            for plan in planet_plans
        ]
        self.min_levels = np.array(
            [[plan["min_levels"][key] for key in self.optimized_keys] for plan in planet_plans],
            dtype=np.int64,
        )
        self.max_levels = np.array(
            [[plan["max_levels"][key] for key in self.optimized_keys] for plan in planet_plans],
            dtype=np.int64,
        )
        self.built_opt_levels = np.array(
            [[plan["built_levels"][key] for key in self.optimized_keys] for plan in planet_plans],
            dtype=np.int64,
        )
        self.fields_max = np.array([plan["fields_max"] for plan in planet_plans], dtype=np.int64)
        self.free_fields = np.array([plan["free_fields"] for plan in planet_plans], dtype=np.int64)
        self.fixed_levels = {
            key: np.array([plan["fixed_levels"][key] for plan in planet_plans], dtype=np.int64)
            for key in planet_plans[0]["fixed_levels"]
        }

        top_level = int(self.max_levels.max()) if self.max_levels.size else 0
        self.flow_tables = np.array(
            [
                [
                    [building_level_flows(key, level, context) for level in range(top_level + 1)]
                    for key in self.optimized_keys
                ]
                for context in planet_contexts
            ],
            dtype=np.float64,
        ).transpose(3, 0, 1, 2)

        self.fixed_flows = np.zeros((len(PRODUCTION_FLOW_KEYS), len(planet_plans)), dtype=np.float64)
        for planet, (plan, context) in enumerate(zip(planet_plans, planet_contexts)):
            for key, level in plan["fixed_levels"].items():
                self.fixed_flows[:, planet] += building_level_flows(key, level, context)
            self.fixed_flows[PRODUCTION_FLOW_KEYS.index("energy_produced"), planet] += (
                plan["solar_cells"]
                * solar_cell_energy_per_unit(context["planet_temperature_c"])
                * technology_multiplier("r2", context["r2_energy_tech"])
            )

        self.warehouse_hours = float(warehouse_hours)
        self.top_warehouse_level = min(catalog_top_level(key) for key in WAREHOUSE_KEYS)
        self.warehouse_capacities = np.array(
            [warehouse_capacity(level) for level in range(self.top_warehouse_level + 1)],
            dtype=np.float64,
        )
        self.min_warehouse_levels = np.array(
            [[plan["warehouse_min_levels"][key] for key in WAREHOUSE_KEYS] for plan in planet_plans],
            dtype=np.int64,
        )
        self.capacity_needed = np.array(
            [[plan["capacity_needed"][resource] for resource in RESOURCE_KEYS] for plan in planet_plans],
            dtype=np.float64,
        )
        self.cost_tables = upgrade_cost_tables(planet_plans, self.optimized_keys, top_level)
        self.warehouse_cost_tables = upgrade_cost_tables(planet_plans, WAREHOUSE_KEYS, self.top_warehouse_level)
        self.central_planet = next(
            (planet for planet, plan in enumerate(planet_plans) if plan["is_central_planet"]), -1
        )
        self.central_reserve = np.zeros(len(RESOURCE_KEYS), dtype=np.float64)

        ship = game_data.SHIPS[SHIP_SLUG_BY_KEY[target_ship_key]]
        cost = full_cost(ship.get("base_cost"))
        self.cost_resources = tuple(resource for resource in RESOURCE_KEYS if float(cost[resource]) > 0.0)
        self.ship_cost = np.array([float(cost[resource]) for resource in self.cost_resources], dtype=np.float64)
        self.ship_capacity = np.array([float(cost[resource]) for resource in RESOURCE_KEYS], dtype=np.float64)
        self.limit_names = (*self.cost_resources, "shipyards")
        self.target_requirements = same_planet_building_requirements(target_ship_key)
        self.hours_per_month = 24.0 * float(month_days)
        factory_levels = max(top_level, int(self.fixed_levels.get("b9", np.zeros(1)).max())) + 1
        microsystem_levels = max(top_level, int(self.fixed_levels.get("b17", np.zeros(1)).max())) + 1
        speedup = float(planet_contexts[0]["speedup_factor"])
        seconds_per_month = 3600.0 * self.hours_per_month
        self.shipyard_ships = np.array(
            [
                [
                    seconds_per_month
                    * shipyard_time_divisor(factory, microsystem, speedup)
                    / float(ship["base_build_time_s"])
                    for microsystem in range(microsystem_levels)
                ]
                for factory in range(factory_levels)
            ],
            dtype=np.float64,
        )

        signatures = [
            (
                self.flow_tables[:, planet].tobytes(),
                self.fixed_flows[:, planet].tobytes(),
                self.min_levels[planet].tobytes(),
                self.max_levels[planet].tobytes(),
                self.built_opt_levels[planet].tobytes(),
                int(self.free_fields[planet]),
                tuple(int(levels[planet]) for levels in self.fixed_levels.values()),
                self.min_warehouse_levels[planet].tobytes(),
                self.capacity_needed[planet].tobytes(),
                self.cost_tables[planet].tobytes(),
                self.warehouse_cost_tables[planet].tobytes(),
                planet == self.central_planet,
            )
            for planet in range(len(planet_plans))
        ]
        self.twin_planets = [signatures.index(signature) for signature in signatures]
        self.check_minimum_designs()

    def check_minimum_designs(self) -> None:
        outcome = self.simulate(self.min_levels[None])
        overfull = np.flatnonzero(~outcome["planet_fits"][0])
        if len(overfull) > 0:
            planet = int(overfull[0])
            if (outcome["planet_warehouse_levels"][0, planet] > self.top_warehouse_level).any():
                raise ValueError(
                    f"Planet {self.planet_names[planet]} would need a warehouse beyond level "
                    f"{self.top_warehouse_level}: lower warehouse_hours_of_production"
                )
            fixed_fields = int(self.fields_max[planet] - self.free_fields[planet])
            raise ValueError(
                f"Planet {self.planet_names[planet]} has {self.fields_max[planet]} fields, but its fixed/built "
                f"floors, minimum levels, and warehouses need {fixed_fields + int(outcome['planet_fields_needed'][0, planet])}: "
                "lower misc_building_levels, min_warehouse_level, central_planet_min_warehouse_level, or warehouse_hours_of_production"
            )

    def with_central_reserve(self, capacity: np.ndarray) -> EmpireProductionModel:
        model = copy.copy(self)
        model.central_reserve = np.asarray(capacity, dtype=np.float64)
        model.check_minimum_designs()
        return model

    def planet_index(self, planets: Iterable[int] | None) -> np.ndarray:
        return np.arange(len(self.free_fields)) if planets is None else np.asarray(list(planets), dtype=np.int64)

    def building_levels(self, key: str, levels: np.ndarray, planets: np.ndarray) -> np.ndarray:
        if key in self.building_index:
            return levels[:, :, self.building_index[key]]
        return np.broadcast_to(self.fixed_levels[key][planets], levels.shape[:2])

    def warehouse_levels_for(self, capacity: np.ndarray) -> np.ndarray:
        return np.searchsorted(self.warehouse_capacities, capacity)

    def simulate(self, levels: np.ndarray, planets: Iterable[int] | None = None) -> dict[str, np.ndarray]:
        planets_idx = self.planet_index(planets)
        planet_axis = planets_idx[None, :, None]
        building_axis = np.arange(levels.shape[2])[None, None, :]
        totals = self.fixed_flows[:, None, planets_idx] + self.flow_tables[:, planet_axis, building_axis, levels].sum(
            axis=3
        )
        flows = dict(zip(PRODUCTION_FLOW_KEYS, totals))

        power_ratio = np.ones_like(flows["energy_used"])
        np.divide(flows["energy_produced"], flows["energy_used"], out=power_ratio, where=flows["energy_used"] > 0.0)
        power_ratio = np.minimum(1.0, power_ratio)

        pig_iron_mined = flows["pig_iron"] * power_ratio
        pig_iron_wanted = flows["pig_iron_used"] * power_ratio
        smelting_ratio = np.ones_like(pig_iron_wanted)
        np.divide(pig_iron_mined, pig_iron_wanted, out=smelting_ratio, where=pig_iron_wanted > 0.0)
        smelting_ratio = np.minimum(1.0, smelting_ratio)

        net = {
            "pig_iron": pig_iron_mined - pig_iron_wanted * smelting_ratio,
            "metal": flows["metal"] * power_ratio * smelting_ratio,
            "kryptonite": flows["kryptonite"] * power_ratio,
            "spice": flows["spice"] * power_ratio - flows["spice_used"],
        }
        net_per_hour = np.stack([net[resource] for resource in RESOURCE_KEYS], axis=2)

        can_build = np.ones(levels.shape[:2], dtype=bool)
        for key, required_level in self.target_requirements.items():
            can_build &= self.building_levels(key, levels, planets_idx) >= required_level
        shipyard_ships = np.where(
            can_build,
            self.shipyard_ships[
                self.building_levels("b9", levels, planets_idx),
                self.building_levels("b17", levels, planets_idx),
            ],
            0.0,
        )
        planet_limits = np.stack(
            [
                *(
                    net[resource] * self.hours_per_month / cost
                    for resource, cost in zip(self.cost_resources, self.ship_cost)
                ),
                shipyard_ships,
            ],
            axis=2,
        )

        capacity = np.maximum(self.warehouse_hours * np.maximum(net_per_hour, 0.0), self.capacity_needed[planets_idx])
        capacity = np.maximum(capacity, self.cost_tables[planet_axis, building_axis, levels].max(axis=2))
        capacity = np.maximum(capacity, np.where(can_build[:, :, None], self.ship_capacity, 0.0))
        capacity = np.maximum(
            capacity, np.where((planets_idx == self.central_planet)[:, None], self.central_reserve, 0.0)
        )
        warehouses = np.maximum(self.warehouse_levels_for(capacity), self.min_warehouse_levels[planets_idx])
        warehouse_axis = np.arange(len(WAREHOUSE_KEYS))[None, None, :]
        while True:
            own_cost = self.warehouse_cost_tables[
                planet_axis, warehouse_axis, np.minimum(warehouses, self.top_warehouse_level)
            ].max(axis=2)
            raised = np.maximum(warehouses, self.warehouse_levels_for(own_cost))
            if (raised == warehouses).all():
                break
            warehouses = raised

        fields_needed = levels.sum(axis=2) + warehouses.sum(axis=2)
        return {
            "planet_limits": planet_limits,
            "planet_net_per_hour": net_per_hour,
            "planet_energy_produced": flows["energy_produced"],
            "planet_energy_used": flows["energy_used"],
            "planet_power_ratio": power_ratio,
            "planet_smelting_ratio": smelting_ratio,
            "planet_warehouse_levels": warehouses,
            "planet_fields_needed": fields_needed,
            "planet_fits": (fields_needed <= self.free_fields[planets_idx])
            & (warehouses <= self.top_warehouse_level).all(axis=2),
        }

    def ships_per_month(self, levels: np.ndarray) -> np.ndarray:
        return self.simulate(levels)["planet_limits"].sum(axis=1).min(axis=1)

    def in_range(self, levels: np.ndarray, planets: Iterable[int] | None = None) -> np.ndarray:
        planets_idx = self.planet_index(planets)
        return ((levels >= self.min_levels[planets_idx]) & (levels <= self.max_levels[planets_idx])).all(axis=(1, 2))

    def is_feasible(self, levels: np.ndarray, planets: Iterable[int] | None = None) -> np.ndarray:
        planets_idx = self.planet_index(planets)
        inside = np.clip(levels, self.min_levels[planets_idx], self.max_levels[planets_idx])
        return self.in_range(levels, planets_idx) & self.simulate(inside, planets_idx)["planet_fits"].all(axis=1)


# ---------------------------------------------------------------------------
# Design Catalogues, Hill-Climb, Genetic Algorithm & Polish
# ---------------------------------------------------------------------------


def balanced_score(limits: np.ndarray, sharpness: float = 8.0) -> np.ndarray:
    """Power-mean smooth stand-in for the smallest limit of each row."""
    floor = np.maximum(limits.min(axis=1, keepdims=True), 1e-9)
    return floor[:, 0] * np.mean((np.maximum(limits, 1e-9) / floor) ** -sharpness, axis=1) ** (-1.0 / sharpness)


def smallest_limit(limits: np.ndarray) -> np.ndarray:
    """Smallest limit of each row: target ships per month."""
    return limits.min(axis=1)


def planet_moves(buildings: int, step_sizes: tuple[int, ...]) -> np.ndarray:
    """Level-change move set tried during hill-climb."""
    moves: set[tuple[int, ...]] = set()
    for size in step_sizes:
        for first in range(buildings):
            for first_step in (size, -size):
                moves.add(tuple(first_step if index == first else 0 for index in range(buildings)))
                for second in range(first + 1, buildings):
                    for second_step in (size, -size):
                        moves.add(
                            tuple(
                                first_step if index == first else second_step if index == second else 0
                                for index in range(buildings)
                            )
                        )
            for second in range(first + 1, buildings):
                for third in range(buildings):
                    if third not in (first, second):
                        moves.add(
                            tuple(
                                size if index in (first, second) else -2 * size if index == third else 0
                                for index in range(buildings)
                            )
                        )
    return np.array(sorted(moves), dtype=np.int64)


def improve_design(
    model: EmpireProductionModel,
    planet: int,
    design: np.ndarray,
    score: Callable[[np.ndarray], np.ndarray],
    moves: np.ndarray,
) -> np.ndarray:
    """NaN-safe hill-climb on one planet's design until no feasible neighbour improves rank."""
    current = np.asarray(design, dtype=np.int64)
    built_vec = model.built_opt_levels[planet]
    current_score = float(score(model.simulate(current[None, None, :], [planet])["planet_limits"][:, 0])[0])
    current_rank = (current_score, -int(np.abs(current - built_vec).sum()), -int(current.sum()))
    while True:
        candidates = current + moves
        candidates = candidates[model.in_range(candidates[:, None, :], [planet])]
        if len(candidates) == 0:
            return current
        outcome = model.simulate(candidates[:, None, :], [planet])
        fits = outcome["planet_fits"][:, 0]
        if not fits.any():
            return current
        candidates = candidates[fits]
        scores = np.asarray(score(outcome["planet_limits"][fits, 0]), dtype=np.float64)
        finite_mask = np.isfinite(scores)
        if not finite_mask.any():
            return current
        if not finite_mask.all():
            candidates = candidates[finite_mask]
            scores = scores[finite_mask]
        change_totals = np.abs(candidates - built_vec).sum(axis=1)
        level_totals = candidates.sum(axis=1)
        best = int(np.lexsort((-level_totals, -change_totals, scores))[-1])
        best_rank = (float(scores[best]), -int(change_totals[best]), -int(level_totals[best]))
        if not best_rank > current_rank:
            return current
        current, current_rank = candidates[best], best_rank


def coarse_designs(model: EmpireProductionModel, planet: int, grid_size: int) -> tuple[np.ndarray, np.ndarray]:
    """Coarse grid of at most ~`grid_size` feasible designs on one planet and their limits."""
    lower, upper = model.min_levels[planet], model.max_levels[planet]
    active_dims = max(1, int((upper > lower).sum()))
    values_per_building = max(2, int(grid_size ** (1.0 / active_dims)))
    axes = [
        np.unique(np.linspace(low, high, values_per_building).round().astype(np.int64))
        for low, high in zip(lower, upper)
    ]
    grid = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, len(lower))
    outcome = model.simulate(grid[:, None, :], [planet])
    fits = outcome["planet_fits"][:, 0]
    feas_grid = grid[fits]
    feas_limits = outcome["planet_limits"][fits, 0]

    # On locked/constrained planets where `active_dims < len(lower)` (or empty feasible grid),
    # ensure feasible shipyard prerequisite seeds (`b9 >= req_lvl`) are included when tight field
    # slack caused `[low..high]` spacing to jump over the narrow feasible shipyard window.
    if len(feas_limits) == 0 or (active_dims < len(lower) and float(feas_limits[:, -1].max()) <= 0.0):
        seed = lower.copy()
        prereq_ok = True
        for key, req_lvl in model.target_requirements.items():
            if key in model.building_index:
                b_idx = model.building_index[key]
                if int(upper[b_idx]) < int(req_lvl):
                    prereq_ok = False
                    break
                seed[b_idx] = max(int(seed[b_idx]), int(req_lvl))
            elif int(model.fixed_levels.get(key, np.zeros(len(model.free_fields), dtype=np.int64))[planet]) < int(
                req_lvl
            ):
                prereq_ok = False
                break
        if prereq_ok:
            extra_rows: list[np.ndarray] = [seed]
            if "b17" in model.building_index:
                b17_idx = model.building_index["b17"]
                max_slack = max(0, int(model.free_fields[planet]) - int(seed.sum()))
                b17_hi = min(int(upper[b17_idx]), int(seed[b17_idx]) + max_slack)
                for b17_lvl in range(int(seed[b17_idx]) + 1, b17_hi + 1):
                    cand = seed.copy()
                    cand[b17_idx] = b17_lvl
                    extra_rows.append(cand)
            extra_arr = np.array(extra_rows, dtype=np.int64)
            extra_out = model.simulate(extra_arr[:, None, :], [planet])
            extra_fits = extra_out["planet_fits"][:, 0]
            if extra_fits.any():
                feas_grid = np.concatenate([feas_grid, extra_arr[extra_fits]], axis=0)
                feas_limits = np.concatenate([feas_limits, extra_out["planet_limits"][extra_fits, 0]], axis=0)
    return feas_grid, feas_limits


def resource_peaks(
    model: EmpireProductionModel,
    planet: int,
    grid: np.ndarray,
    grid_limits: np.ndarray,
    moves: np.ndarray,
) -> np.ndarray:
    """Maximum contribution of one planet to each resource limit when specialized."""
    peaks: list[float] = []
    for column in range(len(model.cost_resources)):

        def this_limit(limits: np.ndarray, column: int = column) -> np.ndarray:
            return limits[:, column]

        design = improve_design(model, planet, grid[int(grid_limits[:, column].argmax())], this_limit, moves)
        peaks.append(float(model.simulate(design[None, None, :], [planet])["planet_limits"][0, 0, column]))
    return np.array(peaks, dtype=np.float64)


def planet_design_catalogue(
    model: EmpireProductionModel,
    planet: int,
    grid: np.ndarray,
    grid_limits: np.ndarray,
    moves: np.ndarray,
    *,
    priority_steps: int,
    shipyard_ceiling: float,
) -> np.ndarray:
    """Candidate Pareto-front designs of one planet across priority mixes + balanced design."""
    ceiling = np.full(grid_limits.shape[1], np.inf)
    ceiling[-1] = shipyard_ceiling
    scale = np.maximum(np.minimum(grid_limits, ceiling).max(axis=0), 1e-12)

    shares = np.stack(np.meshgrid(*[np.arange(priority_steps + 1)] * len(scale), indexing="ij"), axis=-1)
    shares = shares.reshape(-1, len(scale))
    designs: list[np.ndarray] = []
    for priorities in shares[shares.sum(axis=1) == priority_steps] / float(priority_steps):

        def priority_score(limits: np.ndarray, priorities: np.ndarray = priorities) -> np.ndarray:
            return (np.minimum(limits, ceiling) / scale) @ priorities

        start = grid[int(priority_score(grid_limits).argmax())]
        designs.append(improve_design(model, planet, start, priority_score, moves))
    start = grid[int(balanced_score(grid_limits).argmax())]
    designs.append(improve_design(model, planet, start, balanced_score, moves))
    return np.unique(np.array(designs, dtype=np.int64), axis=0)


def build_design_catalogues(
    model: EmpireProductionModel,
    *,
    grid_size: int,
    priority_steps: int,
    step_sizes: tuple[int, ...],
    progress_cb: Optional[Callable[[str, float], None]] = None,
    progress_range: tuple[float, float] = (0.1, 0.5),
) -> list[np.ndarray]:
    """Build one design catalogue per planet, deduplicating identical twin planets."""
    moves = planet_moves(len(model.optimized_keys), step_sizes)
    unique_planets = [planet for planet, twin in enumerate(model.twin_planets) if twin == planet]
    grids = {planet: coarse_designs(model, planet, grid_size) for planet in unique_planets}
    peaks = {planet: resource_peaks(model, planet, *grid, moves) for planet, grid in grids.items()}
    shipyard_ceiling = float(np.sum([peaks[twin] for twin in model.twin_planets], axis=0).min())
    catalogues: dict[int, np.ndarray] = {}
    p0, p1 = progress_range
    total_unique = max(1, len(unique_planets))
    for idx, planet in enumerate(unique_planets):
        catalogues[planet] = planet_design_catalogue(
            model,
            planet,
            *grids[planet],
            moves,
            priority_steps=priority_steps,
            shipyard_ceiling=shipyard_ceiling,
        )
        if progress_cb is not None:
            progress_cb("building_catalogues", p0 + (p1 - p0) * (idx + 1) / total_unique)
    return [catalogues[twin] for twin in model.twin_planets]


def catalogue_limits(model: EmpireProductionModel, catalogues: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """`design_limits[planet, design, limit]` of every catalogue design and count per planet."""
    design_counts = np.array([len(catalogue) for catalogue in catalogues], dtype=np.int64)
    design_limits = np.zeros((len(catalogues), int(design_counts.max()), len(model.limit_names)), dtype=np.float64)
    for planet, catalogue in enumerate(catalogues):
        limits = model.simulate(catalogue[:, None, :], [planet])["planet_limits"][:, 0]
        design_limits[planet, : len(catalogue)] = limits
    return design_limits, design_counts


def run_genetic_algorithm(
    design_limits: np.ndarray,
    design_counts: np.ndarray,
    *,
    population_size: int,
    generations: int,
    tournament_size: int,
    crossover_rate: float,
    mutation_rate: float,
    elite_count: int,
    seed: int,
    progress_cb: Optional[Callable[[str, float], None]] = None,
    progress_range: tuple[float, float] = (0.5, 0.8),
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Evolve catalogue-design choices across planets; return `(best_choice, history_rows)`."""
    rng = np.random.default_rng(int(seed))
    planets = np.arange(len(design_counts))
    population = rng.integers(0, design_counts, size=(population_size, len(design_counts)))
    history: list[dict[str, Any]] = []
    p0, p1 = progress_range
    stride = max(1, generations // 15) if generations > 0 else 1
    ranking = np.arange(population_size)
    for generation in range(generations + 1):
        fitness = design_limits[planets, population].sum(axis=1).min(axis=1)
        ranking = np.argsort(-fitness, kind="stable")
        history.append(
            {
                "generation": generation,
                "best_ships_per_month": float(fitness[ranking[0]]),
                "mean_ships_per_month": float(fitness.mean()),
                "worst_ships_per_month": float(fitness[ranking[-1]]),
            }
        )
        if progress_cb is not None and (generation % stride == 0 or generation == generations):
            frac = (generation / float(max(1, generations))) if generations > 0 else 1.0
            progress_cb("evolving_ga", p0 + (p1 - p0) * frac)
        if generation == generations:
            break
        contenders = rng.integers(0, population_size, size=(2 * population_size, tournament_size))
        winners = contenders[np.arange(2 * population_size), fitness[contenders].argmax(axis=1)]
        mothers, fathers = population[winners[:population_size]], population[winners[population_size:]]
        from_father = (rng.random(mothers.shape) < 0.5) & (rng.random((population_size, 1)) < crossover_rate)
        children = np.where(from_father, fathers, mothers)
        mutated = rng.random(children.shape) < mutation_rate
        children = np.where(mutated, rng.integers(0, design_counts, size=children.shape), children)
        population = np.concatenate([population[ranking[:elite_count]], children[: population_size - elite_count]])
    return population[ranking[0]].copy(), history


def polish_configuration(
    model: EmpireProductionModel,
    levels: np.ndarray,
    score: Callable[[np.ndarray], np.ndarray],
    step_sizes: tuple[int, ...],
) -> np.ndarray:
    """Coordinate-ascent hill-climb over the whole empire, one planet at a time."""
    moves = planet_moves(levels.shape[1], step_sizes)
    levels = np.array(levels, dtype=np.int64)
    planet_limits = model.simulate(levels[None])["planet_limits"][0]
    changed = True
    while changed:
        changed = False
        for planet in range(len(levels)):
            others = planet_limits.sum(axis=0) - planet_limits[planet]

            def empire_score(own: np.ndarray, others: np.ndarray = others) -> np.ndarray:
                return score(others + own)

            design = improve_design(model, planet, levels[planet], empire_score, moves)
            if (design != levels[planet]).any():
                levels[planet] = design
                planet_limits[planet] = model.simulate(design[None, None, :], [planet])["planet_limits"][0, 0]
                changed = True
    return levels


def polish_both_ways(
    model: EmpireProductionModel,
    levels: np.ndarray,
    step_sizes: tuple[int, ...],
) -> np.ndarray:
    """Polish directly and via `balanced_score`, keeping whichever builds more ships (fewer level changes on ties)."""
    direct = polish_configuration(model, levels, smallest_limit, step_sizes)
    rebalanced = polish_configuration(
        model,
        polish_configuration(model, levels, balanced_score, step_sizes),
        smallest_limit,
        step_sizes,
    )
    ships = model.ships_per_month(np.array([direct, rebalanced]))
    direct_changes = int(np.abs(direct - model.built_opt_levels).sum())
    rebal_changes = int(np.abs(rebalanced - model.built_opt_levels).sum())
    return (
        rebalanced
        if (ships[1], -rebal_changes, -int(rebalanced.sum())) > (ships[0], -direct_changes, -int(direct.sum()))
        else direct
    )


def search_best_configuration(
    model: EmpireProductionModel,
    *,
    grid_size: int,
    priority_steps: int,
    step_sizes: tuple[int, ...],
    progress_cb: Optional[Callable[[str, float], None]] = None,
    progress_range: tuple[float, float] = (0.1, 0.9),
    **ga_settings: Any,
) -> tuple[list[np.ndarray], np.ndarray, list[dict[str, Any]], np.ndarray, np.ndarray]:
    """One search round: design catalogues -> GA -> polish_both_ways."""
    p0, p1 = progress_range
    p_cat_end = p0 + 0.45 * (p1 - p0)
    p_ga_end = p0 + 0.80 * (p1 - p0)
    catalogues = build_design_catalogues(
        model,
        grid_size=grid_size,
        priority_steps=priority_steps,
        step_sizes=step_sizes,
        progress_cb=progress_cb,
        progress_range=(p0, p_cat_end),
    )
    ga_designs, ga_history = run_genetic_algorithm(
        *catalogue_limits(model, catalogues),
        progress_cb=progress_cb,
        progress_range=(p_cat_end, p_ga_end),
        **ga_settings,
    )
    if progress_cb is not None:
        progress_cb("polishing", p_ga_end)
    ga_levels = np.array([catalogues[planet][design] for planet, design in enumerate(ga_designs)], dtype=np.int64)
    polished = polish_both_ways(model, ga_levels, step_sizes)
    if progress_cb is not None:
        progress_cb("polishing", p1)
    return catalogues, ga_designs, ga_history, ga_levels, polished


def central_reserve_needed(model: EmpireProductionModel, levels: np.ndarray) -> np.ndarray:
    """Capacity per resource holding `warehouse_hours` of whole-empire net hourly production."""
    net_per_hour = model.simulate(levels[None])["planet_net_per_hour"][0].sum(axis=0)
    return model.warehouse_hours * np.maximum(net_per_hour, 0.0)


def _polish_holding_central_reserve(
    model: EmpireProductionModel,
    levels: np.ndarray,
    step_sizes: tuple[int, ...],
) -> np.ndarray:
    """Coordinate-ascent over the empire enforcing `needed <= held` on `model.central_planet`."""
    cp = model.central_planet
    if cp < 0 or model.warehouse_hours <= 0.0:
        return levels
    moves = planet_moves(levels.shape[1], step_sizes)
    cur = np.array(levels, dtype=np.int64, copy=True)
    sim0 = model.simulate(cur[None])
    p_limits = sim0["planet_limits"][0].copy()
    p_net = sim0["planet_net_per_hour"][0].copy()
    p_wh = sim0["planet_warehouse_levels"][0].copy()

    changed = True
    while changed:
        changed = False
        for planet in range(len(cur)):
            other_limits = p_limits.sum(axis=0) - p_limits[planet]
            other_net = p_net.sum(axis=0) - p_net[planet]
            cp_wh_fixed = p_wh[cp].copy()
            built_vec = model.built_opt_levels[planet]

            cands = cur[planet] + moves
            cands = cands[model.in_range(cands[:, None, :], [planet])]
            if len(cands) == 0:
                continue
            out = model.simulate(cands[:, None, :], [planet])
            fits = out["planet_fits"][:, 0]
            if not fits.any():
                continue
            cands = cands[fits]
            own_limits = out["planet_limits"][fits, 0]
            own_net = out["planet_net_per_hour"][fits, 0]
            own_wh = out["planet_warehouse_levels"][fits, 0]

            tot_limits = other_limits[None, :] + own_limits
            tot_net = other_net[None, :] + own_net
            needed = model.warehouse_hours * np.maximum(tot_net, 0.0)
            held = (
                model.warehouse_capacities[own_wh] if planet == cp else model.warehouse_capacities[cp_wh_fixed][None, :]
            )
            excess = np.maximum(0.0, needed - held)
            rel_excess = (excess / np.maximum(held, 1.0)).sum(axis=1)
            ships = tot_limits.min(axis=1)
            valid = rel_excess <= 1e-12
            scores = np.where(valid, ships, -1e12 * rel_excess + 1e-6 * ships)
            finite_mask = np.isfinite(scores)
            if not finite_mask.any():
                continue
            if not finite_mask.all():
                cands = cands[finite_mask]
                own_limits = own_limits[finite_mask]
                own_net = own_net[finite_mask]
                own_wh = own_wh[finite_mask]
                scores = scores[finite_mask]

            change_totals = np.abs(cands - built_vec).sum(axis=1)
            lvl_tot = cands.sum(axis=1)

            cur_tot_net = other_net + p_net[planet]
            cur_needed = model.warehouse_hours * np.maximum(cur_tot_net, 0.0)
            cur_held = model.warehouse_capacities[p_wh[cp]]
            cur_rel_ex = float((np.maximum(0.0, cur_needed - cur_held) / np.maximum(cur_held, 1.0)).sum())
            cur_ships = float((other_limits + p_limits[planet]).min())
            cur_score = cur_ships if cur_rel_ex <= 1e-12 else (-1e12 * cur_rel_ex + 1e-6 * cur_ships)
            cur_rank = (cur_score, -int(np.abs(cur[planet] - built_vec).sum()), -int(cur[planet].sum()))

            best = int(np.lexsort((-lvl_tot, -change_totals, scores))[-1])
            best_rank = (float(scores[best]), -int(change_totals[best]), -int(lvl_tot[best]))
            if best_rank > cur_rank:
                cur[planet] = cands[best]
                p_limits[planet] = own_limits[best]
                p_net[planet] = own_net[best]
                p_wh[planet] = own_wh[best]
                changed = True
    return cur


def _finalize_self_consistent_round(
    base_model: EmpireProductionModel,
    round_model: EmpireProductionModel,
    result: tuple[Any, ...],
    step_sizes: tuple[int, ...],
) -> Optional[tuple[EmpireProductionModel, tuple[Any, ...], float, np.ndarray, np.ndarray]]:
    """Polish `result` so `needed <= held`, tighten central warehouses to `needed`, and re-polish freed fields."""
    cats, gd, gh, gl, pol = result
    pol_h = _polish_holding_central_reserve(round_model, pol, step_sizes)
    for _ in range(3):
        needed_h = central_reserve_needed(round_model, pol_h)
        try:
            tight_model = base_model.with_central_reserve(needed_h)
        except ValueError:
            return None
        sim_h = tight_model.simulate(pol_h[None])
        if not bool(sim_h["planet_fits"].all()):
            return None
        pol_next = _polish_holding_central_reserve(tight_model, pol_h, step_sizes)
        if np.array_equal(pol_next, pol_h):
            pol_h = pol_next
            break
        pol_h = pol_next
        round_model = tight_model
    needed_final = central_reserve_needed(base_model, pol_h)
    try:
        final_model = base_model.with_central_reserve(needed_final)
    except ValueError:
        return None
    sim_f = final_model.simulate(pol_h[None])
    if not bool(sim_f["planet_fits"].all()):
        return None
    cp = final_model.central_planet
    held_final = (
        final_model.warehouse_capacities[sim_f["planet_warehouse_levels"][0, cp]]
        if cp >= 0
        else np.full(len(RESOURCE_KEYS), np.inf)
    )
    if (needed_final > held_final).any():
        return None
    ships_final = float(sim_f["planet_limits"][0].sum(axis=0).min())
    return final_model, (cats, gd, gh, gl, pol_h), ships_final, needed_final, held_final


def search_in_rounds(
    model: EmpireProductionModel,
    *,
    progress_cb: Optional[Callable[[str, float], None]] = None,
    **search_settings: Any,
) -> tuple[Any, ...]:
    """Repeat search rounds until the central planet's warehouses hold empire production without overshoot slack."""
    reserve = np.zeros(len(RESOURCE_KEYS), dtype=np.float64)
    rounds: list[dict[str, Any]] = []
    max_rounds = 12
    cp = model.central_planet
    step_sizes = tuple(search_settings.get("step_sizes", (1, 2, 4, 8)))

    for round_idx in range(max_rounds):
        round_model = model.with_central_reserve(reserve)
        r_start = min(0.85, 0.10 + 0.35 * round_idx)
        r_end = min(0.95, r_start + 0.35)
        result = search_best_configuration(
            round_model,
            progress_cb=progress_cb,
            progress_range=(r_start, r_end),
            **search_settings,
        )
        best_levels = result[-1]
        needed = central_reserve_needed(round_model, best_levels)
        warehouses = round_model.simulate(best_levels[None])["planet_warehouse_levels"][0]
        held = round_model.warehouse_capacities[warehouses[cp]] if cp >= 0 else np.full(len(RESOURCE_KEYS), np.inf)
        holds_the_production = not (needed > held).any()
        rounds.append(
            {
                "round": len(rounds) + 1,
                "ships_per_month": round(float(round_model.ships_per_month(best_levels[None])[0]), 2),
                **{f"{resource}_reserved": float(amount) for resource, amount in zip(RESOURCE_KEYS, reserve)},
                **{f"{resource}_needed": round(float(amount), 1) for resource, amount in zip(RESOURCE_KEYS, needed)},
                **{f"{resource}_held": float(amount) for resource, amount in zip(RESOURCE_KEYS, held)},
                "holds_the_production": bool(holds_the_production),
            }
        )
        if holds_the_production or round_idx + 1 >= max_rounds:
            final_model = model.with_central_reserve(needed)
            if cp >= 0 and holds_the_production and round_idx >= 1:
                wh_reserved = warehouses[cp].copy()
                wh_needed = final_model.simulate(best_levels[None])["planet_warehouse_levels"][0, cp]
                if (wh_needed < wh_reserved).any():
                    # Round `round_idx + 1` overshot: shrinking `reserve` to `needed` freed central warehouse
                    # fields. Step down intermediate warehouse brackets so freed fields are spent up to equilibrium.
                    best_rank = (
                        float(final_model.ships_per_month(best_levels[None])[0]),
                        -int(np.abs(best_levels - model.built_opt_levels).sum()),
                        -int(best_levels.sum()),
                    )
                    best_choice: tuple[EmpireProductionModel, tuple[Any, ...]] = (final_model, result)
                    refined_base = _finalize_self_consistent_round(model, final_model, result, step_sizes)
                    if refined_base is not None:
                        fm0, res0, ships0, _n0, _h0 = refined_base
                        lvls0 = res0[-1]
                        rk0 = (ships0, -int(np.abs(lvls0 - model.built_opt_levels).sum()), -int(lvls0.sum()))
                        if rk0 > best_rank:
                            best_rank = rk0
                            best_choice = (fm0, res0)

                    max_gap = int((wh_reserved - wh_needed).max())
                    budget = max(0, max_rounds - len(rounds))
                    for step_down in range(1, min(max_gap, budget) + 1):
                        cand_wh = np.maximum(wh_needed, wh_reserved - step_down)
                        cand_reserve = model.warehouse_capacities[cand_wh]
                        try:
                            cand_round_model = model.with_central_reserve(cand_reserve)
                        except ValueError:
                            break
                        p_s = min(0.92, 0.80 + 0.04 * step_down)
                        p_e = min(0.95, p_s + 0.04)
                        cand_raw = search_best_configuration(
                            cand_round_model,
                            progress_cb=progress_cb,
                            progress_range=(p_s, p_e),
                            **search_settings,
                        )
                        refined = _finalize_self_consistent_round(model, cand_round_model, cand_raw, step_sizes)
                        if refined is None:
                            continue
                        fm_c, res_c, ships_c, needed_c, held_c = refined
                        lvls_c = res_c[-1]
                        rounds.append(
                            {
                                "round": len(rounds) + 1,
                                "ships_per_month": round(ships_c, 2),
                                **{
                                    f"{resource}_reserved": float(amount)
                                    for resource, amount in zip(RESOURCE_KEYS, cand_reserve)
                                },
                                **{
                                    f"{resource}_needed": round(float(amount), 1)
                                    for resource, amount in zip(RESOURCE_KEYS, needed_c)
                                },
                                **{
                                    f"{resource}_held": float(amount) for resource, amount in zip(RESOURCE_KEYS, held_c)
                                },
                                "holds_the_production": True,
                            }
                        )
                        rk_c = (ships_c, -int(np.abs(lvls_c - model.built_opt_levels).sum()), -int(lvls_c.sum()))
                        if rk_c > best_rank:
                            best_rank = rk_c
                            best_choice = (fm_c, res_c)
                        elif ships_c < best_rank[0] * (1.0 - 1e-4):
                            break
                    return best_choice[0], rounds, *best_choice[1]
            return final_model, rounds, *result
        next_reserve = np.maximum(reserve, needed)
        if (next_reserve <= reserve).all():
            return model.with_central_reserve(needed), rounds, *result
        reserve = next_reserve
    raise RuntimeError("search_in_rounds terminated unexpectedly")  # pragma: no cover


# ---------------------------------------------------------------------------
# Result builders & public API
# ---------------------------------------------------------------------------


def _warehouse_requirement_rows(
    plan: Mapping[str, Any],
    planned_levels: Mapping[str, int],
    net_per_hour: np.ndarray,
    *,
    warehouse_hours: float,
    ship_built: Optional[str],
    central_reserve: np.ndarray,
) -> list[dict[str, Any]]:
    largest_cost, largest_cost_of = largest_costs(
        [
            *(
                (plan["capacity_needed_for"][resource], {resource: plan["capacity_needed"][resource]})
                for resource in RESOURCE_KEYS
            ),
            *(
                cost
                for key in (*plan["min_levels"], *WAREHOUSE_KEYS)
                for cost in building_upgrade_costs(key, plan["built_levels"][key], planned_levels[key])
            ),
            *(
                (ship, game_data.SHIPS[SHIP_SLUG_BY_KEY[ship]].get("base_cost") or {})
                for ship in ([ship_built] if ship_built else [])
            ),
        ]
    )
    rows: list[dict[str, Any]] = []
    for index, (resource, key) in enumerate(zip(RESOURCE_KEYS, WAREHOUSE_KEYS)):
        asked = [
            (warehouse_level_for(largest_cost[resource]), f"cost of {largest_cost_of[resource]}"),
            (warehouse_level_for(warehouse_hours * max(0.0, float(net_per_hour[index]))), "own production"),
            (warehouse_level_for(float(central_reserve[index])), "empire production"),
            (plan["warehouse_min_levels"][key], plan["warehouse_min_set_by"][key]),
        ]
        level, set_by = max(asked, key=lambda rule: rule[0])
        rows.append(
            {
                "resource": resource,
                "key": key,
                "built_level": int(plan["built_levels"][key]),
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
    plan: Mapping[str, Any],
    planned_levels: Mapping[str, int],
    speedup_factor: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reserved = plan.get("reserved_floors") or {}
    for building_id in sorted(game_data.BUILDING_BY_ID):
        key = f"b{building_id}"
        built_level = int(plan["built_levels"][key])
        target_level = int(planned_levels[key])
        if built_level == 0 and target_level == 0:
            continue
        if key in plan["min_levels"]:
            role = "optimized"
        else:
            role = "warehouse" if key in WAREHOUSE_KEYS else "fixed"
        build_time = last_level_build_time(key, planned_levels, speedup_factor) if target_level > built_level else 0.0
        rows.append(
            {
                "key": key,
                "building": str(game_data.BUILDING_BY_ID[building_id]["name"]),
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
    b1 = max(0, int(planned_levels.get("b1", 0) or 0))
    b2 = max(0, int(planned_levels.get("b2", 0) or 0))
    b3 = max(0, int(planned_levels.get("b3", 0) or 0))
    b4 = max(0, int(planned_levels.get("b4", 0) or 0))
    b9 = max(0, int(planned_levels.get("b9", 0) or 0))
    b17 = max(0, int(planned_levels.get("b17", 0) or 0))

    d_b1 = max(0, b1 - max(0, int(floor_map.get("b1", 0) or 0)))
    d_b2 = max(0, b2 - max(0, int(floor_map.get("b2", 0) or 0)))
    d_b3 = max(0, b3 - max(0, int(floor_map.get("b3", 0) or 0)))
    d_b4 = max(0, b4 - max(0, int(floor_map.get("b4", 0) or 0)))
    d_b9 = max(0, b9 - max(0, int(floor_map.get("b9", 0) or 0)))
    d_b17 = max(0, b17 - max(0, int(floor_map.get("b17", 0) or 0)))

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


def _extract_previous_planet_state(
    planet_plans: list[dict[str, Any]],
    optimized_keys: tuple[str, ...],
    previous_result: Mapping[str, Any] | None,
) -> tuple[list[Optional[np.ndarray]], list[Optional[str]]]:
    """Match current planet plans against a previous Config Optimizer result for sticky warm-start."""
    prev_targets: list[Optional[np.ndarray]] = [None] * len(planet_plans)
    prev_roles: list[Optional[str]] = [None] * len(planet_plans)
    if not isinstance(previous_result, Mapping):
        return prev_targets, prev_roles
    raw_planets = previous_result.get("planets")
    if not isinstance(raw_planets, (list, tuple)) or not raw_planets:
        return prev_targets, prev_roles

    by_pid: dict[int, Mapping[str, Any]] = {}
    by_coords: dict[str, Mapping[str, Any]] = {}
    by_placeholder_slot: dict[int, Mapping[str, Any]] = {}
    for entry in raw_planets:
        if not isinstance(entry, Mapping):
            continue
        pid = game_data.safe_int(entry.get("planet_id"), 0)
        if pid > 0:
            by_pid[pid] = entry
        raw_c = str(entry.get("coords") or "").strip()
        if raw_c:
            by_coords[raw_c] = entry
            if not raw_c.startswith("EMPTY:"):
                by_coords[normalize_coords(raw_c)] = entry
        if bool(entry.get("is_placeholder")):
            slot = game_data.safe_int(entry.get("slot", entry.get("slot_index")), 0)
            if slot > 0:
                by_placeholder_slot[slot] = entry

    for idx, plan in enumerate(planet_plans):
        matched: Optional[Mapping[str, Any]] = None
        pid = int(plan["planet_id"])
        raw_c = str(plan["coords"] or "").strip()
        norm_c = normalize_coords(raw_c) if raw_c and not raw_c.startswith("EMPTY:") else raw_c
        if pid > 0 and pid in by_pid:
            matched = by_pid[pid]
        elif raw_c and raw_c in by_coords:
            matched = by_coords[raw_c]
        elif norm_c and norm_c in by_coords:
            matched = by_coords[norm_c]
        elif bool(plan["is_placeholder"]) and int(plan["slot_index"]) in by_placeholder_slot:
            matched = by_placeholder_slot[int(plan["slot_index"])]
        if matched is None:
            continue

        planned_map: dict[str, int] = {}
        built_map: dict[str, int] = {}
        raw_built = matched.get("built_floors")
        if isinstance(raw_built, Mapping):
            for bk, lvl in raw_built.items():
                built_map[str(bk)] = max(0, game_data.safe_int(lvl, 0))
        raw_blds = matched.get("buildings")
        if isinstance(raw_blds, (list, tuple)):
            for row in raw_blds:
                if not isinstance(row, Mapping):
                    continue
                bk = str(row.get("key") or row.get("building_key") or "").strip()
                if not bk:
                    continue
                planned_map[bk] = max(0, game_data.safe_int(row.get("target_level", row.get("level", 0)), 0))
                if bk not in built_map:
                    built_map[bk] = max(0, game_data.safe_int(row.get("built_level", 0), 0))
        elif isinstance(raw_blds, Mapping):
            for bk, entry in raw_blds.items():
                if isinstance(entry, Mapping):
                    planned_map[str(bk)] = max(
                        0, game_data.safe_int(entry.get("target_level", entry.get("level", 0)), 0)
                    )
                else:
                    planned_map[str(bk)] = max(0, game_data.safe_int(entry, 0))

        if planned_map:
            prev_targets[idx] = np.array(
                [max(0, int(planned_map.get(key, 0))) for key in optimized_keys],
                dtype=np.int64,
            )

        explicit_prev_role = normalize_planet_specialization(matched.get("detected_specialization"))
        if explicit_prev_role in SPECIALIZATION_ALLOWED_BUILDINGS:
            prev_roles[idx] = explicit_prev_role
        elif planned_map:
            prod = matched.get("production") if isinstance(matched.get("production"), Mapping) else {}
            prev_shipyard = float(prod.get("shipyard_ships_per_month") or 0.0)
            inferred = detect_planet_specialization(
                planned_map,
                built_levels=built_map,
                shipyard_ships=prev_shipyard,
            )
            if inferred in SPECIALIZATION_ALLOWED_BUILDINGS:
                prev_roles[idx] = inferred

    return prev_targets, prev_roles


def _trim_design_to_fit(
    model: EmpireProductionModel,
    planet_idx: int,
    design: np.ndarray,
) -> np.ndarray:
    """Clip `design` into `[min_levels, max_levels]` and trim excess levels until feasible."""
    lo = model.min_levels[planet_idx]
    hi = model.max_levels[planet_idx]
    cand = np.clip(np.asarray(design, dtype=np.int64), lo, hi).copy()
    while not bool(model.is_feasible(cand[None, None, :], [planet_idx])[0]):
        slack = cand - lo
        if not (slack > 0).any():
            return lo.copy()
        drop_idx = int(np.argmax(slack))
        cand[drop_idx] -= 1
    return cand


def _central_reserve_holds(model: EmpireProductionModel, outcome: Mapping[str, np.ndarray]) -> bool:
    if model.central_planet < 0:
        return True
    net_per_hour = outcome["planet_net_per_hour"][0].sum(axis=0)
    needed = model.warehouse_hours * np.maximum(net_per_hour, 0.0)
    wh = outcome["planet_warehouse_levels"][0, model.central_planet]
    if (wh > model.top_warehouse_level).any():
        return False
    held = model.warehouse_capacities[wh]
    return not bool((needed > held).any())


def _evaluate_stability_metrics(
    model: EmpireProductionModel,
    levels: np.ndarray,
    planet_plans: list[dict[str, Any]],
    prev_roles: list[Optional[str]],
    cost_cache: dict[tuple[str, int, int], float],
) -> tuple[float, int, float, list[str], dict[str, np.ndarray]]:
    """Return `(ships_per_month, role_changes, upgrade_cost_spice_eq, detected_roles, outcome)`."""
    outcome = model.simulate(levels[None])
    limit_totals = outcome["planet_limits"][0].sum(axis=0)
    ships = float(limit_totals.min())
    shipyard_col = model.limit_names.index("shipyards")

    roles: list[str] = []
    role_changes = 0
    total_cost = 0.0
    for idx, plan in enumerate(planet_plans):
        built = plan["built_levels"]
        wh_lvls = outcome["planet_warehouse_levels"][0, idx]
        planned_opt = dict(zip(model.optimized_keys, (int(v) for v in levels[idx])))
        role = detect_planet_specialization(
            planned_opt,
            min_levels=plan["min_levels"],
            built_levels=built,
            shipyard_ships=float(outcome["planet_limits"][0, idx, shipyard_col]),
            locked_specialization=str(plan.get("locked_specialization") or "auto"),
        )
        roles.append(role)
        prev_role = prev_roles[idx]
        if prev_role in SPECIALIZATION_ALLOWED_BUILDINGS and role != prev_role:
            role_changes += 1

        for b_idx, key in enumerate(model.optimized_keys):
            frm = int(built[key])
            to = int(levels[idx, b_idx])
            if to > frm:
                ckey = (key, frm, to)
                val = cost_cache.get(ckey)
                if val is None:
                    val = building_upgrade_cost_spice_eq(key, frm, to)
                    cost_cache[ckey] = val
                total_cost += val
        for w_idx, wkey in enumerate(WAREHOUSE_KEYS):
            frm = int(built[wkey])
            to = int(wh_lvls[w_idx])
            if to > frm:
                ckey = (wkey, frm, to)
                val = cost_cache.get(ckey)
                if val is None:
                    val = building_upgrade_cost_spice_eq(wkey, frm, to)
                    cost_cache[ckey] = val
                total_cost += val

    return ships, role_changes, total_cost, roles, outcome


def _transfer_design_for_swap(
    model: EmpireProductionModel,
    src_idx: int,
    dst_idx: int,
    src_design: np.ndarray,
) -> np.ndarray:
    """Transfer `src_idx`'s actively chosen buildings onto `dst_idx` without leaking unrelated built floors."""
    active = src_design > model.min_levels[src_idx]
    transferred = np.where(active, src_design, model.min_levels[dst_idx])
    return _trim_design_to_fit(model, dst_idx, transferred)


def _polish_pair_in_place(
    model: EmpireProductionModel,
    levels: np.ndarray,
    pair: tuple[int, int],
    moves: np.ndarray,
) -> np.ndarray:
    """Locally polish only the two swapped planets against the rest of the empire."""
    cand = np.array(levels, dtype=np.int64, copy=True)
    planet_limits = model.simulate(cand[None])["planet_limits"][0]
    for _pass in range(2):
        changed = False
        for planet in pair:
            others = planet_limits.sum(axis=0) - planet_limits[planet]

            def empire_score(own: np.ndarray, others: np.ndarray = others) -> np.ndarray:
                return smallest_limit(others + own)

            design = improve_design(model, planet, cand[planet], empire_score, moves)
            if (design != cand[planet]).any():
                cand[planet] = design
                planet_limits[planet] = model.simulate(design[None, None, :], [planet])["planet_limits"][0, 0]
                changed = True
        if not changed:
            break
    return cand


def _align_unlocked_planet_roles_for_sunk_cost(
    model: EmpireProductionModel,
    levels: np.ndarray,
    planet_plans: list[dict[str, Any]],
    prev_roles: list[Optional[str]],
    cost_cache: dict[tuple[str, int, int], float],
    step_sizes: tuple[int, ...] = (1, 2, 4, 8),
) -> np.ndarray:
    """Pairwise swap unlocked planets' target designs when it keeps/improves ships/mo and reduces role flips / sunk cost."""
    unlocked = [
        idx
        for idx, plan in enumerate(planet_plans)
        if normalize_planet_specialization(plan.get("locked_specialization")) == "auto"
    ]
    if len(unlocked) < 2:
        return levels

    moves = planet_moves(levels.shape[1], step_sizes)
    cur = np.array(levels, dtype=np.int64, copy=True)
    cur_ships, cur_changes, cur_cost, _roles, _outcome = _evaluate_stability_metrics(
        model, cur, planet_plans, prev_roles, cost_cache
    )
    for _pass in range(len(unlocked) * 2):
        improved = False
        for pos_a in range(len(unlocked)):
            i = unlocked[pos_a]
            for pos_b in range(pos_a + 1, len(unlocked)):
                j = unlocked[pos_b]
                if np.array_equal(cur[i], cur[j]):
                    continue
                cand = cur.copy()
                cand[i] = _transfer_design_for_swap(model, j, i, cur[j])
                cand[j] = _transfer_design_for_swap(model, i, j, cur[i])
                if np.array_equal(cand, cur):
                    continue
                if not bool(model.is_feasible(cand[None])[0]):
                    continue
                cand = _polish_pair_in_place(model, cand, (i, j), moves)
                if not bool(model.is_feasible(cand[None])[0]):
                    continue
                cand_ships, cand_changes, cand_cost, _c_roles, cand_out = _evaluate_stability_metrics(
                    model, cand, planet_plans, prev_roles, cost_cache
                )
                if not _central_reserve_holds(model, cand_out):
                    continue
                better = False
                if cand_ships > cur_ships + 1e-6:
                    better = True
                elif cand_ships >= cur_ships - 1e-6:
                    if cand_changes < cur_changes:
                        better = True
                    elif cand_changes == cur_changes and cand_cost < cur_cost - 1e-3:
                        better = True
                if better:
                    cur = cand
                    cur_ships, cur_changes, cur_cost = cand_ships, cand_changes, cand_cost
                    improved = True
        if not improved:
            break
    return cur


def _build_sticky_candidate(
    best_model: EmpireProductionModel,
    fresh_levels: np.ndarray,
    planet_plans: list[dict[str, Any]],
    prev_targets: list[Optional[np.ndarray]],
    prev_roles: list[Optional[str]],
    step_sizes: tuple[int, ...],
) -> tuple[Optional[EmpireProductionModel], Optional[np.ndarray], Optional[float]]:
    """Build and polish a role-preserving candidate anchored to previous planet specializations."""
    sticky_model = copy.copy(best_model)
    sticky_max = best_model.max_levels.copy()
    for idx, plan in enumerate(planet_plans):
        eff_role = normalize_planet_specialization(plan.get("locked_specialization"))
        if eff_role == "auto" and prev_roles[idx] in SPECIALIZATION_ALLOWED_BUILDINGS:
            eff_role = str(prev_roles[idx])
        allowed = SPECIALIZATION_ALLOWED_BUILDINGS.get(eff_role)
        if allowed is not None:
            for b_idx, key in enumerate(best_model.optimized_keys):
                if key not in allowed:
                    sticky_max[idx, b_idx] = best_model.min_levels[idx, b_idx]
    sticky_model.max_levels = sticky_max

    seed = np.zeros_like(fresh_levels)
    for idx in range(len(planet_plans)):
        base_vec = prev_targets[idx] if prev_targets[idx] is not None else fresh_levels[idx]
        seed[idx] = _trim_design_to_fit(sticky_model, idx, base_vec)

    sticky_levels = polish_both_ways(sticky_model, seed, step_sizes)
    for _round in range(3):
        outcome = sticky_model.simulate(sticky_levels[None])
        if _central_reserve_holds(sticky_model, outcome):
            break
        needed = central_reserve_needed(sticky_model, sticky_levels)
        next_reserve = np.maximum(sticky_model.central_reserve, needed)
        try:
            sticky_model = sticky_model.with_central_reserve(next_reserve)
        except ValueError:
            return None, None, None
        for idx in range(len(planet_plans)):
            sticky_levels[idx] = _trim_design_to_fit(sticky_model, idx, sticky_levels[idx])
        sticky_levels = polish_both_ways(sticky_model, sticky_levels, step_sizes)

    if not bool(sticky_model.is_feasible(sticky_levels[None])[0]):
        return None, None, None
    final_out = sticky_model.simulate(sticky_levels[None])
    if not _central_reserve_holds(sticky_model, final_out):
        return None, None, None
    sticky_ships = float(final_out["planet_limits"][0].sum(axis=0).min())
    return sticky_model, sticky_levels, sticky_ships


def run_config_optimizer(
    state: GameState,
    *,
    universe_research_max: Mapping[str, int] | None = None,
    params: Mapping[str, Any] | None = None,
    planet_names: Mapping[str, str] | None = None,
    previous_result: Mapping[str, Any] | None = None,
    progress_cb: Callable[[str, float], None] | None = None,
) -> dict[str, Any]:
    """Optimize target building levels per planet to maximize monthly production of `target_ship_key`.

    Always enforces every owned planet's built building levels (`planet.buildings`),
    `reserved_floors_by_planet`, and any explicit `planet_specializations` locks.
    Preserves custom planet display names from `state.planets` and optional
    `planet_names` / `params["planet_names"]` / `params["custom_planet_names"]`.
    When `previous_result` is supplied and `preserve_previous_specializations` is enabled,
    warm-starts and compares a sticky role-preserving plan against the fresh global search
    using `specialization_switch_threshold_pct` and sunk-cost tie-breaking.
    """
    t0 = time.perf_counter()
    if progress_cb is not None:
        progress_cb("preparing", 0.02)

    cfg = _normalize_params(params)
    if isinstance(planet_names, Mapping) and planet_names:
        cfg["planet_names"] = {
            **cfg["planet_names"],
            **normalize_planet_names_map(planet_names),
        }
    misc_by_slot_int: dict[int, dict[str, int]] = {}
    for slot_k, bmap in cfg["misc_building_levels_by_slot"].items():
        slot_i = game_data.safe_optional_int(slot_k, None)
        if slot_i is not None:
            misc_by_slot_int[slot_i] = {str(k): int(v) for k, v in bmap.items()}

    check_optimizer_settings(
        cfg["optimized_building_max_levels"],
        cfg["misc_building_levels"],
        misc_by_slot_int,
        cfg["min_fleet_per_planet"],
        cfg["target_ship_key"],
        cfg["warehouse_hours_of_production"],
        cfg["central_planet_min_warehouse_level"],
        cfg["min_warehouse_level"],
    )

    owned_normal = [p for p in (state.planets or ()) if normalize_planet_type(p) == "normal"]
    roster_planets: list[PlanetState] = list(owned_normal)
    if cfg["pad_generic_colonies"] and len(owned_normal) < cfg["max_planets"]:
        empty_temp = int(cfg.get("empty_colony_temperature_c", 0))
        missing_slots = cfg["max_planets"] - len(owned_normal)
        for slot_num in range(1, missing_slots + 1):
            roster_planets.append(_empty_colony_slot(slot_num, cfg["empty_colony_fields"], empty_temp))

    if not roster_planets:
        raise ValueError("No normal planets available in GameState (enable pad_generic_colonies for preview)")

    own_researches = {key: max(0, int((state.researches or {}).get(key, 0) or 0)) for key, _ in research_catalog()}
    uni_max = {
        key: max(own_researches[key], max(0, int((universe_research_max or {}).get(key, 0) or 0)))
        for key in own_researches
    }
    required_research = research_requirements(
        [
            cfg["target_ship_key"],
            *(ship for ship, count in cfg["min_fleet_per_planet"].items() if count > 0),
            *cfg["optimized_building_max_levels"],
            *(key for key, level in cfg["misc_building_levels"].items() if level > 0),
            *(key for levels in misc_by_slot_int.values() for key, level in levels.items() if level > 0),
        ]
    )
    planned_research_rows = plan_research_levels(
        uni_max,
        own_researches,
        required_research,
        cfg["research_percent_of_universe_max"],
    )
    planned_research_levels = {row["key"]: int(row["planned_level"]) for row in planned_research_rows}

    speedup_factor = _safe_speedup(state, cfg)
    plan_account_context = {
        "speedup_factor": speedup_factor,
        "r1_mining_tech": planned_research_levels.get("r1", 0),
        "r2_energy_tech": planned_research_levels.get("r2", 0),
    }

    planet_plans = build_planet_plans(
        roster_planets,
        owned_count=len(owned_normal),
        optimized_max_levels=cfg["optimized_building_max_levels"],
        misc_levels=cfg["misc_building_levels"],
        misc_levels_by_slot=misc_by_slot_int,
        min_fleet=cfg["min_fleet_per_planet"],
        central_planet_slot=cfg["central_planet_slot"],
        central_min_warehouse_level=cfg["central_planet_min_warehouse_level"],
        research_costs=research_upgrade_costs(own_researches, planned_research_levels),
        reserved_floors_by_planet=cfg["reserved_floors_by_planet"],
        min_warehouse_level=cfg["min_warehouse_level"],
        planet_specializations=cfg["planet_specializations"],
        allow_demolition=cfg["allow_demolition"],
        planet_names=cfg["planet_names"],
    )
    _apply_manual_planet_pins(
        planet_plans,
        cfg.get("manual_planet_configs"),
        cfg["optimized_building_max_levels"],
    )
    planet_contexts = [
        {
            **plan_account_context,
            "slot_index": plan["slot_index"],
            "planet_id": plan["planet_id"],
            "coords": plan["coords"],
            "planet_status": plan["planet_status"],
            "planet_temperature_c": plan["temperature_c"],
        }
        for plan in planet_plans
    ]

    production_model = EmpireProductionModel(
        planet_plans,
        planet_contexts,
        optimized_keys=tuple(cfg["optimized_building_max_levels"]),
        target_ship_key=cfg["target_ship_key"],
        month_days=cfg["production_month_days"],
        warehouse_hours=cfg["warehouse_hours_of_production"],
    )

    if progress_cb is not None:
        progress_cb("searching", 0.08)

    (
        best_model,
        reserve_rounds,
        catalogues,
        _ga_designs,
        ga_history,
        ga_levels,
        best_levels,
    ) = search_in_rounds(
        production_model,
        progress_cb=progress_cb,
        grid_size=cfg["catalogue_grid_size"],
        priority_steps=cfg["catalogue_priority_steps"],
        step_sizes=tuple(cfg["hill_climb_step_sizes"]),
        population_size=cfg["ga_population_size"],
        generations=cfg["ga_generations"],
        tournament_size=cfg["ga_tournament_size"],
        crossover_rate=cfg["ga_crossover_rate"],
        mutation_rate=cfg["ga_mutation_rate"],
        elite_count=cfg["ga_elite_count"],
        seed=cfg["ga_random_seed"],
    )

    if progress_cb is not None:
        progress_cb("finalizing", 0.96)

    prev_targets, prev_roles = _extract_previous_planet_state(
        planet_plans,
        best_model.optimized_keys,
        previous_result,
    )
    has_prev_roles = any(role in SPECIALIZATION_ALLOWED_BUILDINGS for role in prev_roles)
    has_built_levels = any(any(lvl > 0 for lvl in plan["built_levels"].values()) for plan in planet_plans)
    cost_cache: dict[tuple[str, int, int], float] = {}

    if has_prev_roles or has_built_levels:
        fresh_levels = _align_unlocked_planet_roles_for_sunk_cost(
            best_model,
            best_levels,
            planet_plans,
            prev_roles,
            cost_cache,
            step_sizes=tuple(cfg["hill_climb_step_sizes"]),
        )
    else:
        fresh_levels = best_levels

    fresh_ships, fresh_changes, fresh_cost, _fresh_roles, _fresh_out = _evaluate_stability_metrics(
        best_model,
        fresh_levels,
        planet_plans,
        prev_roles,
        cost_cache,
    )
    best_levels = fresh_levels
    sticky_plan_applied = False
    sticky_ships_out: Optional[float] = None

    if cfg["preserve_previous_specializations"] and has_prev_roles:
        sticky_model, sticky_levels, sticky_ships_raw = _build_sticky_candidate(
            best_model,
            fresh_levels,
            planet_plans,
            prev_targets,
            prev_roles,
            tuple(cfg["hill_climb_step_sizes"]),
        )
        if sticky_model is not None and sticky_levels is not None and sticky_ships_raw is not None:
            sticky_ships, sticky_changes, sticky_cost, _s_roles, _s_out = _evaluate_stability_metrics(
                sticky_model,
                sticky_levels,
                planet_plans,
                prev_roles,
                cost_cache,
            )
            sticky_ships_out = sticky_ships
            threshold = max(0.0, float(cfg["specialization_switch_threshold_pct"])) / 100.0
            choose_sticky = False
            if sticky_ships > 0.0:
                rel_gain = (fresh_ships - sticky_ships) / sticky_ships
                if fresh_changes > sticky_changes:
                    if rel_gain <= threshold:
                        choose_sticky = True
                elif fresh_changes == sticky_changes:
                    if (sticky_ships, -sticky_cost, -int(sticky_levels.sum())) > (
                        fresh_ships,
                        -fresh_cost,
                        -int(fresh_levels.sum()),
                    ):
                        choose_sticky = True
            if choose_sticky:
                best_model = sticky_model
                best_levels = sticky_levels
                sticky_plan_applied = True

    outcome = {name: values[0] for name, values in best_model.simulate(best_levels[None]).items()}
    limit_totals = outcome["planet_limits"].sum(axis=0)
    ships = float(limit_totals.min())
    shipyard_column = best_model.limit_names.index("shipyards")
    shipyard_total = float(limit_totals[shipyard_column])

    planet_records: list[dict[str, Any]] = []
    total_fields_used = 0
    total_fields_free = 0
    total_warehouse_fields = 0
    total_levels_to_build = 0
    total_levels_to_demolish = 0
    total_demolitions_when_full = 0
    total_buildings_cost = 0.0
    total_fleet_cost = 0.0
    total_pig_iron_hr = 0.0
    locked_planets_count = 0
    roles_preserved_count = 0
    roles_changed_count = 0

    for index, plan in enumerate(planet_plans):
        warehouses_map = dict(zip(WAREHOUSE_KEYS, (int(lvl) for lvl in outcome["planet_warehouse_levels"][index])))
        planned_map = {
            **plan["fixed_levels"],
            **warehouses_map,
            **dict(zip(best_model.optimized_keys, (int(lvl) for lvl in best_levels[index]))),
        }
        fields_used_target = int(sum(planned_map.values()))
        fields_free = int(plan["fields_max"]) - fields_used_target
        shipyard_ships = float(outcome["planet_limits"][index, shipyard_column])
        locked_spec = normalize_planet_specialization(plan.get("locked_specialization"))
        if locked_spec != "auto":
            locked_planets_count += 1
        detected_spec = detect_planet_specialization(
            planned_map,
            min_levels=plan["min_levels"],
            built_levels=plan["built_levels"],
            shipyard_ships=shipyard_ships,
            locked_specialization=locked_spec,
        )
        prev_spec = prev_roles[index]
        spec_changed = bool(prev_spec in SPECIALIZATION_ALLOWED_BUILDINGS and detected_spec != prev_spec)
        if prev_spec in SPECIALIZATION_ALLOWED_BUILDINGS:
            if spec_changed:
                roles_changed_count += 1
            else:
                roles_preserved_count += 1
        warehouse_fields = int(sum(warehouses_map.values()))
        levels_to_build = int(sum(max(0, planned_map[key] - plan["built_levels"][key]) for key in planned_map))
        levels_to_demolish = int(sum(max(0, plan["built_levels"][key] - planned_map[key]) for key in planned_map))
        built_sum_now = int(sum(plan["built_levels"].values()))
        demolitions_when_full = min(
            levels_to_demolish,
            max(0, built_sum_now + levels_to_build - int(plan["fields_max"])),
        )
        upgrade_cost_sp = round(
            sum(
                building_upgrade_cost_spice_eq(key, plan["built_levels"][key], planned_map[key]) for key in planned_map
            ),
            2,
        )
        fleet_cost_sp = round(float(plan["fleet_cost_spice_eq"]), 2)
        pig_hr = round(float(outcome["planet_net_per_hour"][index, 0]), 1)

        total_fields_used += fields_used_target
        total_fields_free += fields_free
        total_warehouse_fields += warehouse_fields
        total_levels_to_build += levels_to_build
        total_levels_to_demolish += levels_to_demolish
        total_demolitions_when_full += demolitions_when_full
        total_buildings_cost += upgrade_cost_sp
        total_fleet_cost += fleet_cost_sp
        total_pig_iron_hr += pig_hr

        production_info = {
            "energy_produced": round(float(outcome["planet_energy_produced"][index]), 1),
            "energy_used": round(float(outcome["planet_energy_used"][index]), 1),
            "power_ratio": round(float(outcome["planet_power_ratio"][index]), 4),
            "smelting_ratio": round(float(outcome["planet_smelting_ratio"][index]), 4),
            **{
                f"{resource}_per_hour": round(float(outcome["planet_net_per_hour"][index, col]), 1)
                for col, resource in enumerate(RESOURCE_KEYS)
            },
            "shipyard_ships_per_month": round(shipyard_ships, 2),
            "ships_built_per_month": (
                round(shipyard_ships * ships / shipyard_total, 2) if shipyard_total > 0.0 else 0.0
            ),
            "warehouse_fields": warehouse_fields,
            "levels_to_build": levels_to_build,
            "levels_to_demolish": levels_to_demolish,
            "demolitions_when_full": demolitions_when_full,
            "upgrade_cost_spice_eq": upgrade_cost_sp,
            "fleet_cost_spice_eq": fleet_cost_sp,
        }
        warehouse_rows = _warehouse_requirement_rows(
            plan,
            planned_map,
            outcome["planet_net_per_hour"][index],
            warehouse_hours=best_model.warehouse_hours,
            ship_built=best_model.target_ship_key if shipyard_ships > 0.0 else None,
            central_reserve=(
                best_model.central_reserve if index == best_model.central_planet else np.zeros(len(RESOURCE_KEYS))
            ),
        )
        building_rows = _planet_building_rows(plan, planned_map, speedup_factor)
        planet_records.append(
            {
                "slot": int(plan["slot_index"]),
                "slot_index": int(plan["slot_index"]),
                "planet_id": int(plan["planet_id"]),
                "coords": str(plan["coords"]),
                "name": str(plan["name"]),
                "game_name": str(plan.get("game_name") or plan["name"]),
                "is_placeholder": bool(plan["is_placeholder"]),
                "planet_status": str(plan["planet_status"]),
                "is_central_planet": bool(plan["is_central_planet"]),
                "pays_for_research": bool(plan["pays_for_research"]),
                "temperature": int(plan["temperature_c"]),
                "temperature_c": int(plan["temperature_c"]),
                "fields_max": int(plan["fields_max"]),
                "fields_used_current": int(plan["fields_used_current"]),
                "fields_used_target": fields_used_target,
                "fields_used": fields_used_target,
                "fields_free": fields_free,
                "built_floors": {
                    f"b{bid}": int(plan["built_levels"][f"b{bid}"]) for bid in sorted(game_data.BUILDING_BY_ID)
                },
                "reserved_floors": dict(plan["reserved_floors"]),
                "detected_specialization": detected_spec,
                "locked_specialization": locked_spec,
                "previous_specialization": prev_spec,
                "specialization_changed": spec_changed,
                "production": production_info,
                "warehouses": warehouse_rows,
                "buildings": building_rows,
            }
        )

    needed_per_ship = [*best_model.ship_cost.tolist(), 1.0]
    min_limit = float(limit_totals.min())
    limits_records: list[dict[str, Any]] = []
    bottleneck_names: list[str] = []
    for name, needed, total_ships in zip(best_model.limit_names, needed_per_ship, limit_totals.tolist()):
        is_bn = bool(total_ships == min_limit)
        if is_bn:
            bottleneck_names.append(str(name))
        limits_records.append(
            {
                "limit": str(name),
                "needed_per_ship": float(needed),
                "ships_per_month": round(float(total_ships), 2),
                "available_per_month": round(float(total_ships) * float(needed), 1),
                "used_percent": round(100.0 * ships / float(total_ships), 2) if float(total_ships) > 0.0 else 0.0,
                "is_bottleneck": is_bn,
            }
        )

    ship_info = game_data.SHIPS[SHIP_SLUG_BY_KEY[best_model.target_ship_key]]
    ship_cost_sp = float(game_data.spice_equivalent(full_cost(ship_info.get("base_cost"))))
    research_cost_sp = float(
        sum(
            research_upgrade_cost_spice_eq(key, own_researches.get(key, 0), level)
            for key, level in planned_research_levels.items()
        )
    )
    investment = total_buildings_cost + total_fleet_cost + research_cost_sp
    monthly_output = ships * ship_cost_sp
    limiting_factor = ", ".join(bottleneck_names)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    final_best_hist = float(ga_history[-1]["best_ships_per_month"]) if ga_history else 0.0
    first_best_gen = next(
        (int(row["generation"]) for row in ga_history if float(row["best_ships_per_month"]) >= final_best_hist),
        0,
    )
    search_summary = {
        "search_rounds": int(len(reserve_rounds)),
        "rounds": reserve_rounds,
        "designs_per_planet": [int(len(cat)) for cat in catalogues],
        "possible_choices_log10": round(float(sum(math.log10(max(1, len(cat))) for cat in catalogues)), 2),
        "chromosomes_evaluated": int(cfg["ga_population_size"] * (cfg["ga_generations"] + 1) * len(reserve_rounds)),
        "first_generation_with_the_final_best": first_best_gen,
        "ga_ships_per_month": round(float(best_model.ships_per_month(ga_levels[None])[0]), 2),
        "polished_ships_per_month": round(ships, 2),
        "fresh_ships_per_month": round(float(fresh_ships), 2),
        "sticky_ships_per_month": round(float(sticky_ships_out), 2) if sticky_ships_out is not None else None,
        "sticky_plan_applied": bool(sticky_plan_applied),
        "locked_planets_count": int(locked_planets_count),
        "roles_preserved_count": int(roles_preserved_count),
        "roles_changed_count": int(roles_changed_count),
        "levels_changed_by_polish": int(np.abs(best_levels - ga_levels).sum()),
    }

    central_rec = (
        planet_records[best_model.central_planet] if 0 <= best_model.central_planet < len(planet_records) else None
    )
    resolved_planet_names = {
        str(rec["planet_id"]): str(rec["name"]) for rec in planet_records if int(rec["planet_id"]) > 0
    }
    summary = {
        "target_ship_key": best_model.target_ship_key,
        "target_ship": str(ship_info["name"]),
        "ships_per_month": round(ships, 2),
        "whole_ships_per_month": int(ships),
        "bottleneck": limiting_factor,
        "limiting_factor": limiting_factor,
        "hours_per_month": float(best_model.hours_per_month),
        "planets": int(len(planet_records)),
        "central_planet": best_model.planet_names[best_model.central_planet] if best_model.central_planet >= 0 else "",
        "central_planet_name": str(central_rec["name"]) if central_rec is not None else "",
        "central_planet_coords": str(central_rec["coords"]) if central_rec is not None else "",
        "fields_used": total_fields_used,
        "fields_free": total_fields_free,
        "warehouse_fields": total_warehouse_fields,
        "pig_iron_left_over_per_month": round(total_pig_iron_hr * best_model.hours_per_month, 1),
        "levels_to_build": total_levels_to_build,
        "levels_to_demolish": total_levels_to_demolish,
        "demolitions_when_full": total_demolitions_when_full,
        "allow_demolition": bool(cfg["allow_demolition"]),
        "buildings_cost_spice_eq": round(total_buildings_cost, 2),
        "fleet_cost_spice_eq": round(total_fleet_cost, 2),
        "research_cost_spice_eq": round(research_cost_sp, 2),
        "investment_spice_eq": round(investment, 2),
        "ship_cost_spice_eq": round(ship_cost_sp, 2),
        "monthly_output_spice_eq": round(monthly_output, 2),
        "investment_in_months_of_output": round(investment / monthly_output, 2) if monthly_output > 0.0 else None,
        "sticky_plan_applied": bool(sticky_plan_applied),
        "preserve_previous_specializations": bool(cfg["preserve_previous_specializations"]),
        "specialization_switch_threshold_pct": float(cfg["specialization_switch_threshold_pct"]),
        "locked_planets_count": int(locked_planets_count),
        "roles_preserved_count": int(roles_preserved_count),
        "roles_changed_count": int(roles_changed_count),
        "fresh_ships_per_month": round(float(fresh_ships), 2),
        "sticky_ships_per_month": round(float(sticky_ships_out), 2) if sticky_ships_out is not None else None,
        "elapsed_ms": elapsed_ms,
    }

    result = _json_ready(
        {
            "description": (
                "Pure-Python Config Optimizer result (ported from notebooks/config_optimizer.ipynb "
                "with hard built-level and reserved-floor preservation)."
            ),
            "schema_version": 1,
            "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "meta_parameters": cfg,
            "inputs": {
                "universe_speedup_factor": speedup_factor,
                "owned_planets": len(owned_normal),
                "roster_planets": len(planet_records),
                "pad_generic_colonies": bool(cfg["pad_generic_colonies"]),
                "own_research_levels": own_researches,
                "universe_research_max_levels": uni_max,
            },
            "planned_research_levels": planned_research_levels,
            "planned_research_rows": planned_research_rows,
            "planet_names": resolved_planet_names,
            "summary": summary,
            "search": search_summary,
            "limits": limits_records,
            "planets": planet_records,
            "elapsed_ms": elapsed_ms,
        }
    )

    if progress_cb is not None:
        progress_cb("completed", 1.0)
    return result


def _extract_planned_building_map_from_entry(
    entry: Mapping[str, Any] | None,
    fallback_levels: Mapping[str, int],
) -> dict[str, int]:
    """Extract `{b1..b21: target_level}` from a Config Optimizer planet record."""
    out = {bk: max(0, int(lvl)) for bk, lvl in fallback_levels.items()}
    if not isinstance(entry, Mapping):
        return out
    raw_blds = entry.get("buildings")
    if isinstance(raw_blds, (list, tuple)):
        seen: set[str] = set()
        for row in raw_blds:
            if not isinstance(row, Mapping):
                continue
            bk = str(row.get("key") or row.get("building_key") or "").strip().lower()
            if bk in out:
                out[bk] = max(0, game_data.safe_int(row.get("target_level", row.get("level", 0)), 0))
                seen.add(bk)
        # Buildings omitted from `buildings` rows in `run_config_optimizer` have `built == 0 and target == 0`
        for bk in list(out):
            if bk not in seen and int(fallback_levels.get(bk, 0)) == 0:
                out[bk] = 0
    elif isinstance(raw_blds, Mapping):
        for bk, raw_v in raw_blds.items():
            k = str(bk).strip().lower()
            if k not in out:
                continue
            if isinstance(raw_v, Mapping):
                out[k] = max(0, game_data.safe_int(raw_v.get("target_level", raw_v.get("level", 0)), 0))
            else:
                out[k] = max(0, game_data.safe_int(raw_v, 0))
    return out


def _evaluate_planet_map(
    planned_map: Mapping[str, int],
    plan: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    target_ship_key: str,
    target_requirements: Mapping[str, int],
    cost_resources: tuple[str, ...],
    ship_cost: np.ndarray,
    hours_per_month: float,
    base_ship_time_s: float,
) -> dict[str, Any]:
    """Evaluate exact steady-state production, energy, smelting, and shipyard limits for one planet."""
    totals = np.zeros(len(PRODUCTION_FLOW_KEYS), dtype=np.float64)
    for bid in sorted(game_data.BUILDING_BY_ID):
        bk = f"b{bid}"
        lvl = max(0, int(planned_map.get(bk, 0) or 0))
        if lvl > 0:
            totals += np.asarray(building_level_flows(bk, lvl, context), dtype=np.float64)
    totals[PRODUCTION_FLOW_KEYS.index("energy_produced")] += (
        float(plan.get("solar_cells", 0) or 0)
        * solar_cell_energy_per_unit(float(context["planet_temperature_c"]))
        * technology_multiplier("r2", int(context["r2_energy_tech"]))
    )
    flows = dict(zip(PRODUCTION_FLOW_KEYS, totals.tolist()))
    e_prod = float(flows["energy_produced"])
    e_used = float(flows["energy_used"])
    power_ratio = min(1.0, e_prod / e_used) if e_used > 0.0 else 1.0

    pig_mined = float(flows["pig_iron"]) * power_ratio
    pig_wanted = float(flows["pig_iron_used"]) * power_ratio
    smelting_ratio = min(1.0, pig_mined / pig_wanted) if pig_wanted > 0.0 else 1.0

    net = {
        "pig_iron": pig_mined - pig_wanted * smelting_ratio,
        "metal": float(flows["metal"]) * power_ratio * smelting_ratio,
        "kryptonite": float(flows["kryptonite"]) * power_ratio,
        "spice": float(flows["spice"]) * power_ratio - float(flows["spice_used"]),
    }
    net_vec = np.array([net[r] for r in RESOURCE_KEYS], dtype=np.float64)

    can_build = all(
        int(planned_map.get(req_k, 0) or 0) >= int(req_lvl) for req_k, req_lvl in target_requirements.items()
    )
    if can_build and base_ship_time_s > 0.0:
        shipyard_ships = (
            3600.0
            * float(hours_per_month)
            * shipyard_time_divisor(
                int(planned_map.get("b9", 0) or 0),
                int(planned_map.get("b17", 0) or 0),
                float(context["speedup_factor"]),
            )
            / float(base_ship_time_s)
        )
    else:
        shipyard_ships = 0.0

    limits_vec = np.array(
        [
            *(net[r] * float(hours_per_month) / float(c) for r, c in zip(cost_resources, ship_cost.tolist())),
            shipyard_ships,
        ],
        dtype=np.float64,
    )
    return {
        "energy_produced": e_prod,
        "energy_used": e_used,
        "power_ratio": power_ratio,
        "smelting_ratio": smelting_ratio,
        "net_per_hour": net_vec,
        "shipyard_ships": shipyard_ships,
        "planet_limits": limits_vec,
    }


def apply_manual_planet_override(
    state: GameState,
    *,
    target_buildings: Mapping[str, Any],
    base_result: Mapping[str, Any] | None = None,
    slot: Optional[int] = None,
    planet_id: Optional[int] = None,
    coords: Optional[str] = None,
    fields_max_override: Optional[int] = None,
    locked_specialization: Optional[str] = None,
    planet_name: Optional[str] = None,
    lock_for_ga: bool = True,
    auto_warehouses: bool = False,
    allow_over_fields: bool = False,
    universe_research_max: Mapping[str, int] | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply an operator's custom target building configuration for one planet and recompute empire metrics."""
    t0 = time.perf_counter()
    raw_base_params = (
        base_result.get("meta_parameters")
        if isinstance(base_result, Mapping) and isinstance(base_result.get("meta_parameters"), Mapping)
        else None
    )
    merged_raw_params: dict[str, Any] = {}
    if isinstance(raw_base_params, Mapping):
        merged_raw_params.update(dict(raw_base_params))
    if isinstance(params, Mapping):
        merged_raw_params.update(dict(params))
    cfg = _normalize_params(merged_raw_params)

    misc_by_slot_int: dict[int, dict[str, int]] = {}
    for slot_k, bmap in cfg["misc_building_levels_by_slot"].items():
        slot_i = game_data.safe_optional_int(slot_k, None)
        if slot_i is not None:
            misc_by_slot_int[slot_i] = {str(k): int(v) for k, v in bmap.items()}

    check_optimizer_settings(
        cfg["optimized_building_max_levels"],
        cfg["misc_building_levels"],
        misc_by_slot_int,
        cfg["min_fleet_per_planet"],
        cfg["target_ship_key"],
        cfg["warehouse_hours_of_production"],
        cfg["central_planet_min_warehouse_level"],
        cfg["min_warehouse_level"],
    )

    owned_normal = [p for p in (state.planets or ()) if normalize_planet_type(p) == "normal"]
    roster_planets: list[PlanetState] = list(owned_normal)
    if cfg["pad_generic_colonies"] and len(owned_normal) < cfg["max_planets"]:
        empty_temp = int(cfg.get("empty_colony_temperature_c", 0))
        missing_slots = cfg["max_planets"] - len(owned_normal)
        for slot_num in range(1, missing_slots + 1):
            roster_planets.append(_empty_colony_slot(slot_num, cfg["empty_colony_fields"], empty_temp))
    if not roster_planets:
        raise ValueError("No normal planets available in GameState (enable pad_generic_colonies for preview)")

    own_researches = {key: max(0, int((state.researches or {}).get(key, 0) or 0)) for key, _ in research_catalog()}
    uni_src = universe_research_max
    if uni_src is None and isinstance(base_result, Mapping) and isinstance(base_result.get("inputs"), Mapping):
        raw_u = base_result["inputs"].get("universe_research_max_levels")
        if isinstance(raw_u, Mapping):
            uni_src = raw_u
    uni_max = {key: max(own_researches[key], max(0, int((uni_src or {}).get(key, 0) or 0))) for key in own_researches}
    required_research = research_requirements(
        [
            cfg["target_ship_key"],
            *(ship for ship, count in cfg["min_fleet_per_planet"].items() if count > 0),
            *cfg["optimized_building_max_levels"],
            *(key for key, level in cfg["misc_building_levels"].items() if level > 0),
            *(key for levels in misc_by_slot_int.values() for key, level in levels.items() if level > 0),
        ]
    )
    planned_research_rows = plan_research_levels(
        uni_max,
        own_researches,
        required_research,
        cfg["research_percent_of_universe_max"],
    )
    planned_research_levels = {row["key"]: int(row["planned_level"]) for row in planned_research_rows}
    speedup_factor = _safe_speedup(state, cfg)
    plan_account_context = {
        "speedup_factor": speedup_factor,
        "r1_mining_tech": planned_research_levels.get("r1", 0),
        "r2_energy_tech": planned_research_levels.get("r2", 0),
    }

    planet_plans = build_planet_plans(
        roster_planets,
        owned_count=len(owned_normal),
        optimized_max_levels=cfg["optimized_building_max_levels"],
        misc_levels=cfg["misc_building_levels"],
        misc_levels_by_slot=misc_by_slot_int,
        min_fleet=cfg["min_fleet_per_planet"],
        central_planet_slot=cfg["central_planet_slot"],
        central_min_warehouse_level=cfg["central_planet_min_warehouse_level"],
        research_costs=research_upgrade_costs(own_researches, planned_research_levels),
        reserved_floors_by_planet=cfg["reserved_floors_by_planet"],
        min_warehouse_level=cfg["min_warehouse_level"],
        planet_specializations=cfg["planet_specializations"],
        allow_demolition=cfg["allow_demolition"],
        planet_names=cfg["planet_names"],
    )

    # Locate target planet in `planet_plans`
    target_idx: Optional[int] = None
    norm_req_coords = (
        normalize_coords(coords) if (coords and not str(coords).startswith("EMPTY:")) else str(coords or "").strip()
    )
    if planet_id is not None and int(planet_id) != 0:
        target_idx = next(
            (i for i, pl in enumerate(planet_plans) if int(pl["planet_id"]) == int(planet_id)),
            None,
        )
    if target_idx is None and slot is not None and int(slot) > 0:
        target_idx = next(
            (i for i, pl in enumerate(planet_plans) if int(pl["slot_index"]) == int(slot)),
            None,
        )
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

    # Match baseline planet entries from `base_result` if available
    base_planets_raw = (
        base_result.get("planets")
        if isinstance(base_result, Mapping) and isinstance(base_result.get("planets"), (list, tuple))
        else []
    )
    base_by_pid: dict[int, Mapping[str, Any]] = {}
    base_by_coords: dict[str, Mapping[str, Any]] = {}
    base_by_slot: dict[int, Mapping[str, Any]] = {}
    for entry in base_planets_raw:
        if not isinstance(entry, Mapping):
            continue
        epid = game_data.safe_int(entry.get("planet_id"), 0)
        if epid > 0:
            base_by_pid[epid] = entry
        ec = str(entry.get("coords") or "").strip()
        if ec:
            base_by_coords[ec] = entry
            if not ec.startswith("EMPTY:"):
                base_by_coords[normalize_coords(ec)] = entry
        eslot = game_data.safe_int(entry.get("slot", entry.get("slot_index")), 0)
        if eslot > 0:
            base_by_slot[eslot] = entry

    all_bkeys = tuple(f"b{bid}" for bid in sorted(game_data.BUILDING_BY_ID))
    planned_maps: list[dict[str, int]] = []
    prev_specs: list[Optional[str]] = []
    for idx, plan in enumerate(planet_plans):
        pid = int(plan["planet_id"])
        pc = str(plan["coords"] or "").strip()
        npc = normalize_coords(pc) if pc and not pc.startswith("EMPTY:") else pc
        matched = (
            base_by_pid.get(pid)
            if pid > 0 and pid in base_by_pid
            else base_by_coords.get(pc) or base_by_coords.get(npc) or base_by_slot.get(int(plan["slot_index"]))
        )
        fallback_lvls = {
            bk: max(
                int(plan["built_levels"].get(bk, 0)),
                int(plan["fixed_levels"].get(bk, 0)),
                int(plan["min_levels"].get(bk, 0)),
                int(plan["warehouse_min_levels"].get(bk, 0)),
            )
            for bk in all_bkeys
        }
        pmap = _extract_planned_building_map_from_entry(matched, fallback_lvls)
        if matched is not None and isinstance(matched.get("fields_max"), (int, float)):
            plan["fields_max"] = max(int(plan["fields_max"]), int(matched["fields_max"]))
        planned_maps.append(pmap)
        prev_sp = (
            normalize_planet_specialization(matched.get("detected_specialization"))
            if isinstance(matched, Mapping)
            else None
        )
        prev_specs.append(prev_sp if prev_sp in SPECIALIZATION_ALLOWED_BUILDINGS else None)

    # Apply the operator's edited target buildings & metadata on `target_idx`
    tplan = planet_plans[target_idx]
    normalized_override = normalize_planet_target_buildings(target_buildings)
    # Merge so partial maps keep existing levels for omitted buildings, while explicit keys win
    explicit_keys = {str(raw_bk).strip().lower() for raw_bk in target_buildings}
    merged_target_map = dict(planned_maps[target_idx])
    for k in explicit_keys:
        if k in normalized_override:
            merged_target_map[k] = normalized_override[k]
    # Auto-raise omitted same-planet building prerequisites (e.g. b7 >= 12 when b17 >= 1 is requested)
    for bk_chk, lvl_chk in list(merged_target_map.items()):
        if lvl_chk > 0:
            for req_bk, req_lvl in same_planet_building_requirements(bk_chk, lvl_chk).items():
                if req_bk != bk_chk and req_bk not in explicit_keys:
                    merged_target_map[req_bk] = max(int(merged_target_map.get(req_bk, 0)), int(req_lvl))

    if fields_max_override is not None and int(fields_max_override) > 0:
        tplan["fields_max"] = max(1, min(2000, int(fields_max_override)))

    planet_ident_key = (
        str(int(tplan["planet_id"])) if int(tplan["planet_id"]) > 0 else f"slot:{int(tplan['slot_index'])}"
    )

    if locked_specialization is not None:
        norm_lock = normalize_planet_specialization(locked_specialization)
        tplan["locked_specialization"] = norm_lock
        specs_map = dict(cfg.get("planet_specializations") or {})
        # Remove alternate keys for the same planet so the new lock/unlock is unambiguous
        for alias_k in (
            str(tplan["planet_id"]),
            str(tplan["coords"]),
            normalize_coords(str(tplan["coords"])) if not str(tplan["coords"]).startswith("EMPTY:") else "",
            f"slot:{int(tplan['slot_index'])}",
        ):
            if alias_k:
                specs_map.pop(alias_k, None)
        if norm_lock != "auto":
            specs_map[planet_ident_key] = norm_lock
        cfg["planet_specializations"] = specs_map

    if planet_name is not None:
        clean_nm = normalize_planet_name(planet_name)
        tplan["name"] = clean_nm or str(tplan.get("game_name") or tplan["coords"])
        names_map = dict(cfg.get("planet_names") or {})
        if clean_nm:
            names_map[planet_ident_key] = clean_nm
        else:
            names_map.pop(planet_ident_key, None)
        cfg["planet_names"] = names_map

    ship_info = game_data.SHIPS[SHIP_SLUG_BY_KEY[cfg["target_ship_key"]]]
    ship_base_cost = full_cost(ship_info.get("base_cost"))
    cost_resources = tuple(r for r in RESOURCE_KEYS if float(ship_base_cost[r]) > 0.0)
    ship_cost_vec = np.array([float(ship_base_cost[r]) for r in cost_resources], dtype=np.float64)
    hours_per_month = 24.0 * float(cfg["production_month_days"])
    base_ship_time_s = float(ship_info["base_build_time_s"])
    target_reqs = same_planet_building_requirements(cfg["target_ship_key"])
    warehouse_hours = float(cfg["warehouse_hours_of_production"])
    central_planet_idx = next((i for i, pl in enumerate(planet_plans) if pl["is_central_planet"]), -1)

    planet_contexts = [
        {
            **plan_account_context,
            "slot_index": pl["slot_index"],
            "planet_id": pl["planet_id"],
            "coords": pl["coords"],
            "planet_status": pl["planet_status"],
            "planet_temperature_c": pl["temperature_c"],
        }
        for pl in planet_plans
    ]

    # Optional auto-warehouse sizing on the edited planet if requested
    if auto_warehouses:
        tmp_eval = _evaluate_planet_map(
            merged_target_map,
            tplan,
            planet_contexts[target_idx],
            target_ship_key=cfg["target_ship_key"],
            target_requirements=target_reqs,
            cost_resources=cost_resources,
            ship_cost=ship_cost_vec,
            hours_per_month=hours_per_month,
            base_ship_time_s=base_ship_time_s,
        )
        wh_rows_tmp = _warehouse_requirement_rows(
            tplan,
            merged_target_map,
            tmp_eval["net_per_hour"],
            warehouse_hours=warehouse_hours,
            ship_built=cfg["target_ship_key"] if tmp_eval["shipyard_ships"] > 0.0 else None,
            central_reserve=np.zeros(len(RESOURCE_KEYS)),
        )
        for wrow in wh_rows_tmp:
            wk = str(wrow["key"])
            req_lvl = max(
                int(wrow["level_for_largest_cost"]),
                int(wrow["level_for_own_production"]),
                int(wrow["minimum_level"]),
            )
            merged_target_map[wk] = min(catalog_top_level(wk), max(int(tplan["built_levels"].get(wk, 0)), req_lvl))

    fields_used_target_edit = int(sum(merged_target_map.values()))
    if not allow_over_fields and fields_used_target_edit > int(tplan["fields_max"]):
        over = fields_used_target_edit - int(tplan["fields_max"])
        raise ValueError(
            f"Target configuration on {tplan['name']} [{tplan['coords']}] uses "
            f"{fields_used_target_edit}/{tplan['fields_max']} fields ({over} over planet capacity)."
        )

    planned_maps[target_idx] = merged_target_map

    # Update `manual_planet_configs` pin ledger in `cfg`
    manual_pins = dict(cfg.get("manual_planet_configs") or {})
    for alias_k in (
        str(tplan["planet_id"]),
        str(tplan["coords"]),
        normalize_coords(str(tplan["coords"])) if not str(tplan["coords"]).startswith("EMPTY:") else "",
        f"slot:{int(tplan['slot_index'])}",
    ):
        if alias_k:
            manual_pins.pop(alias_k, None)
    if lock_for_ga:
        manual_pins[planet_ident_key] = {
            "target_buildings": dict(merged_target_map),
            "fields_max_override": (
                int(fields_max_override) if (fields_max_override is not None and int(fields_max_override) > 0) else None
            ),
            "auto_warehouses": bool(auto_warehouses),
        }
    cfg["manual_planet_configs"] = manual_pins

    # If any level is intentionally set below `built_level` on an owned planet, mark `allow_demolition=True`
    # when already enabled or report exact `levels_to_demolish`
    evals = [
        _evaluate_planet_map(
            planned_maps[idx],
            planet_plans[idx],
            planet_contexts[idx],
            target_ship_key=cfg["target_ship_key"],
            target_requirements=target_reqs,
            cost_resources=cost_resources,
            ship_cost=ship_cost_vec,
            hours_per_month=hours_per_month,
            base_ship_time_s=base_ship_time_s,
        )
        for idx in range(len(planet_plans))
    ]
    limit_names = (*cost_resources, "shipyards")
    planet_limits_mat = np.stack([ev["planet_limits"] for ev in evals], axis=0)
    planet_net_mat = np.stack([ev["net_per_hour"] for ev in evals], axis=0)
    limit_totals = planet_limits_mat.sum(axis=0)
    ships = max(0.0, float(limit_totals.min())) if limit_totals.size else 0.0
    shipyard_col = limit_names.index("shipyards")
    shipyard_total = float(limit_totals[shipyard_col])
    empire_net_hr = np.maximum(0.0, planet_net_mat.sum(axis=0))
    central_reserve = warehouse_hours * empire_net_hr

    planet_records: list[dict[str, Any]] = []
    total_fields_used = 0
    total_fields_free = 0
    total_warehouse_fields = 0
    total_levels_to_build = 0
    total_levels_to_demolish = 0
    total_demolitions_when_full = 0
    total_buildings_cost = 0.0
    total_fleet_cost = 0.0
    total_pig_iron_hr = 0.0
    locked_planets_count = 0
    roles_preserved_count = 0
    roles_changed_count = 0

    for idx, plan in enumerate(planet_plans):
        pmap = planned_maps[idx]
        ev = evals[idx]
        fields_used_t = int(sum(pmap.values()))
        fields_free_t = int(plan["fields_max"]) - fields_used_t
        shipyard_ships = float(ev["shipyard_ships"])
        locked_spec = normalize_planet_specialization(plan.get("locked_specialization"))
        if locked_spec != "auto":
            locked_planets_count += 1
        detected_spec = detect_planet_specialization(
            pmap,
            min_levels=plan["min_levels"],
            built_levels=plan["built_levels"],
            shipyard_ships=shipyard_ships,
            locked_specialization=locked_spec,
        )
        prev_spec = prev_specs[idx]
        spec_changed = bool(prev_spec in SPECIALIZATION_ALLOWED_BUILDINGS and detected_spec != prev_spec)
        if prev_spec in SPECIALIZATION_ALLOWED_BUILDINGS:
            if spec_changed:
                roles_changed_count += 1
            else:
                roles_preserved_count += 1

        warehouse_fields = int(sum(int(pmap.get(wk, 0)) for wk in WAREHOUSE_KEYS))
        levels_to_build = int(sum(max(0, int(pmap[bk]) - int(plan["built_levels"][bk])) for bk in pmap))
        levels_to_demolish = int(sum(max(0, int(plan["built_levels"][bk]) - int(pmap[bk])) for bk in pmap))
        built_sum_now = int(sum(plan["built_levels"].values()))
        demolitions_when_full = min(
            levels_to_demolish,
            max(0, built_sum_now + levels_to_build - int(plan["fields_max"])),
        )
        upgrade_cost_sp = round(
            sum(building_upgrade_cost_spice_eq(bk, int(plan["built_levels"][bk]), int(pmap[bk])) for bk in pmap),
            2,
        )
        fleet_cost_sp = round(float(plan["fleet_cost_spice_eq"]), 2)
        pig_hr = round(float(ev["net_per_hour"][0]), 1)

        total_fields_used += fields_used_t
        total_fields_free += fields_free_t
        total_warehouse_fields += warehouse_fields
        total_levels_to_build += levels_to_build
        total_levels_to_demolish += levels_to_demolish
        total_demolitions_when_full += demolitions_when_full
        total_buildings_cost += upgrade_cost_sp
        total_fleet_cost += fleet_cost_sp
        total_pig_iron_hr += pig_hr

        production_info = {
            "energy_produced": round(float(ev["energy_produced"]), 1),
            "energy_used": round(float(ev["energy_used"]), 1),
            "power_ratio": round(float(ev["power_ratio"]), 4),
            "smelting_ratio": round(float(ev["smelting_ratio"]), 4),
            **{
                f"{resource}_per_hour": round(float(ev["net_per_hour"][col]), 1)
                for col, resource in enumerate(RESOURCE_KEYS)
            },
            "shipyard_ships_per_month": round(shipyard_ships, 2),
            "ships_built_per_month": (
                round(shipyard_ships * ships / shipyard_total, 2) if shipyard_total > 0.0 else 0.0
            ),
            "warehouse_fields": warehouse_fields,
            "levels_to_build": levels_to_build,
            "levels_to_demolish": levels_to_demolish,
            "demolitions_when_full": demolitions_when_full,
            "upgrade_cost_spice_eq": upgrade_cost_sp,
            "fleet_cost_spice_eq": fleet_cost_sp,
        }
        warehouse_rows = _warehouse_requirement_rows(
            plan,
            pmap,
            ev["net_per_hour"],
            warehouse_hours=warehouse_hours,
            ship_built=cfg["target_ship_key"] if shipyard_ships > 0.0 else None,
            central_reserve=(central_reserve if idx == central_planet_idx else np.zeros(len(RESOURCE_KEYS))),
        )
        building_rows = _planet_building_rows(plan, pmap, speedup_factor)
        is_pinned = _resolve_manual_planet_config(plan, manual_pins) is not None
        planet_records.append(
            {
                "slot": int(plan["slot_index"]),
                "slot_index": int(plan["slot_index"]),
                "planet_id": int(plan["planet_id"]),
                "coords": str(plan["coords"]),
                "name": str(plan["name"]),
                "game_name": str(plan.get("game_name") or plan["name"]),
                "is_placeholder": bool(plan["is_placeholder"]),
                "planet_status": str(plan["planet_status"]),
                "is_central_planet": bool(plan["is_central_planet"]),
                "pays_for_research": bool(plan["pays_for_research"]),
                "manual_pinned": bool(is_pinned),
                "temperature": int(plan["temperature_c"]),
                "temperature_c": int(plan["temperature_c"]),
                "fields_max": int(plan["fields_max"]),
                "fields_used_current": int(plan["fields_used_current"]),
                "fields_used_target": fields_used_t,
                "fields_used": fields_used_t,
                "fields_free": fields_free_t,
                "built_floors": {
                    f"b{bid}": int(plan["built_levels"][f"b{bid}"]) for bid in sorted(game_data.BUILDING_BY_ID)
                },
                "reserved_floors": dict(plan["reserved_floors"]),
                "detected_specialization": detected_spec,
                "locked_specialization": locked_spec,
                "previous_specialization": prev_spec,
                "specialization_changed": spec_changed,
                "production": production_info,
                "warehouses": warehouse_rows,
                "buildings": building_rows,
            }
        )

    needed_per_ship = [*ship_cost_vec.tolist(), 1.0]
    min_limit = float(limit_totals.min()) if limit_totals.size else 0.0
    limits_records: list[dict[str, Any]] = []
    bottleneck_names: list[str] = []
    for lname, needed, total_sh in zip(limit_names, needed_per_ship, limit_totals.tolist()):
        is_bn = bool(total_sh == min_limit)
        if is_bn:
            bottleneck_names.append(str(lname))
        limits_records.append(
            {
                "limit": str(lname),
                "needed_per_ship": float(needed),
                "ships_per_month": round(float(total_sh), 2),
                "available_per_month": round(float(total_sh) * float(needed), 1),
                "used_percent": round(100.0 * ships / float(total_sh), 2) if float(total_sh) > 0.0 else 0.0,
                "is_bottleneck": is_bn,
            }
        )

    ship_cost_sp = float(game_data.spice_equivalent(ship_base_cost))
    research_cost_sp = float(
        sum(
            research_upgrade_cost_spice_eq(key, own_researches.get(key, 0), level)
            for key, level in planned_research_levels.items()
        )
    )
    investment = total_buildings_cost + total_fleet_cost + research_cost_sp
    monthly_output = ships * ship_cost_sp
    limiting_factor = ", ".join(bottleneck_names)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    central_rec = planet_records[central_planet_idx] if 0 <= central_planet_idx < len(planet_records) else None
    resolved_planet_names = {
        str(rec["planet_id"]): str(rec["name"]) for rec in planet_records if int(rec["planet_id"]) > 0
    }
    prev_search = (
        dict(base_result.get("search") or {})
        if isinstance(base_result, Mapping) and isinstance(base_result.get("search"), Mapping)
        else {}
    )
    search_summary = {
        **prev_search,
        "polished_ships_per_month": round(ships, 2),
        "locked_planets_count": int(locked_planets_count),
        "manual_override_applied": True,
        "manual_override_slot": int(tplan["slot_index"]),
    }
    summary = {
        "target_ship_key": cfg["target_ship_key"],
        "target_ship": str(ship_info["name"]),
        "ships_per_month": round(ships, 2),
        "whole_ships_per_month": int(ships),
        "bottleneck": limiting_factor,
        "limiting_factor": limiting_factor,
        "hours_per_month": float(hours_per_month),
        "planets": int(len(planet_records)),
        "central_planet": (
            f"{central_rec['name']} [{central_rec['coords']}] (slot {central_rec['slot_index']})"
            if central_rec is not None
            else ""
        ),
        "central_planet_name": str(central_rec["name"]) if central_rec is not None else "",
        "central_planet_coords": str(central_rec["coords"]) if central_rec is not None else "",
        "fields_used": total_fields_used,
        "fields_free": total_fields_free,
        "warehouse_fields": total_warehouse_fields,
        "pig_iron_left_over_per_month": round(total_pig_iron_hr * hours_per_month, 1),
        "levels_to_build": total_levels_to_build,
        "levels_to_demolish": total_levels_to_demolish,
        "demolitions_when_full": total_demolitions_when_full,
        "allow_demolition": bool(cfg["allow_demolition"] or total_levels_to_demolish > 0),
        "buildings_cost_spice_eq": round(total_buildings_cost, 2),
        "fleet_cost_spice_eq": round(total_fleet_cost, 2),
        "research_cost_spice_eq": round(research_cost_sp, 2),
        "investment_spice_eq": round(investment, 2),
        "ship_cost_spice_eq": round(ship_cost_sp, 2),
        "monthly_output_spice_eq": round(monthly_output, 2),
        "investment_in_months_of_output": round(investment / monthly_output, 2) if monthly_output > 0.0 else None,
        "sticky_plan_applied": False,
        "preserve_previous_specializations": bool(cfg["preserve_previous_specializations"]),
        "specialization_switch_threshold_pct": float(cfg["specialization_switch_threshold_pct"]),
        "locked_planets_count": int(locked_planets_count),
        "roles_preserved_count": int(roles_preserved_count),
        "roles_changed_count": int(roles_changed_count),
        "manual_override_applied": True,
        "elapsed_ms": elapsed_ms,
    }
    return _json_ready(
        {
            "description": "Config Optimizer result with operator manual planet configuration override.",
            "schema_version": 1,
            "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "meta_parameters": cfg,
            "inputs": {
                "universe_speedup_factor": speedup_factor,
                "owned_planets": len(owned_normal),
                "roster_planets": len(planet_records),
                "pad_generic_colonies": bool(cfg["pad_generic_colonies"]),
                "own_research_levels": own_researches,
                "universe_research_max_levels": uni_max,
            },
            "planned_research_levels": planned_research_levels,
            "planned_research_rows": planned_research_rows,
            "planet_names": resolved_planet_names,
            "summary": summary,
            "search": search_summary,
            "limits": limits_records,
            "planets": planet_records,
            "elapsed_ms": elapsed_ms,
        }
    )


def compute_yearly_ships_per_week_projection(
    result: Mapping[str, Any],
    state: Optional[GameState] = None,
    *,
    horizon_years: int = 5,
) -> dict[str, Any]:
    """Project year-by-year `ships / week` as the empire builds from current floors to `result`."""
    years_n = max(1, min(15, game_data.safe_int(horizon_years, 5) or 5))
    total_weeks = 52 * years_n
    seconds_per_week = 168.0 * 3600.0

    summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
    meta_params = result.get("meta_parameters") if isinstance(result.get("meta_parameters"), Mapping) else {}
    inputs = result.get("inputs") if isinstance(result.get("inputs"), Mapping) else {}

    ship_key = str(summary.get("target_ship_key") or meta_params.get("target_ship_key") or "s16")
    if ship_key not in SHIP_SLUG_BY_KEY:
        ship_key = "s16"
    ship_info = game_data.SHIPS[SHIP_SLUG_BY_KEY[ship_key]]
    ship_name = str(ship_info.get("name") or ship_key)
    ship_base_cost = full_cost(ship_info.get("base_cost"))
    cost_resources = tuple(r for r in RESOURCE_KEYS if float(ship_base_cost[r]) > 0.0)
    ship_cost_map = {r: float(ship_base_cost[r]) for r in cost_resources}
    base_ship_time_s = max(1.0, float(ship_info.get("base_build_time_s") or 1.0))
    ship_b_reqs = same_planet_building_requirements(ship_key)
    ship_r_reqs = research_requirements([ship_key])

    speedup = max(
        0.1,
        game_data.safe_finite_float(
            inputs.get("universe_speedup_factor"),
            float(getattr(state, "speedup_multiplier", None) or 1.0) if state is not None else 1.0,
        ),
    )

    own_res_raw = inputs.get("own_research_levels") if isinstance(inputs.get("own_research_levels"), Mapping) else {}
    state_res_raw = (state.researches or {}) if state is not None else {}
    cur_res: dict[str, int] = {}
    tgt_res: dict[str, int] = {}
    planned_res_raw = result.get("planned_research_levels")
    planned_res_map: dict[str, int] = {}
    if isinstance(planned_res_raw, Mapping):
        for rk, rv in planned_res_raw.items():
            planned_res_map[str(rk)] = max(0, game_data.safe_int(rv, 0))
    elif isinstance(planned_res_raw, (list, tuple)):
        for row in planned_res_raw:
            if isinstance(row, Mapping):
                rk = str(row.get("key") or row.get("research_key") or "")
                if rk:
                    planned_res_map[rk] = max(0, game_data.safe_int(row.get("planned_level", row.get("level", 0)), 0))
    for rk, _info in research_catalog():
        c_lvl = max(
            0,
            game_data.safe_int(state_res_raw.get(rk, own_res_raw.get(rk, 0)), 0),
        )
        cur_res[rk] = c_lvl
        tgt_res[rk] = max(c_lvl, planned_res_map.get(rk, c_lvl))

    all_bkeys = tuple(f"b{bid}" for bid in sorted(game_data.BUILDING_BY_ID))
    b_base_times: dict[str, dict[int, float]] = {
        f"b{bid}": build_monotonic_base_times(
            info.get("levels") or {},
            int(info.get("max_table_level") or len(info.get("levels") or {})),
        )
        for bid, info in game_data.BUILDING_BY_ID.items()
    }
    r_base_times: dict[str, dict[int, float]] = {
        rk: build_monotonic_base_times(
            info.get("levels") or {},
            int(info.get("max_table_level") or len(info.get("levels") or {})),
        )
        for rk, info in research_catalog()
    }

    live_planets_by_id = (
        {int(p.planet_id): p for p in (state.planets or ()) if int(p.planet_id) > 0} if state is not None else {}
    )
    raw_planets = result.get("planets") if isinstance(result.get("planets"), (list, tuple)) else []

    sim_planets: list[dict[str, Any]] = []
    initial_stock_sp = 0.0
    for idx, entry in enumerate(raw_planets):
        if not isinstance(entry, Mapping):
            continue
        pid = game_data.safe_int(entry.get("planet_id"), 0)
        is_ph = bool(entry.get("is_placeholder", False))
        live_p = live_planets_by_id.get(pid) if (pid > 0 and not is_ph) else None
        raw_built = entry.get("built_floors") if isinstance(entry.get("built_floors"), Mapping) else {}
        cur_b: dict[str, int] = {}
        for bk in all_bkeys:
            if is_ph:
                cur_b[bk] = 0
            elif live_p is not None:
                cur_b[bk] = max(0, int(live_p.buildings.get(bk, 0) or 0))
            else:
                cur_b[bk] = max(0, game_data.safe_int(raw_built.get(bk, 0), 0))
        tgt_b = _extract_planned_building_map_from_entry(entry, cur_b)
        temp_c = int(
            live_p.temperature
            if live_p is not None
            else game_data.safe_int(entry.get("temperature_c", entry.get("temperature", 0)), 0)
        )
        f_max = max(
            1,
            int(
                live_p.fields_max
                if live_p is not None and int(entry.get("fields_max", 0) or 0) <= int(live_p.fields_max)
                else game_data.safe_int(entry.get("fields_max", 300), 300)
            ),
        )
        solar_cells = max(
            0,
            int(live_p.ships.get("s9", 0) or 0)
            if live_p is not None
            else int((meta_params.get("min_fleet_per_planet") or {}).get("s9", 0) or 0),
        )
        if live_p is not None and getattr(live_p, "resources", None) is not None:
            res_obj = live_p.resources
            initial_stock_sp += float(
                game_data.spice_equivalent(
                    {
                        "pig_iron": max(0, int(getattr(res_obj, "pig_iron", 0) or 0)),
                        "metal": max(0, int(getattr(res_obj, "metal", 0) or 0)),
                        "kryptonite": max(0, int(getattr(res_obj, "kryptonite", 0) or 0)),
                        "spice": max(0, int(getattr(res_obj, "spice", 0) or 0)),
                    }
                )
            )
        sim_planets.append(
            {
                "idx": len(sim_planets),
                "planet_id": pid,
                "is_placeholder": is_ph,
                "temperature_c": temp_c,
                "fields_max": f_max,
                "solar_cells": solar_cells,
                "cur": cur_b,
                "tgt": tgt_b,
            }
        )

    total_upgrade_levels = int(
        sum(max(0, int(sp["tgt"][bk]) - int(sp["cur"][bk])) for sp in sim_planets for bk in all_bkeys)
        + sum(max(0, int(tgt_res[rk]) - int(cur_res[rk])) for rk in tgt_res)
    )
    completed_upgrade_levels = 0

    merchant_sp = game_data.MERCHANT_RATES_IN_SPICE
    b_prereqs_map = {
        bk: {rk_b: rl_b for rk_b, rl_b in same_planet_building_requirements(bk).items() if rk_b != bk}
        for bk in all_bkeys
    }
    b_r_prereqs_map = {bk: research_requirements([bk]) for bk in all_bkeys}
    req_b8_for_ship = max(
        [1, *(int(pr_l) for srk in ship_r_reqs for pr_k, pr_l in TECH_GRAPH.prereqs_of.get(srk, ()) if pr_k == "b8")]
    )
    flow_cache: dict[tuple[int, int, int, str, int], list[float]] = {}
    econ_cache: dict[tuple[int, int, int, int, int], float] = {}
    b_cost_cache: dict[tuple[str, int], float] = {}
    r_cost_cache: dict[tuple[str, int], float] = {}
    solar_unit_by_temp: dict[int, float] = {
        int(sp["temperature_c"]): solar_cell_energy_per_unit(float(sp["temperature_c"])) for sp in sim_planets
    }

    def _cached_flows(temp_c: int, r1_lvl: int, r2_lvl: int, bk: str, lvl: int) -> list[float]:
        ck = (temp_c, r1_lvl, r2_lvl, bk, lvl)
        hit = flow_cache.get(ck)
        if hit is None:
            hit = building_level_flows(
                bk,
                lvl,
                {
                    "speedup_factor": speedup,
                    "r1_mining_tech": r1_lvl,
                    "r2_energy_tech": r2_lvl,
                    "planet_temperature_c": temp_c,
                },
            )
            flow_cache[ck] = hit
        return hit

    def _cached_net_sp_hr(temp_c: int, r1_lvl: int, r2_lvl: int, bid: int, lvl: int) -> float:
        ck = (temp_c, r1_lvl, r2_lvl, bid, lvl)
        hit = econ_cache.get(ck)
        if hit is None:
            hit = float(
                building_level_economy(
                    bid,
                    lvl,
                    {
                        "speedup_factor": speedup,
                        "r1_mining_tech": r1_lvl,
                        "r2_energy_tech": r2_lvl,
                        "planet_temperature_c": temp_c,
                    },
                )["net_spice_eq_hr"]
            )
            econ_cache[ck] = hit
        return hit

    def _eval_snapshot() -> dict[str, Any]:
        r1_lvl = int(cur_res.get("r1", 0))
        r2_lvl = int(cur_res.get("r2", 0))
        r2_mult = technology_multiplier("r2", r2_lvl)
        res_unlocked = all(int(cur_res.get(rk, 0)) >= int(rlvl) for rk, rlvl in ship_r_reqs.items())
        emp_net_hr = {r: 0.0 for r in RESOURCE_KEYS}
        shipyard_wk = 0.0
        planet_power_deficits: list[bool] = []
        planet_smelt_deficits: list[bool] = []
        for sp in sim_planets:
            t_c = int(sp["temperature_c"])
            cb = sp["cur"]
            t0 = t1 = t2 = t3 = t4 = t5 = t6 = t7 = 0.0
            for bk in all_bkeys:
                lvl = int(cb.get(bk, 0))
                if lvl > 0:
                    fl = _cached_flows(t_c, r1_lvl, r2_lvl, bk, lvl)
                    t0 += fl[0]
                    t1 += fl[1]
                    t2 += fl[2]
                    t3 += fl[3]
                    t4 += fl[4]
                    t5 += fl[5]
                    t6 += fl[6]
                    t7 += fl[7]
            if sp["solar_cells"]:
                t6 += float(sp["solar_cells"]) * solar_unit_by_temp[t_c] * r2_mult
            e_prod = t6
            e_used = t7
            p_ratio = min(1.0, e_prod / e_used) if e_used > 0.0 else 1.0
            planet_power_deficits.append(e_used > e_prod + 1e-6)

            pig_mined = t0 * p_ratio
            pig_wanted = t4 * p_ratio
            s_ratio = min(1.0, pig_mined / pig_wanted) if pig_wanted > 0.0 else 1.0
            planet_smelt_deficits.append(pig_wanted > pig_mined + 1e-6)

            emp_net_hr["pig_iron"] += pig_mined - pig_wanted * s_ratio
            emp_net_hr["metal"] += t1 * p_ratio * s_ratio
            emp_net_hr["kryptonite"] += t2 * p_ratio
            emp_net_hr["spice"] += t3 * p_ratio - t5

            if res_unlocked and all(int(cb.get(bk, 0)) >= int(req_l) for bk, req_l in ship_b_reqs.items()):
                shipyard_wk += (
                    seconds_per_week
                    * shipyard_time_divisor(int(cb.get("b9", 0)), int(cb.get("b17", 0)), speedup)
                    / base_ship_time_s
                )

        res_limits_wk = {r: max(0.0, emp_net_hr[r] * 168.0 / ship_cost_map[r]) for r in cost_resources}
        res_cap_wk = min(res_limits_wk.values()) if res_limits_wk else 0.0
        ships_wk = min(res_cap_wk, shipyard_wk)
        all_limits = {**res_limits_wk, "shipyards": shipyard_wk}
        bottleneck = min(all_limits, key=lambda k: all_limits[k]) if all_limits else "shipyards"
        sp_per_hr = sum(max(0.0, emp_net_hr[r]) / float(merchant_sp[r]) for r in RESOURCE_KEYS)
        res_bottleneck = min(res_limits_wk, key=lambda k: res_limits_wk[k]) if res_limits_wk else "metal"
        return {
            "ships_per_week": ships_wk,
            "resource_cap_per_week": res_cap_wk,
            "shipyard_cap_per_week": shipyard_wk,
            "bottleneck": bottleneck,
            "resource_bottleneck": res_bottleneck,
            "sp_per_s": max(5.0 / 3600.0, sp_per_hr / 3600.0),
            "power_deficits": planet_power_deficits,
            "smelt_deficits": planet_smelt_deficits,
            "res_unlocked": res_unlocked,
        }

    def _b_cost_sp(bk: str, lvl: int) -> float:
        ck = (bk, lvl)
        hit = b_cost_cache.get(ck)
        if hit is None:
            hit = max(1.0, float(game_data.spice_equivalent(full_cost(game_data.building_cost(int(bk[1:]), lvl)))))
            b_cost_cache[ck] = hit
        return hit

    def _b_dur_s(sp: Mapping[str, Any], bk: str, lvl: int) -> float:
        cb = sp["cur"]
        div = building_time_divisor(int(cb.get("b7", 0)), int(cb.get("b17", 0)), speedup)
        return max(1.0, float(b_base_times[bk].get(lvl, 1.0)) / div)

    def _r_cost_sp(rk: str, lvl: int) -> float:
        ck = (rk, lvl)
        hit = r_cost_cache.get(ck)
        if hit is None:
            row = (game_data.RESEARCH_BY_ID[rk].get("levels") or {}).get(lvl) or {}
            hit = max(1.0, float(game_data.spice_equivalent(full_cost(row.get("cost")))))
            r_cost_cache[ck] = hit
        return hit

    def _r_dur_s(rk: str, lvl: int) -> float:
        best_b8 = max((int(sp["cur"].get("b8", 0)) for sp in sim_planets), default=0)
        div = research_time_divisor(best_b8, speedup)
        return max(1.0, float(r_base_times[rk].get(lvl, 1.0)) / div)

    # Target steady-state weekly output
    hours_per_month = max(1.0, game_data.safe_finite_float(summary.get("hours_per_month"), 720.0))
    summary_ships_mo = max(0.0, game_data.safe_finite_float(summary.get("ships_per_month"), 0.0))
    target_ships_wk = summary_ships_mo * (168.0 / hours_per_month)

    t_now = 0.0
    stock_sp = initial_stock_sp
    planet_busy: list[bool] = [False] * len(sim_planets)
    research_busy = False
    # Heap entries: `(finish_s, seq, kind, p_idx, key, next_lvl)`
    in_flight: list[tuple[float, int, str, int, str, int]] = []
    ev_seq = 0
    snap = _eval_snapshot()
    current_ships_wk = float(snap["ships_per_week"])

    def _pick_best_candidate(cur_snap: Mapping[str, Any]) -> Optional[tuple[float, float, float, str, int, str, int]]:
        """Return `(score, cost_sp, dur_s, kind, p_idx, key, next_lvl)` across idle queues."""
        best: Optional[tuple[float, float, float, str, int, str, int]] = None
        bn = str(cur_snap["bottleneck"])
        res_bn = str(cur_snap.get("resource_bottleneck") or bn)
        eff_res_bn = res_bn if float(cur_snap["shipyard_cap_per_week"]) <= 0.0 else bn
        sp_s = float(cur_snap["sp_per_s"])
        power_defs = cur_snap["power_deficits"]
        smelt_defs = cur_snap["smelt_deficits"]

        for p_idx, sp in enumerate(sim_planets):
            if planet_busy[p_idx]:
                continue
            cb = sp["cur"]
            tb = sp["tgt"]
            fields_used = sum(cb.values())

            # Demolish off-target structure ONLY when colony is out of fields and still has builds pending
            has_builds_left = any(int(tb[bk]) > int(cb[bk]) for bk in all_bkeys)
            if has_builds_left and fields_used >= int(sp["fields_max"]):
                dem_cands = [bk for bk in all_bkeys if int(cb[bk]) > int(tb[bk])]
                if dem_cands:
                    bk_d = dem_cands[0]
                    nxt = int(cb[bk_d]) - 1
                    dur = _b_dur_s(sp, bk_d, int(cb[bk_d]))
                    cand = (1e9, 0.0, dur, "demolish", p_idx, bk_d, nxt)
                    if best is None or cand[0] > best[0]:
                        best = cand
                    continue

            for bk in all_bkeys:
                c_l = int(cb[bk])
                t_l = int(tb[bk])
                if c_l >= t_l:
                    continue
                n_l = c_l + 1
                # Check same-planet building prerequisites (excluding self)
                reqs = b_prereqs_map[bk]
                if any(int(cb.get(rk_b, 0)) < int(rl_b) for rk_b, rl_b in reqs.items()):
                    continue
                # Check research prerequisites for building
                r_reqs = b_r_prereqs_map[bk]
                if any(int(cur_res.get(rk_r, 0)) < int(rl_r) for rk_r, rl_r in r_reqs.items()):
                    continue

                cost = _b_cost_sp(bk, n_l)
                dur = _b_dur_s(sp, bk, n_l)
                eff_dt = max(60.0, dur + cost / max(1e-3, sp_s))

                # Priority weight based on role, ROI payback, & active empire bottleneck
                planet_builds_ship = all(int(tb.get(sbk, 0)) >= int(srl) for sbk, srl in ship_b_reqs.items())
                weight = 1.0
                if bk in ("b5", "b6") and power_defs[p_idx]:
                    weight = 80_000.0
                elif planet_builds_ship and bk in ship_b_reqs and c_l < int(ship_b_reqs[bk]):
                    weight = 30_000.0
                elif (
                    bk == "b8"
                    and any(int(cur_res.get(srk, 0)) < int(srl) for srk, srl in ship_r_reqs.items())
                    and c_l < req_b8_for_ship
                ):
                    weight = 32_000.0
                elif bk == "b7" and c_l < min(t_l, 12):
                    weight = 1600.0
                elif bk == "b17":
                    weight = 2600.0 if bn == "shipyards" else 420.0
                elif bk == "b8" and any(int(cur_res[rk]) < int(tgt_res[rk]) for rk in tgt_res):
                    weight = 750.0
                elif bk in ("b1", "b2", "b3", "b4"):
                    bid_m = int(bk[1:])
                    t_c_m = int(sp["temperature_c"])
                    r1_m = int(cur_res.get("r1", 0))
                    r2_m = int(cur_res.get("r2", 0))
                    gain_sp_hr = max(
                        0.0,
                        _cached_net_sp_hr(t_c_m, r1_m, r2_m, bid_m, n_l)
                        - _cached_net_sp_hr(t_c_m, r1_m, r2_m, bid_m, c_l),
                    )
                    payback_hr = (cost / gain_sp_hr) if gain_sp_hr > 1e-3 else 1e6
                    roi_boost = 1800.0 / max(1.0, math.sqrt(payback_hr / 24.0))
                    is_active_bn = (
                        (bk == "b1" and (smelt_defs[p_idx] or eff_res_bn in ("pig_iron", "metal")))
                        or (bk == "b2" and eff_res_bn == "metal")
                        or (bk == "b3" and eff_res_bn == "kryptonite")
                        or (bk == "b4" and eff_res_bn == "spice")
                    )
                    if is_active_bn:
                        weight = 2400.0 + 2.0 * roi_boost
                    else:
                        weight = 180.0 + 0.4 * roi_boost
                elif bk == "b9":
                    weight = 2400.0 if bn == "shipyards" else 140.0
                elif bk in ("b5", "b6"):
                    weight = 120.0
                elif bk in WAREHOUSE_KEYS:
                    weight = 15.0
                else:
                    weight = 35.0

                score = weight / eff_dt
                cand = (score, cost, dur, "build", p_idx, bk, n_l)
                if best is None or cand[0] > best[0]:
                    best = cand

        if not research_busy and any(int(sp["cur"].get("b8", 0)) > 0 for sp in sim_planets):
            best_lab_b8 = max((int(sp["cur"].get("b8", 0)) for sp in sim_planets), default=0)
            for rk, t_l in tgt_res.items():
                c_l = int(cur_res.get(rk, 0))
                if c_l >= t_l:
                    continue
                n_l = c_l + 1
                prereqs_r = TECH_GRAPH.prereqs_of.get(rk, ())
                req_b8 = max([1, *(int(pr_l) for pr_k, pr_l in prereqs_r if pr_k == "b8")])
                if best_lab_b8 < req_b8:
                    continue
                if any(int(cur_res.get(pr_k, 0)) < int(pr_l) for pr_k, pr_l in prereqs_r if pr_k.startswith("r")):
                    continue
                cost = _r_cost_sp(rk, n_l)
                dur = _r_dur_s(rk, n_l)
                eff_dt = max(60.0, dur + cost / max(1e-3, sp_s))
                if rk in ship_r_reqs and c_l < int(ship_r_reqs[rk]):
                    # Hard unlock tech for target ship: high priority once affordable within ~14d
                    afford_days = (cost / max(1e-3, sp_s)) / 86400.0
                    unlock_damp = 1.0 / (1.0 + max(0.0, afford_days - 14.0) * 0.25)
                    weight = 25_000.0 * unlock_damp
                else:
                    afford_days = (cost / max(1e-3, sp_s)) / 86400.0
                    afford_damp = 1.0 / (1.0 + max(0.0, afford_days - 7.0))
                    if rk in ("r1", "r2"):
                        weight = 650.0 * afford_damp
                    elif rk == "r3":
                        weight = 600.0 * afford_damp
                    else:
                        weight = 80.0 * afford_damp
                score = weight / eff_dt
                cand = (score, cost, dur, "research", -1, rk, n_l)
                if best is None or cand[0] > best[0]:
                    best = cand

        return best

    def _pop_until(target_t: float) -> None:
        nonlocal t_now, stock_sp, research_busy, snap, completed_upgrade_levels
        while in_flight and in_flight[0][0] <= target_t + 1e-6:
            fin_s, _seq, kind, p_idx, key, nxt_lvl = heapq.heappop(in_flight)
            if fin_s > t_now:
                stock_sp += (fin_s - t_now) * float(snap["sp_per_s"])
                t_now = fin_s
            if kind == "research":
                cur_res[key] = max(int(cur_res.get(key, 0)), int(nxt_lvl))
                research_busy = False
                completed_upgrade_levels += 1
            elif kind == "demolish":
                sim_planets[p_idx]["cur"][key] = int(nxt_lvl)
                planet_busy[p_idx] = False
            else:
                sim_planets[p_idx]["cur"][key] = int(nxt_lvl)
                planet_busy[p_idx] = False
                completed_upgrade_levels += 1
            snap = _eval_snapshot()

    weeks_out: list[dict[str, Any]] = []
    weeks_to_90: Optional[int] = None
    weeks_to_full: Optional[int] = None
    safety_guard = 0

    for wk in range(0, total_weeks + 1):
        sample_t = float(wk) * seconds_per_week
        while t_now < sample_t - 1e-6 and safety_guard < 50_000:
            safety_guard += 1
            # Start any immediately affordable jobs at `t_now`
            cand = _pick_best_candidate(snap)
            if cand is not None and stock_sp >= cand[1] * (1.0 - 1e-9) - 1.0:
                _sc, cost, dur, kind, p_idx, key, nxt_lvl = cand
                stock_sp = max(0.0, stock_sp - cost)
                if kind == "research":
                    research_busy = True
                else:
                    planet_busy[p_idx] = True
                ev_seq += 1
                heapq.heappush(in_flight, (t_now + dur, ev_seq, kind, p_idx, key, nxt_lvl))
                continue

            next_fin = in_flight[0][0] if in_flight else math.inf
            if cand is not None:
                dt_afford = max(0.0, (cand[1] - stock_sp) / max(1e-4, float(snap["sp_per_s"])))
                t_afford = t_now + dt_afford
            else:
                t_afford = math.inf

            next_event_t = min(sample_t, next_fin, t_afford)
            if not math.isfinite(next_event_t):
                break
            if in_flight and next_fin <= next_event_t + 1e-6 and next_fin <= sample_t:
                _pop_until(next_fin)
            elif cand is not None and t_afford <= sample_t and t_afford <= next_fin:
                t_now = max(t_now, t_afford)
                _sc, cost, dur, kind, p_idx, key, nxt_lvl = cand
                stock_sp = 0.0
                if kind == "research":
                    research_busy = True
                else:
                    planet_busy[p_idx] = True
                ev_seq += 1
                heapq.heappush(in_flight, (t_now + dur, ev_seq, kind, p_idx, key, nxt_lvl))
            else:
                stock_sp += max(0.0, sample_t - t_now) * float(snap["sp_per_s"])
                t_now = sample_t
                break

        _pop_until(sample_t)
        # Kick off any zero-wait builds right at `t = 0` so Week 1 reflects immediate colony starts
        if wk == 0:
            while safety_guard < 50_000:
                cand0 = _pick_best_candidate(snap)
                if cand0 is None or stock_sp + 1e-6 < cand0[1]:
                    break
                safety_guard += 1
                _sc, cost, dur, kind, p_idx, key, nxt_lvl = cand0
                stock_sp = max(0.0, stock_sp - cost)
                if kind == "research":
                    research_busy = True
                else:
                    planet_busy[p_idx] = True
                ev_seq += 1
                heapq.heappush(in_flight, (t_now + dur, ev_seq, kind, p_idx, key, nxt_lvl))

        ships_wk = float(snap["ships_per_week"])
        if target_ships_wk > 0.0:
            ships_wk = min(ships_wk, target_ships_wk) if completed_upgrade_levels >= total_upgrade_levels else ships_wk
            if completed_upgrade_levels >= total_upgrade_levels and not in_flight:
                ships_wk = target_ships_wk
        comp_pct = (
            100.0
            if total_upgrade_levels <= 0
            else round(min(100.0, 100.0 * float(completed_upgrade_levels) / float(total_upgrade_levels)), 1)
        )
        if weeks_to_90 is None and target_ships_wk > 0.0 and ships_wk >= 0.90 * target_ships_wk:
            weeks_to_90 = wk
        if weeks_to_full is None and (
            completed_upgrade_levels >= total_upgrade_levels
            or (target_ships_wk > 0.0 and ships_wk >= 0.999 * target_ships_wk)
        ):
            weeks_to_full = wk

        yr_num = 1 if wk == 0 else ((wk - 1) // 52) + 1
        wk_in_yr = 0 if wk == 0 else ((wk - 1) % 52) + 1
        weeks_out.append(
            {
                "week": wk,
                "year": yr_num,
                "week_in_year": wk_in_yr,
                "ships_per_week": round(ships_wk, 2),
                "resource_cap_per_week": round(float(snap["resource_cap_per_week"]), 2),
                "shipyard_cap_per_week": round(float(snap["shipyard_cap_per_week"]), 2),
                "bottleneck": str(snap["bottleneck"]),
                "completion_pct": comp_pct,
            }
        )

    years_out: list[dict[str, Any]] = []
    cumulative_ships = 0.0
    for yr in range(1, years_n + 1):
        w_start = (yr - 1) * 52
        w_end = yr * 52
        slice_wks = weeks_out[w_start + 1 : w_end + 1]
        start_rate = float(weeks_out[w_start]["ships_per_week"])
        end_rate = float(slice_wks[-1]["ships_per_week"]) if slice_wks else start_rate
        avg_rate = sum(float(w["ships_per_week"]) for w in slice_wks) / len(slice_wks) if slice_wks else start_rate
        peak_rate = max((float(w["ships_per_week"]) for w in slice_wks), default=start_rate)
        yr_ships = sum(float(w["ships_per_week"]) for w in slice_wks)
        cumulative_ships += yr_ships
        end_row = slice_wks[-1] if slice_wks else weeks_out[w_start]
        years_out.append(
            {
                "year": yr,
                "label": f"Year {yr}",
                "start_ships_per_week": round(start_rate, 2),
                "avg_ships_per_week": round(avg_rate, 2),
                "end_ships_per_week": round(end_rate, 2),
                "peak_ships_per_week": round(peak_rate, 2),
                "delta_ships_per_week": round(end_rate - start_rate, 2),
                "total_ships_produced": round(yr_ships, 1),
                "cumulative_ships_produced": round(cumulative_ships, 1),
                "bottleneck": str(end_row["bottleneck"]),
                "completion_pct": float(end_row["completion_pct"]),
            }
        )
    return _json_ready(
        {
            "target_ship_key": ship_key,
            "target_ship": ship_name,
            "horizon_years": years_n,
            "total_weeks": total_weeks,
            "current_ships_per_week": round(current_ships_wk, 2),
            "target_ships_per_week": round(target_ships_wk, 2),
            "target_ships_per_month": round(summary_ships_mo, 2),
            "total_upgrade_levels": total_upgrade_levels,
            "weeks_to_90_pct": weeks_to_90,
            "weeks_to_full_target": weeks_to_full,
            "years_to_full_target": round(weeks_to_full / 52.0, 2) if weeks_to_full is not None else None,
            "years": years_out,
            "weeks": weeks_out,
        }
    )
