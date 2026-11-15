"""Parameter validation and payload normalization helpers for planner `@command` handlers."""

from __future__ import annotations

from collections.abc import Mapping
import math

from backend.runtime.api.validation import ApiRequestError
from backend.runtime.bridge._helpers import bounded_api_str, parse_command_int
from backend.runtime.bridge._helpers_validation import validate_config_patch

_AI_DELAY_KEYS: tuple[str, ...] = (
    "transport_dispatch_delay_minutes",
    "PATH_TRANSPORT_DISPATCH_DELAY_MINUTES",
    "fleet_dispatch_delay_minutes",
)


def validate_co_update_planet_payload(
    *,
    target_buildings: object,
    slot: int | None,
    planet_id: int | None,
    coords: object,
    fields_max_override: int | None,
    locked_specialization: object,
    planet_name: object,
    lock_for_ga: object,
    auto_warehouses: object,
    auto_replan_ai: object,
) -> dict[str, object]:
    """Validate `config_optimizer_update_planet` inputs and build its normalized bridge payload."""
    if not isinstance(target_buildings, Mapping):
        raise ApiRequestError("'config_optimizer_update_planet' requires a dictionary 'target_buildings'.")
    if locked_specialization is not None and not isinstance(locked_specialization, str):
        raise ApiRequestError("'locked_specialization' must be a string.")
    if planet_name is not None and not isinstance(planet_name, str):
        raise ApiRequestError("'planet_name' must be a string.")
    if not isinstance(lock_for_ga, bool):
        raise ApiRequestError("'lock_for_ga' must be a boolean.")
    if not isinstance(auto_warehouses, bool):
        raise ApiRequestError("'auto_warehouses' must be a boolean.")
    if not isinstance(auto_replan_ai, bool):
        raise ApiRequestError("'auto_replan_ai' must be a boolean.")
    coords_val = str(coords or "").strip() if coords is not None else None
    return {
        "target_buildings": dict(target_buildings),
        "slot": slot,
        "planet_id": planet_id,
        "coords": coords_val,
        "fields_max_override": fields_max_override,
        "locked_specialization": locked_specialization,
        "planet_name": planet_name,
        "lock_for_ga": lock_for_ga,
        "auto_warehouses": auto_warehouses,
        "auto_replan_ai": auto_replan_ai,
    }


def validate_co_run_payload(
    *,
    raw_params: object,
    auto_replan_ai: object,
    sync: object,
    trigger_source: object,
) -> dict[str, object]:
    """Validate `config_optimizer_run` inputs and build its normalized bridge payload."""
    if raw_params is not None and not isinstance(raw_params, Mapping):
        raise ApiRequestError("'config_optimizer_run' field 'params' must be a dictionary.")
    if not isinstance(auto_replan_ai, bool):
        raise ApiRequestError("'config_optimizer_run' field 'auto_replan_ai' must be a boolean.")
    if not isinstance(sync, bool):
        raise ApiRequestError("'config_optimizer_run' field 'sync' must be a boolean.")
    trig = bounded_api_str(trigger_source or "manual", default="manual")[:64]
    return {
        "params": dict(raw_params) if isinstance(raw_params, Mapping) else None,
        "auto_replan_ai": auto_replan_ai,
        "sync": sync,
        "trigger_source": trig,
    }


def validate_ai_get_payload(
    *,
    window_size: int,
    week: int | None,
    planet_id: object,
    page: int,
    page_size: int,
    search: object,
    limit: int,
) -> dict[str, object]:
    """Validate `ai_planner_get` inputs and build its normalized bridge payload."""
    planet_filter: int | str | None = None
    if planet_id not in (None, "", "all"):
        if isinstance(planet_id, int) and not isinstance(planet_id, bool):
            planet_filter = int(planet_id)
        elif isinstance(planet_id, str) and planet_id.strip():
            cleaned = planet_id.strip()
            planet_filter = int(cleaned) if cleaned.isascii() and cleaned.isdigit() else cleaned
        else:
            raise ApiRequestError("'ai_planner_get' field 'planet_id' must be an integer or string.")
    return {
        "window_size": window_size,
        "week": week,
        "planet_id": planet_filter,
        "page": page,
        "page_size": page_size,
        "search": search if isinstance(search, str) else "",
        "limit": limit,
    }


def validate_ai_run_payload(
    *,
    raw_params: object,
    sync: object,
    refresh: object,
    auto_run_config_if_missing: object,
    trigger_source: object,
) -> dict[str, object]:
    """Validate `ai_planner_run` inputs (including dispatch delay bounds) and build its payload."""
    if raw_params is not None and not isinstance(raw_params, Mapping):
        raise ApiRequestError("'ai_planner_run' field 'params' must be a dictionary.")
    if isinstance(raw_params, Mapping):
        for delay_key in _AI_DELAY_KEYS:
            if delay_key not in raw_params or raw_params.get(delay_key) is None:
                continue
            raw_delay = raw_params.get(delay_key)
            if isinstance(raw_delay, bool) or not isinstance(raw_delay, (int, float, str)):
                raise ApiRequestError(f"'ai_planner_run' param '{delay_key}' must be a number between 0 and 1440.")
            try:
                delay_val = float(raw_delay)
            except (TypeError, ValueError) as exc:
                raise ApiRequestError(
                    f"'ai_planner_run' param '{delay_key}' must be a number between 0 and 1440."
                ) from exc
            if not math.isfinite(delay_val) or delay_val < 0.0 or delay_val > 1440.0:
                raise ApiRequestError(f"'ai_planner_run' param '{delay_key}' must be between 0 and 1440 minutes.")
    if not isinstance(sync, bool):
        raise ApiRequestError("'ai_planner_run' field 'sync' must be a boolean.")
    if not isinstance(refresh, bool):
        raise ApiRequestError("'ai_planner_run' field 'refresh' must be a boolean.")
    if not isinstance(auto_run_config_if_missing, bool):
        raise ApiRequestError("'ai_planner_run' field 'auto_run_config_if_missing' must be a boolean.")
    trig = bounded_api_str(trigger_source or "manual", default="manual")[:64]
    return {
        "params": dict(raw_params) if isinstance(raw_params, Mapping) else None,
        "sync": sync,
        "refresh": refresh,
        "auto_run_config_if_missing": auto_run_config_if_missing,
        "trigger_source": trig,
    }


def validate_field_conflict_payload(
    *,
    conflict_key: object,
    decision: object,
    action: object,
    sync: object,
    candidate_building_key: object,
    building_key: object,
) -> dict[str, object]:
    """Validate `resolve_field_conflict` inputs and build its normalized bridge payload."""
    if not isinstance(conflict_key, str) or not conflict_key.strip():
        raise ApiRequestError("'resolve_field_conflict' requires a non-empty 'conflict_key'.")
    raw_dec = decision if decision is not None else action
    if not isinstance(raw_dec, str) or raw_dec.strip().lower() not in ("demolish", "keep_reserve"):
        raise ApiRequestError("'resolve_field_conflict' requires 'decision' of 'demolish' or 'keep_reserve'.")
    if not isinstance(sync, bool):
        raise ApiRequestError("'resolve_field_conflict' field 'sync' must be a boolean.")
    out: dict[str, object] = {
        "conflict_key": conflict_key.strip(),
        "decision": raw_dec.strip().lower(),
        "sync": sync,
    }
    override_bkey = candidate_building_key or building_key
    if override_bkey is not None:
        out["candidate_building_key"] = override_bkey
    return out


def validate_fleet_planner_payload(raw_config: object) -> dict[str, object]:
    """Validate optional `fleet_planner_get` config override mapping."""
    if raw_config is None:
        return {}
    if not isinstance(raw_config, Mapping):
        raise ApiRequestError("'fleet_planner_get' field 'config' must be a dictionary.")
    validate_config_patch(raw_config, strict_bools=False)
    return {"config": dict(raw_config)}


def parse_horizon_years(raw: object) -> int:
    """Strictly parse `config_optimizer_yearly_projection` horizon years (`1..15`, default `5`)."""
    val = parse_command_int("horizon_years", raw, min_val=1, max_val=15)
    return int(val if val is not None else 5)


def parse_sim_target_coords(raw: object) -> str:
    """Validate optional `simulate_combat` target coordinates, defaulting to `01:001:01`."""
    if raw is None:
        return "01:001:01"
    if not isinstance(raw, str):
        raise ApiRequestError("'simulate_combat' field 'target_coords' must be a string.")
    return raw.strip() or "01:001:01"
