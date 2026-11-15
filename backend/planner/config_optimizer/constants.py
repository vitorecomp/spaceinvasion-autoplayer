"""Constants, defaults, aliases, and NumPy array type aliases for the Config Optimizer."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
BoolArray = NDArray[np.bool_]

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

DEFAULT_CONFIG_OPTIMIZER_PARAMS: dict[str, object] = {
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
