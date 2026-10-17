"""Planet slot resolution, reserved floors, display names, and settings validation."""

from __future__ import annotations

from collections.abc import Mapping
import math

from spaceinvasion.combat.fleet import SHIP_SLUG_BY_KEY
from spaceinvasion.core import game_data
from spaceinvasion.core.coercion import coerce_int
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import EnergyBalance, PlanetState, Resources
from spaceinvasion.planner.config_opt._params_norm import (
    _apply_manual_planet_pins,
    _json_ready,
    _normalize_params,
    _resolve_manual_planet_config,
    _safe_speedup,
    normalize_manual_planet_configs_map,
    normalize_planet_name,
    normalize_planet_names_map,
    normalize_planet_specialization,
    normalize_planet_specializations_map,
    normalize_planet_target_buildings,
)
from spaceinvasion.planner.config_opt._protocols import PlanetLike
from spaceinvasion.planner.config_opt.catalog_math import catalog_top_level, warehouse_capacity
from spaceinvasion.planner.config_opt.constants import WAREHOUSE_KEYS
from spaceinvasion.planner.config_opt.tech_graph import same_planet_building_requirements


def _empty_colony_slot(slot_number: int, fields: int, temperature_c: int = 0) -> PlanetState:
    """Build a placeholder ``PlanetState`` for an uncolonized generic slot."""
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
        buildings={f"b{int(building_id)}": 0 for building_id in sorted(game_data.BUILDING_BY_ID)},
        ships={},
        defense={},
        economy_rates={},
    )


def _resolve_planet_reserved_floors(
    planet: PlanetLike,
    reserved_floors_by_planet: Mapping[str, Mapping[str, int]],
) -> dict[str, int]:
    """Merge reserved floors matching ``str(planet.planet_id)``, ``planet.coords``, or normalized coords."""
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
                    val = max(0, coerce_int(lvl, 0))
                    if val > merged.get(bk, 0):
                        merged[bk] = val
    return merged


def _resolve_planet_specialization(
    planet: PlanetLike,
    slot_index: int,
    planet_specializations: Mapping[str, str] | None,
) -> str:
    """Resolve an explicit specialization lock matching ``planet_id``, ``coords``, or ``slot:<n>``."""
    if not planet_specializations:
        return "auto"
    pid = int(planet.planet_id)
    pid_str = str(pid) if pid > 0 else ""
    raw_coords = str(planet.coords or "").strip()
    norm_coords = normalize_coords(raw_coords) if raw_coords and not raw_coords.startswith("EMPTY:") else raw_coords
    slot_key = f"slot:{int(slot_index)}"

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
    planet: PlanetLike,
    slot_index: int,
    planet_names: Mapping[str, str] | None,
) -> tuple[str, str]:
    """Resolve ``(display_name, game_name)`` from ``planet_names`` override + ``PlanetLike``."""
    raw_coords = str(planet.coords or "").strip()
    state_name = normalize_planet_name(planet.name)
    state_game_name = state_name or raw_coords

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
    """Raise ``ValueError`` when optimizer parameters contradict ``game_data`` or each other."""
    building_keys = {f"b{int(building_id)}" for building_id in game_data.BUILDING_BY_ID}
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


__all__ = [
    "_apply_manual_planet_pins",
    "_empty_colony_slot",
    "_json_ready",
    "_normalize_params",
    "_resolve_manual_planet_config",
    "_resolve_planet_display_name",
    "_resolve_planet_reserved_floors",
    "_resolve_planet_specialization",
    "_safe_speedup",
    "check_optimizer_settings",
    "normalize_manual_planet_configs_map",
    "normalize_planet_name",
    "normalize_planet_names_map",
    "normalize_planet_specialization",
    "normalize_planet_specializations_map",
    "normalize_planet_target_buildings",
]
