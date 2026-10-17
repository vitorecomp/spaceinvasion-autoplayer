"""Parameter normalization, planet naming/specialization normalization, and manual pinning."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import copy
import math

import numpy as np

from spaceinvasion.core import game_data
from spaceinvasion.core.coercion import coerce_finite_float, coerce_int, coerce_optional_int
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import is_building_allowed_on_planet_type
from spaceinvasion.planner.config_opt._protocols import GameStateLike, as_int_dict, map_int, map_str
from spaceinvasion.planner.config_opt.catalog_math import catalog_top_level
from spaceinvasion.planner.config_opt.constants import (
    _SPECIALIZATION_ALIASES,
    _UPPER_PARAM_ALIASES,
    DEFAULT_CONFIG_OPTIMIZER_PARAMS,
    MAX_PLANET_NAME_LENGTH,
    SPECIALIZATION_ALLOWED_BUILDINGS,
    VALID_PLANET_SPECIALIZATIONS,
    WAREHOUSE_KEYS,
)


def _json_ready(value: object) -> object:
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


def _safe_speedup(state: GameStateLike, params: Mapping[str, object]) -> float:
    """Return a finite positive universe speedup factor from ``state`` or ``params``."""
    for candidate in (state.speedup_factor, params.get("speedup_factor")):
        if candidate is None or isinstance(candidate, bool):
            continue
        val = coerce_finite_float(candidate, 0.0)
        if math.isfinite(val) and val > 0.0:
            return val
    return 1.0


def normalize_planet_specialization(raw: object) -> str:
    """Normalize a single planet specialization value into ``VALID_PLANET_SPECIALIZATIONS``."""
    if raw is None:
        return "auto"
    norm = str(raw).strip().lower().replace(" ", "_")
    if not norm:
        return "auto"
    mapped = _SPECIALIZATION_ALIASES.get(norm, norm)
    return mapped if mapped in VALID_PLANET_SPECIALIZATIONS else "auto"


def normalize_planet_specializations_map(raw: object) -> dict[str, str]:
    """Normalize ``{planet_key: specialization}`` keeping only explicit non-``auto`` locks."""
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


def normalize_planet_name(raw: object) -> str:
    """Sanitize a custom planet display name (control-char free, single-line, max 48 chars)."""
    if raw is None or isinstance(raw, bool):
        return ""
    text = "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in str(raw))
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return ""
    return cleaned[:MAX_PLANET_NAME_LENGTH].strip()


def normalize_planet_names_map(raw: object) -> dict[str, str]:
    """Normalize ``{planet_key: custom_name}`` dropping empty keys or blank names."""
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


def normalize_planet_target_buildings(raw: object) -> dict[str, int]:
    """Normalize ``{b1..b21: level}`` clamping normal-planet buildings to ``0..catalog_top_level``."""
    out: dict[str, int] = {}
    src = raw if isinstance(raw, Mapping) else {}
    for bid_raw in sorted(game_data.BUILDING_BY_ID):
        bid = int(bid_raw)
        bk = f"b{bid}"
        if not is_building_allowed_on_planet_type("normal", bid):
            out[bk] = 0
            continue
        lvl = max(0, coerce_int(src.get(bk, 0), 0))
        out[bk] = min(lvl, catalog_top_level(bk))
    return out


def normalize_manual_planet_configs_map(raw: object) -> dict[str, dict[str, object]]:
    """Normalize ``{planet_key: {target_buildings, fields_max_override, auto_warehouses}}``."""
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, dict[str, object]] = {}
    for pk_raw, entry in raw.items():
        pk = str(pk_raw).strip()
        if not pk or not isinstance(entry, Mapping):
            continue
        raw_tb = entry.get("target_buildings")
        raw_blds: object = raw_tb if isinstance(raw_tb, Mapping) else entry
        blds = normalize_planet_target_buildings(raw_blds)
        raw_fm = coerce_optional_int(entry.get("fields_max_override"), None)
        fm_override = max(1, min(2000, int(raw_fm))) if (raw_fm is not None and raw_fm > 0) else None
        out[pk] = {
            "target_buildings": blds,
            "fields_max_override": fm_override,
            "auto_warehouses": bool(entry.get("auto_warehouses", False)),
        }
    return out


def _resolve_manual_planet_config(
    plan: Mapping[str, object],
    manual_configs: Mapping[str, object] | None,
) -> dict[str, object] | None:
    """Match a planet plan against ``manual_planet_configs`` by ``planet_id``, ``coords``, or ``slot:<n>``."""
    if not isinstance(manual_configs, Mapping) or not manual_configs:
        return None
    pid = map_int(plan, "planet_id", 0)
    pid_str = str(pid) if pid > 0 else ""
    raw_coords = map_str(plan, "coords", "").strip()
    norm_coords = normalize_coords(raw_coords) if raw_coords and not raw_coords.startswith("EMPTY:") else raw_coords
    slot_idx = map_int(plan, "slot_index", map_int(plan, "slot", 0))
    slot_key = f"slot:{slot_idx}"

    if pid_str and isinstance(manual_configs.get(pid_str), Mapping):
        raw_match = manual_configs[pid_str]
        return {str(k): v for k, v in raw_match.items()} if isinstance(raw_match, Mapping) else None
    if raw_coords and isinstance(manual_configs.get(raw_coords), Mapping):
        raw_match = manual_configs[raw_coords]
        return {str(k): v for k, v in raw_match.items()} if isinstance(raw_match, Mapping) else None
    for lk_raw, entry in manual_configs.items():
        if not isinstance(entry, Mapping):
            continue
        lk = str(lk_raw).strip()
        if not lk:
            continue
        if ":" in lk and not lk.startswith("EMPTY:") and not lk.lower().startswith("slot:"):
            if norm_coords and normalize_coords(lk) == norm_coords:
                return {str(k): v for k, v in entry.items()}
        elif lk.lower() == slot_key:
            return {str(k): v for k, v in entry.items()}
    return None


def _apply_manual_planet_pins(
    planet_plans: list[dict[str, object]],
    manual_configs: Mapping[str, object] | None,
    optimized_keys: Iterable[str],
) -> None:
    """Pin manually configured planets in-place before constructing ``EmpireProductionModel``."""
    if not isinstance(manual_configs, Mapping) or not manual_configs:
        return
    opt_set = set(optimized_keys)
    for plan in planet_plans:
        pin = _resolve_manual_planet_config(plan, manual_configs)
        if pin is None:
            continue
        blds = normalize_planet_target_buildings(pin.get("target_buildings"))
        fm_ov = coerce_optional_int(pin.get("fields_max_override"), None)
        if fm_ov is not None and fm_ov > 0:
            plan["fields_max"] = max(int(fm_ov), sum(blds.values()))
        fixed_levels = as_int_dict(plan.get("fixed_levels"))
        for fk in list(fixed_levels):
            if fk in blds:
                fixed_levels[fk] = int(blds[fk])
        plan["fixed_levels"] = fixed_levels
        fixed_fields = int(sum(fixed_levels.values()))
        plan["fixed_fields"] = fixed_fields
        plan["free_fields"] = map_int(plan, "fields_max", 0) - fixed_fields
        min_levels = as_int_dict(plan.get("min_levels"))
        max_levels = as_int_dict(plan.get("max_levels"))
        for ok in opt_set:
            if ok in blds:
                lvl = int(blds[ok])
                min_levels[ok] = lvl
                max_levels[ok] = lvl
        plan["min_levels"] = min_levels
        plan["max_levels"] = max_levels
        wh_mins = as_int_dict(plan.get("warehouse_min_levels"))
        wh_set_by_raw = plan.get("warehouse_min_set_by")
        wh_set_by = {str(k): str(v) for k, v in wh_set_by_raw.items()} if isinstance(wh_set_by_raw, Mapping) else {}
        for wk in WAREHOUSE_KEYS:
            if wk in blds and not bool(pin.get("auto_warehouses", False)):
                wh_mins[wk] = max(int(wh_mins.get(wk, 0)), int(blds[wk]))
                if int(blds[wk]) > 0:
                    wh_set_by[wk] = "manual planet config"
        plan["warehouse_min_levels"] = wh_mins
        plan["warehouse_min_set_by"] = wh_set_by
        plan["manual_pinned"] = True


def _apply_param_override(merged: dict[str, object], key: str, raw_val: object) -> None:
    if key in ("misc_building_levels", "optimized_building_max_levels") and isinstance(raw_val, Mapping):
        existing = as_int_dict(merged.get(key))
        incoming = {str(k): coerce_int(v, 0) for k, v in raw_val.items()}
        merged[key] = incoming if set(incoming) == set(existing) else {**existing, **incoming}
    elif key == "min_fleet_per_planet" and isinstance(raw_val, Mapping):
        merged[key] = {str(k): coerce_int(v, 0) for k, v in raw_val.items()}
    elif key == "misc_building_levels_by_slot" and isinstance(raw_val, Mapping):
        merged[key] = {
            str(slot): {str(bk): coerce_int(lvl, 0) for bk, lvl in bmap.items()}
            for slot, bmap in raw_val.items()
            if isinstance(bmap, Mapping)
        }
    elif key == "reserved_floors_by_planet" and isinstance(raw_val, Mapping):
        merged[key] = {
            str(pk): {str(bk): max(0, coerce_int(lvl, 0)) for bk, lvl in bmap.items()}
            for pk, bmap in raw_val.items()
            if isinstance(bmap, Mapping)
        }
    elif key == "planet_specializations" and isinstance(raw_val, Mapping):
        merged[key] = normalize_planet_specializations_map(raw_val)
    elif key == "planet_names" and isinstance(raw_val, Mapping):
        existing_raw = merged.get("planet_names")
        existing_names = {str(k): str(v) for k, v in existing_raw.items()} if isinstance(existing_raw, Mapping) else {}
        merged[key] = {**existing_names, **normalize_planet_names_map(raw_val)}
    elif key == "manual_planet_configs" and isinstance(raw_val, Mapping):
        merged[key] = normalize_manual_planet_configs_map(raw_val)
    elif key == "hill_climb_step_sizes" and isinstance(raw_val, (list, tuple)):
        steps = [max(1, coerce_int(x, 1)) for x in raw_val]
        merged[key] = steps if steps else [1, 2, 4, 8]
    else:
        merged[key] = raw_val


def _normalize_params(raw_params: Mapping[str, object] | None) -> dict[str, object]:
    """Merge user/DB parameter overrides onto ``DEFAULT_CONFIG_OPTIMIZER_PARAMS``."""
    merged: dict[str, object] = copy.deepcopy(DEFAULT_CONFIG_OPTIMIZER_PARAMS)
    if isinstance(raw_params, Mapping):
        for raw_key, raw_val in raw_params.items():
            key = _UPPER_PARAM_ALIASES.get(str(raw_key), str(raw_key))
            if raw_val is None and key in merged:
                continue
            _apply_param_override(merged, key, raw_val)

    pop_size = max(2, coerce_int(merged["ga_population_size"], 200))
    raw_thresh = coerce_finite_float(merged.get("specialization_switch_threshold_pct"), 0.5)
    merged["target_ship_key"] = str(merged["target_ship_key"])
    merged["production_month_days"] = coerce_finite_float(merged["production_month_days"], 30.0)
    merged["research_percent_of_universe_max"] = coerce_finite_float(merged["research_percent_of_universe_max"], 100.0)
    merged["warehouse_hours_of_production"] = coerce_finite_float(merged["warehouse_hours_of_production"], 24.0)
    merged["min_warehouse_level"] = coerce_int(merged.get("min_warehouse_level", 0), 0)
    merged["central_planet_slot"] = coerce_int(merged["central_planet_slot"], 1)
    merged["central_planet_min_warehouse_level"] = coerce_int(merged["central_planet_min_warehouse_level"], 15)
    merged["catalogue_grid_size"] = max(16, coerce_int(merged["catalogue_grid_size"], 25000))
    merged["catalogue_priority_steps"] = max(1, coerce_int(merged["catalogue_priority_steps"], 4))
    merged["ga_population_size"] = pop_size
    merged["ga_generations"] = max(0, coerce_int(merged["ga_generations"], 150))
    merged["ga_tournament_size"] = max(1, coerce_int(merged["ga_tournament_size"], 3))
    merged["ga_crossover_rate"] = coerce_finite_float(merged["ga_crossover_rate"], 0.9)
    merged["ga_mutation_rate"] = coerce_finite_float(merged["ga_mutation_rate"], 0.1)
    merged["ga_elite_count"] = max(1, min(coerce_int(merged["ga_elite_count"], 4), pop_size - 1))
    merged["ga_random_seed"] = coerce_int(merged["ga_random_seed"], 20261117)
    merged["pad_generic_colonies"] = bool(merged["pad_generic_colonies"])
    merged["allow_demolition"] = bool(merged.get("allow_demolition", False))
    merged["max_planets"] = max(1, coerce_int(merged["max_planets"], 11))
    merged["empty_colony_fields"] = max(1, coerce_int(merged["empty_colony_fields"], 300))
    merged["planet_specializations"] = normalize_planet_specializations_map(merged.get("planet_specializations"))
    merged["planet_names"] = normalize_planet_names_map(merged.get("planet_names"))
    merged["manual_planet_configs"] = normalize_manual_planet_configs_map(merged.get("manual_planet_configs"))
    merged["preserve_previous_specializations"] = bool(merged.get("preserve_previous_specializations", True))
    merged["specialization_switch_threshold_pct"] = (
        max(0.0, min(100.0, raw_thresh)) if math.isfinite(raw_thresh) else 0.5
    )
    return merged
