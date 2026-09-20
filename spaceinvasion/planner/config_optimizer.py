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
import json
import math
import time
from typing import Any, Optional

import numpy as np

from spaceinvasion.combat.fleet import DEFENSE_SLUG_BY_KEY, SHIP_SLUG_BY_KEY, normalize_coords
from spaceinvasion.core import game_data
from spaceinvasion.core.state import (
    EnergyBalance,
    GameState,
    PlanetState,
    Resources,
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
LOCKABLE_PLANET_SPECIALIZATIONS: tuple[str, ...] = (
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
    "max_planets": 11,
    "empty_colony_fields": 300,
    "reserved_floors_by_planet": {},
    "planet_specializations": {},
    "planet_names": {},
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
    "PLANET_SPECIALIZATIONS": "planet_specializations",
    "PLANET_NAMES": "planet_names",
    "CUSTOM_PLANET_NAMES": "planet_names",
    "custom_planet_names": "planet_names",
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
    return {
        resource: max(0, game_data.safe_int(src.get(resource, 0), 0))
        for resource in RESOURCE_KEYS
    }


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
            game_data.mine_production_per_hour(
                int(building_id), lvl, temperature=temperature, mining_tech_level=0
            )
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
    return float(base_times[level]) / building_time_divisor(
        dev_center_level, microsystem_level, speedup_factor
    )


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
            raise ValueError(
                f"Cycle detected in technology requirement graph: {sorted(set(self.nodes) - set(order))}"
            )
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
            elif key == "hill_climb_step_sizes" and isinstance(raw_val, (list, tuple)):
                steps = [max(1, int(x)) for x in raw_val]
                merged[key] = steps if steps else [1, 2, 4, 8]
            else:
                merged[key] = raw_val

    merged["target_ship_key"] = str(merged["target_ship_key"])
    merged["production_month_days"] = float(merged["production_month_days"])
    merged["research_percent_of_universe_max"] = float(merged["research_percent_of_universe_max"])
    merged["warehouse_hours_of_production"] = float(merged["warehouse_hours_of_production"])
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
    merged["max_planets"] = max(1, int(merged["max_planets"]))
    merged["empty_colony_fields"] = max(1, int(merged["empty_colony_fields"]))
    merged["planet_specializations"] = normalize_planet_specializations_map(merged.get("planet_specializations"))
    merged["planet_names"] = normalize_planet_names_map(merged.get("planet_names"))
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
        lk_norm = normalize_coords(lk) if (":" in lk and not lk.startswith("EMPTY:") and not lk.startswith("slot:")) else lk
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
        norm_coords = (
            normalize_coords(raw_coords)
            if raw_coords and not raw_coords.startswith("EMPTY:")
            else raw_coords
        )
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
    ]
    if any(int(count) != count or count < 0 for count in counts):
        raise ValueError("Building levels and fleet sizes must be whole numbers of 0 or more")
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
    central_min_warehouse_level: Optional[int] = None,
    specialization: str = "auto",
    display_name: Optional[str] = None,
    game_name: Optional[str] = None,
) -> dict[str, Any]:
    """Fixed part, hard built/reserved floors, specialization lock, and search range of one planet."""
    all_keys = (*misc_levels, *optimized_max_levels)
    built = {key: max(0, int(planet.buildings.get(key, 0) or 0)) for key in all_keys}
    reserved = {key: max(0, int(reserved_floors.get(key, 0) or 0)) for key in all_keys}
    reserved_nonzero = {key: lvl for key, lvl in reserved.items() if lvl > 0}
    locked_spec = normalize_planet_specialization(specialization)
    allowed_for_spec = SPECIALIZATION_ALLOWED_BUILDINGS.get(locked_spec)

    wanted = {key: max(int(level), built[key], reserved[key]) for key, level in misc_levels.items()}
    required: dict[str, int] = {}
    for needs in (
        *(same_planet_building_requirements(ship) for ship, count in min_fleet.items() if count > 0),
        *(same_planet_building_requirements(key, level) for key, level in wanted.items() if level > 0),
        *(
            same_planet_building_requirements(key, reserved[key])
            for key in optimized_max_levels
            if reserved[key] > built[key]
        ),
    ):
        for key, level in needs.items():
            required[key] = max(required.get(key, 0), level)

    fixed_levels = {
        key: max(level, required.get(key, 0)) for key, level in wanted.items() if key not in WAREHOUSE_KEYS
    }

    min_levels: dict[str, int] = {}
    max_levels: dict[str, int] = {}
    locked_by: dict[str, str] = {}
    for key, max_level in optimized_max_levels.items():
        unmet = [
            f"{source} L{level}"
            for source, level in same_planet_building_requirements(key).items()
            if fixed_levels.get(source, level) < level
        ]
        # HARD REQUIREMENT: always enforce built level and reserved floor on every optimized building
        hard_floor = max(built[key], reserved[key])
        min_levels[key] = max(required.get(key, 0), hard_floor)
        top_allowed = 0 if unmet else min(int(max_level), catalog_top_level(key))
        if allowed_for_spec is not None and key not in allowed_for_spec:
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
    planet_specializations: Mapping[str, str] | None = None,
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
                central_min_warehouse_level=(
                    central_min_warehouse_level if slot_index == central_planet_slot else None
                ),
                specialization=locked_spec,
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
                "lower misc_building_levels, central_planet_min_warehouse_level, or warehouse_hours_of_production"
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
        totals = (
            self.fixed_flows[:, None, planets_idx]
            + self.flow_tables[:, planet_axis, building_axis, levels].sum(axis=3)
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
    current_score = float(score(model.simulate(current[None, None, :], [planet])["planet_limits"][:, 0])[0])
    current_rank = (current_score, -int(current.sum()))
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
        level_totals = candidates.sum(axis=1)
        best = int(np.lexsort((-level_totals, scores))[-1])
        best_rank = (float(scores[best]), -int(level_totals[best]))
        if not best_rank > current_rank:
            return current
        current, current_rank = candidates[best], best_rank


def coarse_designs(model: EmpireProductionModel, planet: int, grid_size: int) -> tuple[np.ndarray, np.ndarray]:
    """Coarse grid of at most ~`grid_size` feasible designs on one planet and their limits."""
    lower, upper = model.min_levels[planet], model.max_levels[planet]
    values_per_building = max(2, int(grid_size ** (1.0 / len(lower))))
    axes = [
        np.unique(np.linspace(low, high, values_per_building).round().astype(np.int64))
        for low, high in zip(lower, upper)
    ]
    grid = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, len(lower))
    outcome = model.simulate(grid[:, None, :], [planet])
    fits = outcome["planet_fits"][:, 0]
    return grid[fits], outcome["planet_limits"][fits, 0]


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
    """Polish directly and via `balanced_score`, keeping whichever builds more ships (fewer levels on ties)."""
    direct = polish_configuration(model, levels, smallest_limit, step_sizes)
    rebalanced = polish_configuration(
        model,
        polish_configuration(model, levels, balanced_score, step_sizes),
        smallest_limit,
        step_sizes,
    )
    ships = model.ships_per_month(np.array([direct, rebalanced]))
    return rebalanced if (ships[1], -int(rebalanced.sum())) > (ships[0], -int(direct.sum())) else direct


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


def search_in_rounds(
    model: EmpireProductionModel,
    *,
    progress_cb: Optional[Callable[[str, float], None]] = None,
    **search_settings: Any,
) -> tuple[Any, ...]:
    """Repeat search rounds until the central planet's warehouses hold empire production."""
    reserve = np.zeros(len(RESOURCE_KEYS), dtype=np.float64)
    rounds: list[dict[str, Any]] = []
    max_rounds = 12
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
        held = (
            round_model.warehouse_capacities[warehouses[round_model.central_planet]]
            if round_model.central_planet >= 0
            else np.full(len(RESOURCE_KEYS), np.inf)
        )
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
            return model.with_central_reserve(needed), rounds, *result
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
        (float(shipyard_ships) > 0.0)
        and (best_mine_delta <= 0.0)
        and (b17 >= 2 or (b9 >= 12 and top_mine_lvl < 15.0))
    )

    if active_shipyard_growth or static_shipyard_role:
        if b4 >= 12 and (top_mine_role == "spice" or d_b4 >= 4 or float(b4) >= top_mine_lvl - 6.0):
            return "spice_shipyard"
        if top_mine_lvl <= 15.0 or best_mine_delta <= 2.0 or (shipyard_delta >= best_mine_delta and top_mine_delta <= 8.0):
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

    own_researches = {
        key: max(0, int((state.researches or {}).get(key, 0) or 0))
        for key, _ in research_catalog()
    }
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
        planet_specializations=cfg["planet_specializations"],
        planet_names=cfg["planet_names"],
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
        levels_to_build = int(
            sum(max(0, planned_map[key] - plan["built_levels"][key]) for key in planned_map)
        )
        upgrade_cost_sp = round(
            sum(
                building_upgrade_cost_spice_eq(key, plan["built_levels"][key], planned_map[key])
                for key in planned_map
            ),
            2,
        )
        fleet_cost_sp = round(float(plan["fleet_cost_spice_eq"]), 2)
        pig_hr = round(float(outcome["planet_net_per_hour"][index, 0]), 1)

        total_fields_used += fields_used_target
        total_fields_free += fields_free
        total_warehouse_fields += warehouse_fields
        total_levels_to_build += levels_to_build
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
                    f"b{bid}": int(plan["built_levels"][f"b{bid}"])
                    for bid in sorted(game_data.BUILDING_BY_ID)
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
        planet_records[best_model.central_planet]
        if 0 <= best_model.central_planet < len(planet_records)
        else None
    )
    resolved_planet_names = {
        str(rec["planet_id"]): str(rec["name"])
        for rec in planet_records
        if int(rec["planet_id"]) > 0
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


def export_config_optimizer_json(result: Mapping[str, Any]) -> str:
    """Serialize a `run_config_optimizer` result into strictly finite indented JSON."""
    return json.dumps(_json_ready(result), indent=2, ensure_ascii=False, allow_nan=False) + "\n"
