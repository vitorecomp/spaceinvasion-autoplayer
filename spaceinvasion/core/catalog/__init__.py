"""Offline SpaceInvasion Classic catalog package separating generated data from formulas."""

from __future__ import annotations

from spaceinvasion.core.catalog.coercion import (
    MAX_JSON_SAFE_INT,
    cap_json_int,
    fmt_compact_int,
    safe_big_ratio,
    safe_finite_float,
    safe_int,
    safe_optional_int,
    safe_scale_round_int,
)
from spaceinvasion.core.catalog.data_buildings import (
    BUILDINGS,
    MERCHANT_RATES_IN_SPICE,
    MINING_TECH_BONUS_PER_LEVEL,
    WIKI_SOURCE_URL,
    WIKI_SYNCED_AT_UTC,
)
from spaceinvasion.core.catalog.data_defenses import DEFENSES
from spaceinvasion.core.catalog.data_research import RESEARCHES
from spaceinvasion.core.catalog.data_ships import SHIPS
from spaceinvasion.core.catalog.formulas import (
    blast_furnace_pig_iron_used,
    building_cost,
    building_energy_delta,
    building_level_stats,
    interpolate_temp_value,
    mine_energy_needed,
    mine_production_per_hour,
    spice_equivalent,
)
from spaceinvasion.core.catalog.json_sanitize import safe_key, sanitize_json_tree
from spaceinvasion.core.catalog.lookup import (
    BUILDING_BY_ID,
    BUILDING_LEVELS,
    DEFENSE_SLUG_BY_KEY,
    RESEARCH_BY_ID,
    RESEARCH_LEVELS,
    SHIP_SLUG_BY_KEY,
    missing_prerequisites,
    normalize_unit_key,
    resolve_ship_slug,
    resolve_unit,
)

__all__ = [
    "BUILDINGS",
    "BUILDING_BY_ID",
    "BUILDING_LEVELS",
    "DEFENSES",
    "DEFENSE_SLUG_BY_KEY",
    "MAX_JSON_SAFE_INT",
    "MERCHANT_RATES_IN_SPICE",
    "MINING_TECH_BONUS_PER_LEVEL",
    "RESEARCHES",
    "RESEARCH_BY_ID",
    "RESEARCH_LEVELS",
    "SHIPS",
    "SHIP_SLUG_BY_KEY",
    "WIKI_SOURCE_URL",
    "WIKI_SYNCED_AT_UTC",
    "blast_furnace_pig_iron_used",
    "building_cost",
    "building_energy_delta",
    "building_level_stats",
    "cap_json_int",
    "fmt_compact_int",
    "interpolate_temp_value",
    "mine_energy_needed",
    "mine_production_per_hour",
    "missing_prerequisites",
    "normalize_unit_key",
    "resolve_ship_slug",
    "resolve_unit",
    "safe_big_ratio",
    "safe_finite_float",
    "safe_int",
    "safe_key",
    "safe_optional_int",
    "safe_scale_round_int",
    "sanitize_json_tree",
    "spice_equivalent",
]
