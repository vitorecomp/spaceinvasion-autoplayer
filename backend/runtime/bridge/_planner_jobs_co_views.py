"""Config Optimizer command handlers (`get`, `set_specializations`, `update_planet`, `yearly_projection`, `run`)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging

from backend.core.config import StrategyConfig
from backend.planner import config_optimizer as co_mod
from backend.runtime.api.validation import ApiRequestError
from backend.runtime.bridge._context import BridgeContext
from backend.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    bounded_api_str,
    parse_command_int,
    redact_api_payload,
    safe_api_float,
    safe_api_int,
    sanitize_json_dict,
)
from backend.runtime.bridge._planner_jobs_persist import persist_config_optimizer_result
from backend.runtime.bridge._planner_jobs_state import (
    get_active_config_optimizer,
    get_planet_specializations,
    get_planner_state,
    get_reserved_floors,
    list_config_optimizer_runs,
    update_planet_specializations,
)
from backend.runtime.bridge._planner_jobs_telemetry import (
    evaluate_ai_planner_gate,
    get_planner_jobs_snapshot,
    snapshot_universe_research_max_map,
)
from backend.runtime.bridge.planet_names import PlanetNameService
from backend.runtime.bridge.state_cache import StateCacheService

_LOGGER = logging.getLogger(__name__)
_REPLAN_ERRORS: tuple[type[Exception], ...] = (ApiRequestError, *BRIDGE_RUNTIME_ERRORS)


def handle_config_optimizer_get(
    ctx: BridgeContext,
    names: PlanetNameService,
    *,
    limit: int = 15,
) -> dict[str, object]:
    """Assemble the `config_optimizer_get` response payload."""
    runs_limit = parse_command_int("limit", limit, min_val=1, max_val=200) or 15
    default_params = dict(co_mod.DEFAULT_CONFIG_OPTIMIZER_PARAMS)
    with ctx.state_lock:
        snap_state = get_planner_state(ctx)
        gate = evaluate_ai_planner_gate(ctx, snap_state)
        custom_names = names.get_custom_planet_names_locked()
        active_co = names.overlay_custom_names_on_config_optimizer(
            get_active_config_optimizer(ctx), snap_state, custom_names
        )
        runs = list_config_optimizer_runs(ctx, limit=int(runs_limit))
        reserved_floors = get_reserved_floors(ctx)
        planet_specializations = get_planet_specializations(ctx)
        jobs_snap = get_planner_jobs_snapshot(ctx, snap_state)
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "config_optimizer_get",
            "active": active_co,
            "job": jobs_snap["config_optimizer_job"],
            "runs": runs,
            "reserved_floors": reserved_floors,
            "planet_specializations": planet_specializations,
            "custom_planet_names": custom_names,
            "specialization_options": list(co_mod.VALID_PLANET_SPECIALIZATIONS),
            "default_params": default_params,
            "colonies_owned": gate["colonies_owned"],
            "max_planets": gate["max_planets"],
            "live_ai_unlocked": gate["live_ai_unlocked"],
        }
    )


def handle_config_optimizer_set_specializations(
    ctx: BridgeContext,
    specializations: Mapping[str, object],
    *,
    replace: bool = False,
    on_publish_snapshot: Callable[[], None] | None = None,
) -> dict[str, object]:
    """Validate and persist Config Optimizer planet specialization locks."""
    if not isinstance(specializations, Mapping):
        raise ApiRequestError(
            "'config_optimizer_set_specializations' field 'planet_specializations' must be a dictionary."
        )
    eff_replace = replace
    eff_specs: Mapping[str, object] = specializations
    if "planet_specializations" in specializations:
        raw_inner = specializations.get("planet_specializations")
        if not isinstance(raw_inner, Mapping):
            raise ApiRequestError(
                "'config_optimizer_set_specializations' field 'planet_specializations' must be a dictionary."
            )
        raw_rep = specializations.get("replace", replace)
        if not isinstance(raw_rep, bool):
            raise ApiRequestError("'config_optimizer_set_specializations' field 'replace' must be a boolean.")
        eff_specs = raw_inner
        eff_replace = raw_rep
    if not isinstance(eff_replace, bool):
        raise ApiRequestError("'config_optimizer_set_specializations' field 'replace' must be a boolean.")

    with ctx.state_lock:
        updated_specs = update_planet_specializations(ctx, eff_specs, replace=eff_replace)
        ctx.invalidate_dashboard_cache()
    if on_publish_snapshot is not None:
        on_publish_snapshot()
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "config_optimizer_set_specializations",
            "planet_specializations": updated_specs,
            "locked_planets_count": len(updated_specs),
        }
    )


def _parse_update_planet_payload(
    payload: Mapping[str, object],
) -> tuple[
    Mapping[str, object], int | None, int | None, str | None, int | None, str | None, str | None, bool, bool, bool
]:
    raw_blds = payload.get("target_buildings")
    if not isinstance(raw_blds, Mapping):
        raise ApiRequestError("'config_optimizer_update_planet' requires a dictionary 'target_buildings'.")
    slot_val = parse_command_int("slot", payload.get("slot"), allow_none=True, min_val=1, max_val=100)
    pid_val = parse_command_int(
        "planet_id", payload.get("planet_id"), allow_none=True, min_val=-1000, max_val=2**63 - 1
    )
    coords_val = str(payload.get("coords") or "").strip() if payload.get("coords") is not None else None
    fm_val = parse_command_int(
        "fields_max_override", payload.get("fields_max_override"), allow_none=True, min_val=1, max_val=2000
    )
    spec_raw = payload.get("locked_specialization")
    if spec_raw is not None and not isinstance(spec_raw, str):
        raise ApiRequestError("'locked_specialization' must be a string.")
    pname_raw = payload.get("planet_name")
    if pname_raw is not None and not isinstance(pname_raw, str):
        raise ApiRequestError("'planet_name' must be a string.")
    lock_for_ga = payload.get("lock_for_ga", True)
    if not isinstance(lock_for_ga, bool):
        raise ApiRequestError("'lock_for_ga' must be a boolean.")
    auto_wh = payload.get("auto_warehouses", False)
    if not isinstance(auto_wh, bool):
        raise ApiRequestError("'auto_warehouses' must be a boolean.")
    auto_replan_ai = payload.get("auto_replan_ai", True)
    if not isinstance(auto_replan_ai, bool):
        raise ApiRequestError("'auto_replan_ai' must be a boolean.")
    return raw_blds, slot_val, pid_val, coords_val, fm_val, spec_raw, pname_raw, lock_for_ga, auto_wh, auto_replan_ai


def _build_override_params(
    active_co: Mapping[str, object] | None,
    persisted_specs: Mapping[str, str],
    custom_names: Mapping[str, str],
    reserved_floors: Mapping[str, Mapping[str, int]],
    cfg_snap: StrategyConfig,
) -> tuple[dict[str, object] | None, dict[str, object], dict[str, dict[str, int]]]:
    raw_base_res = active_co.get("result") if isinstance(active_co, Mapping) else None
    base_res = dict(raw_base_res) if isinstance(raw_base_res, Mapping) else None
    merged_params: dict[str, object] = dict(co_mod.DEFAULT_CONFIG_OPTIMIZER_PARAMS)
    if isinstance(active_co, Mapping) and isinstance(active_co.get("params"), Mapping):
        raw_ap = active_co["params"]
        if isinstance(raw_ap, Mapping):
            merged_params.update(dict(raw_ap))
    merged_params["planet_specializations"] = dict(persisted_specs)
    merged_params["planet_names"] = dict(custom_names)
    merged_params["custom_planet_names"] = dict(custom_names)
    merged_params["max_planets"] = max(1, safe_api_int(cfg_snap.max_planets, 11))
    merged_floors = {
        str(pk): {str(bk): max(0, safe_api_int(lv, 0)) for bk, lv in bm.items()}
        for pk, bm in reserved_floors.items()
        if isinstance(bm, Mapping)
    }
    merged_params["reserved_floors_by_planet"] = merged_floors
    return base_res, merged_params, merged_floors


def _trigger_post_override_ai_replan(
    ctx: BridgeContext,
    *,
    auto_replan_ai: bool,
    run_id: int,
    updated_res: Mapping[str, object],
    start_ai_fn: Callable[..., dict[str, object]],
) -> dict[str, object] | None:
    if not auto_replan_ai or ctx.planner_closed:
        return None
    try:
        ai_resp = start_ai_fn(
            params=None,
            sync=False,
            trigger_source="auto_replan:manual_planet_edit",
            auto_run_config_if_missing=False,
            override_config_run_id=run_id,
            override_config_result=updated_res,
        )
        raw_job = ai_resp.get("job") if isinstance(ai_resp, Mapping) else None
        return dict(raw_job) if isinstance(raw_job, Mapping) else None
    except _REPLAN_ERRORS:
        _LOGGER.warning("Auto AI-Planner replan after manual planet edit failed", exc_info=True)
        return None


def handle_config_optimizer_update_planet(
    ctx: BridgeContext,
    names: PlanetNameService,
    state_cache: StateCacheService,
    payload: Mapping[str, object],
    *,
    start_ai_fn: Callable[..., dict[str, object]],
    on_publish_snapshot: Callable[[], None] | None = None,
) -> dict[str, object]:
    """Apply a manual planet target-building override, persist a new Config Optimizer run, and optionally replan AI."""
    raw_blds, slot_val, pid_val, coords_val, fm_val, spec_raw, pname_raw, lock_for_ga, auto_wh, auto_replan_ai = (
        _parse_update_planet_payload(payload)
    )
    with ctx.state_lock:
        snap_state = get_planner_state(ctx)
        if snap_state is None:
            raise ApiRequestError("No game state snapshot available yet; open /dashboard first.")
        if pname_raw is not None and pid_val is not None and int(pid_val) > 0:
            if any(int(p.planet_id) == int(pid_val) for p in snap_state.planets):
                names.set_custom_planet_name_locked(int(pid_val), pname_raw)
        uni_max = snapshot_universe_research_max_map(state_cache, snap_state)
        base_res, merged_params, merged_floors = _build_override_params(
            get_active_config_optimizer(ctx),
            get_planet_specializations(ctx),
            names.get_custom_planet_names_locked(),
            get_reserved_floors(ctx),
            ctx.strategy_config,
        )

    try:
        updated_res = co_mod.apply_manual_planet_override(
            snap_state,
            target_buildings=raw_blds,
            base_result=base_res,
            slot=slot_val,
            planet_id=pid_val,
            coords=coords_val,
            fields_max_override=fm_val,
            locked_specialization=spec_raw,
            planet_name=pname_raw,
            lock_for_ga=lock_for_ga,
            auto_warehouses=auto_wh,
            universe_research_max=uni_max,
            params=merged_params,
        )
    except ValueError as exc:
        raise ApiRequestError(str(redact_api_payload(exc))) from exc

    raw_meta = updated_res.get("meta_parameters")
    out_params: dict[str, object] = dict(raw_meta) if isinstance(raw_meta, Mapping) else merged_params
    run_id = persist_config_optimizer_result(
        ctx,
        updated_res,
        params=out_params,
        reserved_floors=merged_floors,
        trigger_source="manual_planet_edit",
        elapsed_ms=safe_api_float(updated_res.get("elapsed_ms"), 0.0),
        on_publish_snapshot=None,
    )
    with ctx.state_lock:
        raw_out_specs = out_params.get("planet_specializations")
        updated_specs = (
            update_planet_specializations(ctx, raw_out_specs, replace=True)
            if (spec_raw is not None and isinstance(raw_out_specs, Mapping))
            else get_planet_specializations(ctx)
        )
        custom_names_after = names.get_custom_planet_names_locked()
        overlaid_active = names.overlay_custom_names_on_config_optimizer(
            get_active_config_optimizer(ctx), snap_state, custom_names_after
        )
        ctx.co_yearly_proj_cache.clear()
        ctx.invalidate_dashboard_cache()
    if on_publish_snapshot is not None:
        on_publish_snapshot()

    ai_job_out = _trigger_post_override_ai_replan(
        ctx, auto_replan_ai=auto_replan_ai, run_id=run_id, updated_res=updated_res, start_ai_fn=start_ai_fn
    )
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "config_optimizer_update_planet",
            "run_id": run_id,
            "active": overlaid_active,
            "result": overlaid_active["result"] if isinstance(overlaid_active, Mapping) else updated_res,
            "planet_specializations": updated_specs,
            "custom_planet_names": custom_names_after,
            "ai_planner_job": ai_job_out,
        }
    )


def handle_config_optimizer_yearly_projection(ctx: BridgeContext, payload: Mapping[str, object]) -> dict[str, object]:
    """Return cached multi-year ships-per-week projection for the active Config Optimizer target."""
    horizon_years = parse_command_int("horizon_years", payload.get("horizon_years", 5), min_val=1, max_val=15) or 5
    with ctx.intel_read_lock:
        active_co = get_active_config_optimizer(ctx)
        snap_state = get_planner_state(ctx)
    raw_res = active_co.get("result") if isinstance(active_co, Mapping) else None
    if not isinstance(active_co, Mapping) or not isinstance(raw_res, Mapping):
        raise ApiRequestError("No active Config Optimizer result available yet; run Config Optimizer first.")
    cache_key: tuple[object, ...] = (
        active_co.get("run_id"),
        active_co.get("updated_ts"),
        int(horizon_years),
        round(float(snap_state.timestamp or 0.0), 0) if snap_state is not None else 0.0,
    )
    cached_proj = ctx.co_yearly_proj_cache.get(cache_key)
    if cached_proj is None:
        cached_proj = dict(
            co_mod.compute_yearly_ships_per_week_projection(raw_res, snap_state, horizon_years=int(horizon_years))
        )
        if len(ctx.co_yearly_proj_cache) >= 16:
            ctx.co_yearly_proj_cache.clear()
        ctx.co_yearly_proj_cache[cache_key] = cached_proj
    return sanitize_json_dict({"ok": True, "command": "config_optimizer_yearly_projection", "projection": cached_proj})


def handle_config_optimizer_run(
    payload: Mapping[str, object],
    *,
    start_co_fn: Callable[..., dict[str, object]],
) -> dict[str, object]:
    """Validate `config_optimizer_run` payload and dispatch the optimization job."""
    raw_params = payload.get("params")
    if raw_params is not None and not isinstance(raw_params, Mapping):
        raise ApiRequestError("'config_optimizer_run' field 'params' must be a dictionary.")
    auto_replan_raw = payload.get("auto_replan_ai", True)
    if not isinstance(auto_replan_raw, bool):
        raise ApiRequestError("'config_optimizer_run' field 'auto_replan_ai' must be a boolean.")
    sync_raw = payload.get("sync", False)
    if not isinstance(sync_raw, bool):
        raise ApiRequestError("'config_optimizer_run' field 'sync' must be a boolean.")
    trig = bounded_api_str(payload.get("trigger_source") or "manual", default="manual")[:64]
    resp = start_co_fn(params=raw_params, auto_replan_ai=auto_replan_raw, sync=sync_raw, trigger_source=trig)
    return sanitize_json_dict(resp)
