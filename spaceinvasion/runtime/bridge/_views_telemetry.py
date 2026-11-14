"""Dashboard telemetry helpers: builders, production series, loot/recycle, DB summary, planner status."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
import time

from spaceinvasion.combat.points import estimate_hourly_economy
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState, normalize_planet_type
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    safe_api_float,
    safe_api_int,
    sanitize_json_dict,
)
from spaceinvasion.runtime.bridge._helpers_sig import supports_kwarg
from spaceinvasion.runtime.bridge._views_health import ViewHooks, default_evaluate_ai_gate

_LOGGER = logging.getLogger(__name__)


def count_active_builders(state: GameState | None, *, now_ts: float | None = None) -> int:
    """Count distinct non-raid planets currently running a building upgrade."""
    if state is None or not state.planets:
        return 0
    now_w = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    pid_by_coords: dict[str, int] = {}
    owned_pids: set[int] = set()
    for planet in state.planets:
        if normalize_planet_type(planet) == "raid":
            continue
        pid = safe_api_int(planet.planet_id, 0)
        if pid <= 0:
            continue
        owned_pids.add(pid)
        nc = normalize_coords(planet.coords)
        if nc:
            pid_by_coords[nc] = pid
    active_pid = safe_api_int(state.active_planet_id, 0)
    busy_pids: set[int] = set()
    for job in state.active_queues or ():
        if job.category != "building":
            continue
        if job.finish_ts is not None:
            if safe_api_float(job.finish_ts, 0.0) <= now_w:
                continue
        elif job.remaining_seconds is not None and safe_api_float(job.remaining_seconds, 0.0) <= 0.0:
            continue
        q_orig = normalize_coords(job.origin_coords or "")
        if q_orig and q_orig in pid_by_coords:
            busy_pids.add(pid_by_coords[q_orig])
        elif not q_orig and active_pid in owned_pids:
            busy_pids.add(active_pid)
    return len(busy_pids)


def compute_current_hourly_production(state: GameState | None, *, now_ts: float | None = None) -> dict[str, object]:
    """Compute current empire-wide hourly production snapshot."""
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    minute_bucket = int(eff_now // 60) * 60
    if state is None or not state.planets:
        return {
            "minute_bucket": minute_bucket,
            "recorded_at": round(eff_now, 3),
            "pig_iron_per_hour": 0.0,
            "metal_per_hour": 0.0,
            "kryptonite_per_hour": 0.0,
            "spice_per_hour": 0.0,
            "total_per_hour": 0.0,
            "speq_per_hour": 0.0,
            "planet_count": 0,
            "active_builders": 0,
        }
    econ = estimate_hourly_economy(state)
    pig_hr = round(safe_api_float(econ.get("pig_iron_per_hr"), 0.0), 1)
    met_hr = round(max(0.0, safe_api_float(econ.get("metal_per_hr"), 0.0)), 1)
    kry_hr = round(max(0.0, safe_api_float(econ.get("kryptonite_per_hr"), 0.0)), 1)
    spi_hr = round(max(0.0, safe_api_float(econ.get("spice_per_hr"), 0.0)), 1)
    tot_default = max(0.0, pig_hr) + met_hr + kry_hr + spi_hr
    tot_hr = round(max(0.0, safe_api_float(econ.get("net_resources_per_hr"), tot_default)), 1)
    speq_hr = round(max(0.0, safe_api_float(econ.get("spice_eq_per_hr"), 0.0)), 1)
    p_cnt = sum(1 for p in state.planets if normalize_planet_type(p) != "raid")
    return {
        "minute_bucket": minute_bucket,
        "recorded_at": round(eff_now, 3),
        "pig_iron_per_hour": pig_hr,
        "metal_per_hour": met_hr,
        "kryptonite_per_hour": kry_hr,
        "spice_per_hour": spi_hr,
        "total_per_hour": tot_hr,
        "speq_per_hour": speq_hr,
        "planet_count": p_cnt,
        "active_builders": count_active_builders(state, now_ts=eff_now),
    }


def get_db_summary_cached(ctx: BridgeContext) -> dict[str, object] | None:
    """Return memoized database summary keyed by DB change token."""
    if ctx.db is None:
        return None
    tok = ctx.db_total_changes_token()
    ckey: object = (tok, ctx.db.summary) if tok is not None else None
    if ckey is not None and ctx.db_summary_cache_key == ckey and ctx.db_summary_cache is not None:
        return dict(ctx.db_summary_cache)
    raw = ctx.db.summary()
    clean = sanitize_json_dict(raw) if isinstance(raw, Mapping) else None
    if ckey is not None and clean is not None:
        ctx.db_summary_cache_key = ckey
        ctx.db_summary_cache = clean
        return dict(clean)
    return clean


def get_hourly_production_history_for_dashboard(
    ctx: BridgeContext,
    hooks: ViewHooks,
    *,
    limit: int = 1440,
) -> list[dict[str, object]]:
    """Fetch hourly production history via hook or memoized DB fallback."""
    if hooks.on_get_hourly_production_history is not None:
        fn = hooks.on_get_hourly_production_history
        if supports_kwarg(fn, "limit"):
            return fn(limit=limit)
        if supports_kwarg(fn, "hours"):
            return fn(hours=24)
        return fn()
    if ctx.db is None:
        return []
    tok = ctx.db_total_changes_token()
    ckey: object = (tok, int(limit)) if tok is not None else None
    if ckey is not None and ctx.hourly_prod_cache_key == ckey:
        return [dict(r) for r in ctx.hourly_prod_cache]
    try:
        rows = ctx.db.get_hourly_production_history(limit=int(limit))
        clean = [sanitize_json_dict(r) for r in (rows or ()) if isinstance(r, Mapping)]
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.warning("Failed to fetch hourly production history", exc_info=True)
        clean = []
    if ckey is not None:
        ctx.hourly_prod_cache_key = ckey
        ctx.hourly_prod_cache = clean
    return [dict(r) for r in clean]


def _empty_loot_recycle_payload(end_bucket: int, win_hours: int) -> dict[str, object]:
    start_bucket = end_bucket - (win_hours - 1) * 3600
    return {
        "history": [
            {
                "hour_bucket": hb,
                "stolen_pig_iron": 0.0,
                "stolen_metal": 0.0,
                "stolen_kryptonite": 0.0,
                "stolen_spice": 0.0,
                "stolen_total": 0.0,
                "recycled_pig_iron": 0.0,
                "recycled_metal": 0.0,
                "recycled_kryptonite": 0.0,
                "recycled_spice": 0.0,
                "recycled_total": 0.0,
                "total": 0.0,
                "attack_count": 0,
                "recycle_count": 0,
                "updated_at": 0.0,
            }
            for hb in range(start_bucket, end_bucket + 3600, 3600)
        ],
        "summary": {
            "total_stolen": 0.0,
            "total_recycled": 0.0,
            "grand_total": 0.0,
            "attack_count": 0,
            "recycle_count": 0,
            "window_hours": win_hours,
            "stolen_pig_iron": 0.0,
            "stolen_metal": 0.0,
            "stolen_kryptonite": 0.0,
            "stolen_spice": 0.0,
            "recycled_pig_iron": 0.0,
            "recycled_metal": 0.0,
            "recycled_kryptonite": 0.0,
            "recycled_spice": 0.0,
        },
    }


def _normalize_loot_recycle_mapping(raw: Mapping[str, object]) -> dict[str, object]:
    raw_hist = raw.get("history")
    hist_list = (
        [dict(r) for r in raw_hist if isinstance(r, Mapping)]
        if isinstance(raw_hist, Sequence) and not isinstance(raw_hist, (str, bytes))
        else []
    )
    raw_sum = raw.get("summary")
    sum_dict = dict(raw_sum) if isinstance(raw_sum, Mapping) else {}
    return {"history": hist_list, "summary": sum_dict}


def get_loot_recycle_for_dashboard(
    ctx: BridgeContext,
    hooks: ViewHooks,
    *,
    hours: int = 24,
) -> dict[str, object]:
    """Fetch 24h loot & recycle telemetry via hook or memoized DB fallback."""
    if hooks.on_get_loot_recycle_hourly is not None:
        fn = hooks.on_get_loot_recycle_hourly
        raw_hook = fn(hours=hours) if supports_kwarg(fn, "hours") else fn()
        if isinstance(raw_hook, Mapping):
            return _normalize_loot_recycle_mapping(raw_hook)
    eff_now = time.time()
    win_hours = max(1, min(168, safe_api_int(hours, 24)))
    end_bucket = int(eff_now // 3600) * 3600
    if ctx.db is None:
        return _empty_loot_recycle_payload(end_bucket, win_hours)
    tok = ctx.db_total_changes_token()
    ckey: object = (tok, end_bucket, win_hours) if tok is not None else None
    if ckey is not None and ctx.loot_recycle_cache_key == ckey and ctx.loot_recycle_cache is not None:
        return _normalize_loot_recycle_mapping(ctx.loot_recycle_cache)
    try:
        payload = ctx.db.get_loot_recycle_hourly(hours=win_hours, now_ts=eff_now)
        clean = sanitize_json_dict(_normalize_loot_recycle_mapping(payload)) if isinstance(payload, Mapping) else None
        if clean is None:
            raise ValueError("invalid loot_recycle_hourly payload")
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.warning("Failed to fetch loot/recycle hourly telemetry", exc_info=True)
        return {
            "history": [],
            "summary": {
                "total_stolen": 0.0,
                "total_recycled": 0.0,
                "grand_total": 0.0,
                "attack_count": 0,
                "recycle_count": 0,
                "window_hours": win_hours,
            },
        }
    if ckey is not None:
        tok_after = ctx.db_total_changes_token()
        ctx.loot_recycle_cache_key = (tok_after, end_bucket, win_hours) if tok_after is not None else ckey
        ctx.loot_recycle_cache = clean
    return _normalize_loot_recycle_mapping(clean)


def build_planner_status_for_dashboard(
    ctx: BridgeContext,
    hooks: ViewHooks,
    dash_state: GameState | None,
) -> dict[str, object]:
    """Build `planner_status` snapshot from `PlannerJobService` hook or lightweight fallback."""
    if hooks.on_get_planner_jobs_status is not None:
        fn = hooks.on_get_planner_jobs_status
        if supports_kwarg(fn, "state"):
            return dict(fn(state=dash_state, cfg=ctx.strategy_config))
        return dict(fn())
    gate = (
        hooks.on_evaluate_ai_gate(dash_state, cfg=ctx.strategy_config)
        if hooks.on_evaluate_ai_gate is not None
        else default_evaluate_ai_gate(ctx, dash_state, ctx.strategy_config)
    )
    with ctx.planner_jobs_lock:
        co_job = dict(ctx.config_opt_job)
        ai_job = dict(ctx.ai_planner_job)
        now_t = time.time()
        for job in (co_job, ai_job):
            started = job.get("started_ts")
            if job.get("status") == "running" and isinstance(started, (int, float)):
                job["elapsed_ms"] = round(max(0.0, (now_t - float(started)) * 1000.0), 1)
    return {
        "config_optimizer_job": co_job,
        "ai_planner_job": ai_job,
        "colonies_owned": gate["colonies_owned"],
        "max_planets": gate["max_planets"],
        "live_ai_unlocked": gate["live_ai_unlocked"],
        "active_planner_mode": gate["active_planner_mode"],
        "effective_planner_mode": gate["effective_planner_mode"],
        "fallback_reason": gate["fallback_reason"],
        "pending_field_conflicts": [],
    }
