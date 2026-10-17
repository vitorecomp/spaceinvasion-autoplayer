"""Strict config-patch and combat-simulator parameter validation helpers."""

from __future__ import annotations

from collections.abc import Mapping
import math

from spaceinvasion.core.config import normalize_planner_mode
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._helpers import parse_command_int, redact_api_payload, validate_strict_int

_CONFIG_INT_SPECS: dict[str, tuple[int | None, int | None, bool]] = {
    "ga_population_size": (4, 200, False),
    "ga_generations": (1, 200, False),
    "action_batch_interval_seconds": (5, 86400, False),
    "max_actions_per_hour": (1, 100_000, False),
    "page_view_limit_per_minute": (6, 600, False),
    "min_free_fields_warning": (1, 1000, False),
    "min_colony_fields": (50, 600, False),
    "max_planets": (1, 50, False),
    "galaxy_sweep_interval_seconds": (30, 86400, False),
    "galaxy_sweep_pages": (1, 400, False),
    "galaxy_scan_refresh_pct": (0, 100, False),
    "reserved_spy_slots": (0, 10, False),
    "hot_planet_solar_temp_threshold": (-273, 1000, False),
    "fleet_save_fuel_reserve_spice": (0, 1_000_000_000_000, False),
}

_CONFIG_FLOAT_SPECS: dict[str, tuple[float | None, float | None]] = {
    "ai_resource_spare_pct": (0.0, 100.0),
    "ga_mutation_rate": (0.0, 1.0),
    "storage_warn_ratio": (0.1, 0.99),
    "blast_pig_iron_max_share": (0.1, 1.0),
    "combat_safety_margin": (1.0, 1000.0),
    "raid_min_loot_spice_eq": (0.0, 1e12),
    "attack_min_res_per_hour": (0.0, 1e12),
    "recycle_min_res_per_hour": (0.0, 1e12),
    "galaxy_scan_click_delay_seconds": (0.2, 60.0),
    "galaxy_scan_click_jitter_seconds": (0.0, 30.0),
    "galaxy_scan_near_weight": (0.0, 20.0),
    "galaxy_scan_attack_weight": (0.0, 20.0),
}

_CONFIG_BOOL_KEYS: tuple[str, ...] = (
    "merchant_mode",
    "auto_throttle_blast_furnace",
    "allow_research",
    "allow_fleet_research",
    "allow_shipyard",
    "allow_multi_planet",
    "allow_fleetsave",
    "allow_galaxy_scan",
    "galaxy_scan_block_other_actions",
    "allow_raids",
    "allow_pvp_in_az",
    "allow_colonize",
    "allow_transport",
    "allow_transmitter",
    "forbid_planetary_solar",
    "galaxy_scan_explore_first",
    "galaxy_scan_all_galaxies",
    "spy_reserved_slots_only",
    "ai_adaptive_execution",
)


def validate_strict_float(
    name: str,
    val: object,
    *,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    """Strictly parse a config finite float field with legacy error wording."""
    if isinstance(val, bool) or val is None or isinstance(val, (Mapping, list, tuple, set, frozenset)):
        raise ApiRequestError(f"Config field {name!r} must be a finite number.", field=name)
    raw: int | float | str
    if isinstance(val, str):
        txt = val.strip()
        if not txt or not txt.isascii() or "_" in txt:
            raise ApiRequestError(f"Config field {name!r} must be a finite number.", field=name)
        raw = txt
    elif isinstance(val, (int, float)):
        raw = val
    else:
        raise ApiRequestError(f"Config field {name!r} must be a finite number.", field=name)
    try:
        parsed = float(raw)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ApiRequestError(f"Config field {name!r} must be a finite number.", field=name) from exc
    if not math.isfinite(parsed):
        raise ApiRequestError(f"Config field {name!r} must be a finite number.", field=name)
    if min_val is not None and parsed < min_val:
        raise ApiRequestError(f"Config field {name!r} must be >= {min_val}.", field=name)
    if max_val is not None and parsed > max_val:
        raise ApiRequestError(f"Config field {name!r} must be <= {max_val}.", field=name)
    return parsed


def _validate_target_map(raw_cfg: Mapping[str, object], key: str, *, max_val: int, type_err: str) -> None:
    if key not in raw_cfg:
        return
    raw_targets = raw_cfg[key]
    if not isinstance(raw_targets, Mapping):
        raise ApiRequestError(type_err, field=key)
    for item_k, item_v in raw_targets.items():
        if not isinstance(item_k, str) or not item_k.strip():
            raise ApiRequestError(f"Config field {key!r} keys must be non-empty strings.", field=key)
        validate_strict_int(f"{key}.{item_k.strip()}", item_v, min_val=0, max_val=max_val, allow_none=False)


def validate_config_patch(raw_cfg: Mapping[str, object], *, strict_bools: bool = True) -> None:
    """Validate an incoming `update_config` patch mapping against strict domain bounds."""
    if "planner_mode" in raw_cfg:
        raw_mode = raw_cfg["planner_mode"]
        if not isinstance(raw_mode, str) or not raw_mode.strip():
            raise ApiRequestError("'planner_mode' must be a non-empty string ('priority' or 'ai_planner').")
        try:
            normalize_planner_mode(raw_mode, strict=True)
        except ValueError as exc:
            raise ApiRequestError(str(redact_api_payload(exc))) from exc
    if "playstyle" in raw_cfg:
        raw_playstyle = raw_cfg["playstyle"]
        if not isinstance(raw_playstyle, str) or not raw_playstyle.strip():
            raise ApiRequestError("'playstyle' must be a non-empty string.")
    for key, (lo, hi, allow_none) in _CONFIG_INT_SPECS.items():
        if key in raw_cfg:
            validate_strict_int(key, raw_cfg[key], min_val=lo, max_val=hi, allow_none=allow_none)
    if "spy_dispatch_planet_id" in raw_cfg and raw_cfg["spy_dispatch_planet_id"] not in (None, ""):
        validate_strict_int(
            "spy_dispatch_planet_id",
            raw_cfg["spy_dispatch_planet_id"],
            min_val=0,
            max_val=2**63 - 1,
            allow_none=False,
        )
    for key, (f_lo, f_hi) in _CONFIG_FLOAT_SPECS.items():
        if key in raw_cfg:
            validate_strict_float(key, raw_cfg[key], min_val=f_lo, max_val=f_hi)
    if strict_bools:
        for key in _CONFIG_BOOL_KEYS:
            if key in raw_cfg and not isinstance(raw_cfg[key], bool):
                raise ApiRequestError(f"Config field {key!r} must be a boolean.", field=key)
    _validate_target_map(
        raw_cfg,
        "shipyard_targets",
        max_val=1_000_000_000,
        type_err="Config field 'shipyard_targets' must be a mapping of ship keys to integer counts.",
    )
    _validate_target_map(
        raw_cfg,
        "fleet_research_targets",
        max_val=100,
        type_err="Config field 'fleet_research_targets' must be a mapping of research keys to integer target levels.",
    )


def parse_sim_count_map(field_name: str, raw: object, *, max_val: int = 1_000_000_000) -> dict[str, int]:
    """Validate and coerce a combat simulator unit count mapping."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ApiRequestError(
            f"'simulate_combat' field {field_name!r} must be a mapping of unit keys to integer counts."
        )
    out: dict[str, int] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ApiRequestError(f"'simulate_combat' field {field_name!r} keys must be non-empty strings.")
        parsed = parse_command_int(f"{field_name}[{key!r}]", val, min_val=0, max_val=max_val)
        if parsed is None:
            raise ApiRequestError(f"Command parameter '{field_name}[{key!r}]' must be an integer.")
        out[key.strip()] = parsed
    return out
