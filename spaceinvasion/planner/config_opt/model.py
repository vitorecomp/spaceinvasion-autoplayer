"""Vectorized steady-state empire production model and upgrade cost tables."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import copy

import numpy as np

from spaceinvasion.core.coercion import coerce_finite_float, coerce_int
from spaceinvasion.planner.config_opt._model_plans import (
    build_planet_plan,
    build_planet_plans,
    plan_research_levels,
)
from spaceinvasion.planner.config_opt._protocols import as_float_dict, as_int_dict, map_int
from spaceinvasion.planner.config_opt.catalog_math import (
    building_cost_map,
    building_level_flows,
    catalog_top_level,
    full_cost,
    ship_info,
    shipyard_time_divisor,
    solar_cell_energy_per_unit,
    technology_multiplier,
    warehouse_capacity,
)
from spaceinvasion.planner.config_opt.constants import (
    PRODUCTION_FLOW_KEYS,
    RESOURCE_KEYS,
    WAREHOUSE_KEYS,
    BoolArray,
    FloatArray,
    IntArray,
)
from spaceinvasion.planner.config_opt.tech_graph import same_planet_building_requirements


def upgrade_cost_table(key: str, built_level: int, top_level: int) -> FloatArray:
    """``table[level, resource]``: largest single-level cost from ``built_level + 1`` to ``level``."""
    table: FloatArray = np.zeros((top_level + 1, len(RESOURCE_KEYS)), dtype=np.float64)
    for level in range(int(built_level) + 1, int(top_level) + 1):
        cost = full_cost(building_cost_map(int(key[1:]), level))
        table[level] = np.maximum(table[level - 1], [float(cost[resource]) for resource in RESOURCE_KEYS])
    return table


def upgrade_cost_tables(
    planet_plans: Sequence[Mapping[str, object]],
    keys: tuple[str, ...],
    top_level: int,
) -> FloatArray:
    """``tables[planet, building, level, resource]`` across all planets and ``keys``."""
    tables = {
        (key, built_level): upgrade_cost_table(key, built_level, top_level)
        for key in keys
        for built_level in {as_int_dict(plan["built_levels"])[key] for plan in planet_plans}
    }
    return np.array(
        [[tables[key, as_int_dict(plan["built_levels"])[key]] for key in keys] for plan in planet_plans],
        dtype=np.float64,
    )


def _format_model_planet_label(plan: Mapping[str, object]) -> str:
    name = str(plan.get("name") or "").strip()
    coords = str(plan.get("coords") or "").strip()
    slot_idx = plan["slot_index"]
    if name and name != coords:
        return f"{name} [{coords}] (slot {slot_idx})"
    return f"{coords} (slot {slot_idx})"


class EmpireProductionModel:
    """Vectorized steady-state empire output model matching ``config_optimizer.ipynb``."""

    def __init__(
        self,
        planet_plans: list[dict[str, object]],
        planet_contexts: list[dict[str, object]],
        *,
        optimized_keys: tuple[str, ...],
        target_ship_key: str,
        month_days: float,
        warehouse_hours: float,
    ) -> None:
        self.optimized_keys: tuple[str, ...] = tuple(optimized_keys)
        self.target_ship_key: str = target_ship_key
        self.building_index: dict[str, int] = {key: index for index, key in enumerate(self.optimized_keys)}
        self.planet_names: list[str] = [_format_model_planet_label(plan) for plan in planet_plans]
        top_level = self._init_plan_arrays(planet_plans)
        self._init_flow_tables(planet_plans, planet_contexts, top_level)
        self._init_warehouse_and_ship_tables(planet_plans, planet_contexts, top_level, month_days, warehouse_hours)
        self.twin_planets: list[int] = self._compute_twin_planets(len(planet_plans))
        self.check_minimum_designs()

    def _init_plan_arrays(self, planet_plans: Sequence[Mapping[str, object]]) -> int:
        self.min_levels: IntArray = np.array(
            [[as_int_dict(plan["min_levels"])[key] for key in self.optimized_keys] for plan in planet_plans],
            dtype=np.int64,
        )
        self.max_levels: IntArray = np.array(
            [[as_int_dict(plan["max_levels"])[key] for key in self.optimized_keys] for plan in planet_plans],
            dtype=np.int64,
        )
        self.built_opt_levels: IntArray = np.array(
            [[as_int_dict(plan["built_levels"])[key] for key in self.optimized_keys] for plan in planet_plans],
            dtype=np.int64,
        )
        self.fields_max: IntArray = np.array([map_int(plan, "fields_max", 0) for plan in planet_plans], dtype=np.int64)
        self.free_fields: IntArray = np.array(
            [map_int(plan, "free_fields", 0) for plan in planet_plans], dtype=np.int64
        )
        first_fixed = as_int_dict(planet_plans[0]["fixed_levels"])
        self.fixed_levels: dict[str, IntArray] = {
            key: np.array([as_int_dict(plan["fixed_levels"])[key] for plan in planet_plans], dtype=np.int64)
            for key in first_fixed
        }
        return int(self.max_levels.max()) if self.max_levels.size else 0

    def _init_flow_tables(
        self,
        planet_plans: Sequence[Mapping[str, object]],
        planet_contexts: Sequence[Mapping[str, object]],
        top_level: int,
    ) -> None:
        raw_flows: FloatArray = np.array(
            [
                [
                    [building_level_flows(key, level, context) for level in range(top_level + 1)]
                    for key in self.optimized_keys
                ]
                for context in planet_contexts
            ],
            dtype=np.float64,
        )
        self.flow_tables: FloatArray = raw_flows.transpose(3, 0, 1, 2)
        self.fixed_flows: FloatArray = np.zeros((len(PRODUCTION_FLOW_KEYS), len(planet_plans)), dtype=np.float64)
        e_prod_idx = PRODUCTION_FLOW_KEYS.index("energy_produced")
        for planet, (plan, context) in enumerate(zip(planet_plans, planet_contexts, strict=True)):
            for key, level in as_int_dict(plan["fixed_levels"]).items():
                self.fixed_flows[:, planet] += building_level_flows(key, level, context)
            self.fixed_flows[e_prod_idx, planet] += (
                map_int(plan, "solar_cells", 0)
                * solar_cell_energy_per_unit(coerce_finite_float(context["planet_temperature_c"], 0.0))
                * technology_multiplier("r2", coerce_int(context["r2_energy_tech"], 0))
            )

    def _init_warehouse_and_ship_tables(
        self,
        planet_plans: Sequence[Mapping[str, object]],
        planet_contexts: Sequence[Mapping[str, object]],
        top_level: int,
        month_days: float,
        warehouse_hours: float,
    ) -> None:
        self.warehouse_hours: float = float(warehouse_hours)
        self.top_warehouse_level: int = min(catalog_top_level(key) for key in WAREHOUSE_KEYS)
        self.warehouse_capacities: FloatArray = np.array(
            [warehouse_capacity(level) for level in range(self.top_warehouse_level + 1)],
            dtype=np.float64,
        )
        self.min_warehouse_levels: IntArray = np.array(
            [[as_int_dict(plan["warehouse_min_levels"])[key] for key in WAREHOUSE_KEYS] for plan in planet_plans],
            dtype=np.int64,
        )
        self.capacity_needed: FloatArray = np.array(
            [[as_float_dict(plan["capacity_needed"])[resource] for resource in RESOURCE_KEYS] for plan in planet_plans],
            dtype=np.float64,
        )
        self.cost_tables: FloatArray = upgrade_cost_tables(planet_plans, self.optimized_keys, top_level)
        self.warehouse_cost_tables: FloatArray = upgrade_cost_tables(
            planet_plans, WAREHOUSE_KEYS, self.top_warehouse_level
        )
        self.central_planet: int = next(
            (planet for planet, plan in enumerate(planet_plans) if bool(plan["is_central_planet"])), -1
        )
        self.central_reserve: FloatArray = np.zeros(len(RESOURCE_KEYS), dtype=np.float64)

        ship = ship_info(self.target_ship_key)
        cost = full_cost(ship.get("base_cost"))
        self.cost_resources: tuple[str, ...] = tuple(r for r in RESOURCE_KEYS if float(cost[r]) > 0.0)
        self.ship_cost: FloatArray = np.array([float(cost[r]) for r in self.cost_resources], dtype=np.float64)
        self.ship_capacity: FloatArray = np.array([float(cost[r]) for r in RESOURCE_KEYS], dtype=np.float64)
        self.limit_names: tuple[str, ...] = (*self.cost_resources, "shipyards")
        self.target_requirements: dict[str, int] = same_planet_building_requirements(self.target_ship_key)
        self.hours_per_month: float = 24.0 * float(month_days)
        b9_arr = self.fixed_levels.get("b9", np.zeros(1, dtype=np.int64))
        b17_arr = self.fixed_levels.get("b17", np.zeros(1, dtype=np.int64))
        factory_levels = max(top_level, int(b9_arr.max())) + 1
        microsystem_levels = max(top_level, int(b17_arr.max())) + 1
        speedup = coerce_finite_float(planet_contexts[0]["speedup_factor"], 1.0)
        seconds_per_month = 3600.0 * self.hours_per_month
        base_build_s = coerce_finite_float(ship.get("base_build_time_s"), 1.0)
        self.shipyard_ships: FloatArray = np.array(
            [
                [
                    seconds_per_month * shipyard_time_divisor(factory, microsystem, speedup) / base_build_s
                    for microsystem in range(microsystem_levels)
                ]
                for factory in range(factory_levels)
            ],
            dtype=np.float64,
        )

    def _compute_twin_planets(self, planet_count: int) -> list[int]:
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
            for planet in range(planet_count)
        ]
        return [signatures.index(signature) for signature in signatures]

    def check_minimum_designs(self) -> None:
        """Raise ``ValueError`` if minimum/fixed levels exceed planetary field or warehouse caps."""
        outcome = self.simulate(self.min_levels[None])
        overfull = np.flatnonzero(~outcome["planet_fits"][0])
        if len(overfull) > 0:
            planet = int(overfull[0])
            if bool((outcome["planet_warehouse_levels"][0, planet] > self.top_warehouse_level).any()):
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

    def with_central_reserve(self, capacity: FloatArray) -> EmpireProductionModel:
        """Return a shallow copy configured with ``capacity`` central reserve buffer."""
        model = copy.copy(self)
        model.central_reserve = np.asarray(capacity, dtype=np.float64)
        model.check_minimum_designs()
        return model

    def planet_index(self, planets: Iterable[int] | None) -> IntArray:
        """Return an index vector of selected planet positions."""
        if planets is None:
            return np.arange(len(self.free_fields), dtype=np.int64)
        return np.asarray(list(planets), dtype=np.int64)

    def building_levels(self, key: str, levels: IntArray, planets: IntArray) -> IntArray:
        """Return the ``(batch, planets)`` level matrix for building ``key``."""
        if key in self.building_index:
            return levels[:, :, self.building_index[key]]
        return np.asarray(np.broadcast_to(self.fixed_levels[key][planets], levels.shape[:2]), dtype=np.int64)

    def warehouse_levels_for(self, capacity: FloatArray) -> IntArray:
        """Vectorized warehouse level lookup matching ``warehouse_level_for``."""
        return np.asarray(np.searchsorted(self.warehouse_capacities, capacity), dtype=np.int64)

    def simulate(
        self, levels: IntArray, planets: Iterable[int] | None = None
    ) -> dict[str, FloatArray | IntArray | BoolArray]:
        """Simulate a batch of ``(batch, planets, buildings)`` target level matrices."""
        planets_idx = self.planet_index(planets)
        planet_axis = planets_idx[None, :, None]
        building_axis = np.arange(levels.shape[2], dtype=np.int64)[None, None, :]
        totals: FloatArray = self.fixed_flows[:, None, planets_idx] + self.flow_tables[
            :, planet_axis, building_axis, levels
        ].sum(axis=3)
        flows: dict[str, FloatArray] = dict(zip(PRODUCTION_FLOW_KEYS, totals, strict=True))

        power_ratio: FloatArray = np.ones_like(flows["energy_used"])
        np.divide(flows["energy_produced"], flows["energy_used"], out=power_ratio, where=flows["energy_used"] > 0.0)
        power_ratio = np.minimum(1.0, power_ratio)

        pig_iron_mined: FloatArray = flows["pig_iron"] * power_ratio
        pig_iron_wanted: FloatArray = flows["pig_iron_used"] * power_ratio
        smelting_ratio: FloatArray = np.ones_like(pig_iron_wanted)
        np.divide(pig_iron_mined, pig_iron_wanted, out=smelting_ratio, where=pig_iron_wanted > 0.0)
        smelting_ratio = np.minimum(1.0, smelting_ratio)

        net: dict[str, FloatArray] = {
            "pig_iron": pig_iron_mined - pig_iron_wanted * smelting_ratio,
            "metal": flows["metal"] * power_ratio * smelting_ratio,
            "kryptonite": flows["kryptonite"] * power_ratio,
            "spice": flows["spice"] * power_ratio - flows["spice_used"],
        }
        net_per_hour: FloatArray = np.stack([net[resource] for resource in RESOURCE_KEYS], axis=2)

        can_build: BoolArray = np.ones(levels.shape[:2], dtype=bool)
        for key, required_level in self.target_requirements.items():
            can_build &= self.building_levels(key, levels, planets_idx) >= required_level
        shipyard_ships: FloatArray = np.where(
            can_build,
            self.shipyard_ships[
                self.building_levels("b9", levels, planets_idx),
                self.building_levels("b17", levels, planets_idx),
            ],
            0.0,
        )
        planet_limits: FloatArray = np.stack(
            [
                *(
                    net[resource] * self.hours_per_month / cost
                    for resource, cost in zip(self.cost_resources, self.ship_cost, strict=True)
                ),
                shipyard_ships,
            ],
            axis=2,
        )

        capacity: FloatArray = np.maximum(
            self.warehouse_hours * np.maximum(net_per_hour, 0.0), self.capacity_needed[planets_idx]
        )
        capacity = np.maximum(capacity, self.cost_tables[planet_axis, building_axis, levels].max(axis=2))
        capacity = np.maximum(capacity, np.where(can_build[:, :, None], self.ship_capacity, 0.0))
        capacity = np.maximum(
            capacity, np.where((planets_idx == self.central_planet)[:, None], self.central_reserve, 0.0)
        )
        warehouses: IntArray = np.maximum(self.warehouse_levels_for(capacity), self.min_warehouse_levels[planets_idx])
        warehouse_axis = np.arange(len(WAREHOUSE_KEYS), dtype=np.int64)[None, None, :]
        while True:
            own_cost: FloatArray = self.warehouse_cost_tables[
                planet_axis, warehouse_axis, np.minimum(warehouses, self.top_warehouse_level)
            ].max(axis=2)
            raised: IntArray = np.maximum(warehouses, self.warehouse_levels_for(own_cost))
            if bool((raised == warehouses).all()):
                break
            warehouses = raised

        fields_needed: IntArray = np.asarray(levels.sum(axis=2) + warehouses.sum(axis=2), dtype=np.int64)
        planet_fits: BoolArray = np.asarray(
            (fields_needed <= self.free_fields[planets_idx]) & (warehouses <= self.top_warehouse_level).all(axis=2),
            dtype=bool,
        )
        return {
            "planet_limits": planet_limits,
            "planet_net_per_hour": net_per_hour,
            "planet_energy_produced": flows["energy_produced"],
            "planet_energy_used": flows["energy_used"],
            "planet_power_ratio": power_ratio,
            "planet_smelting_ratio": smelting_ratio,
            "planet_warehouse_levels": warehouses,
            "planet_fields_needed": fields_needed,
            "planet_fits": planet_fits,
        }

    def ships_per_month(self, levels: IntArray) -> FloatArray:
        """Return bottleneck ships-per-month scalar for each batch element."""
        limits = np.asarray(self.simulate(levels)["planet_limits"], dtype=np.float64)
        return np.asarray(limits.sum(axis=1).min(axis=1), dtype=np.float64)

    def in_range(self, levels: IntArray, planets: Iterable[int] | None = None) -> BoolArray:
        """Return boolean mask of batch elements whose levels stay within ``[min_levels, max_levels]``."""
        planets_idx = self.planet_index(planets)
        return np.asarray(
            ((levels >= self.min_levels[planets_idx]) & (levels <= self.max_levels[planets_idx])).all(axis=(1, 2)),
            dtype=bool,
        )

    def is_feasible(self, levels: IntArray, planets: Iterable[int] | None = None) -> BoolArray:
        """Return boolean mask of batch elements that are both in range and fit planet fields/warehouses."""
        planets_idx = self.planet_index(planets)
        inside: IntArray = np.asarray(
            np.clip(levels, self.min_levels[planets_idx], self.max_levels[planets_idx]), dtype=np.int64
        )
        fits = np.asarray(self.simulate(inside, planets_idx)["planet_fits"], dtype=bool)
        return np.asarray(self.in_range(levels, planets_idx) & fits.all(axis=1), dtype=bool)


__all__ = [
    "EmpireProductionModel",
    "build_planet_plan",
    "build_planet_plans",
    "plan_research_levels",
    "upgrade_cost_table",
    "upgrade_cost_tables",
]
