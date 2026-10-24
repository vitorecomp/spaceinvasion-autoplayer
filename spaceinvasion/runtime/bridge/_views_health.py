"""Lock-free `/health` and `/bot_status` payload builders and `ViewHooks` contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
import math
import time

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import GameState, PlannedAction
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import safe_api_int, sanitize_json_dict
from spaceinvasion.runtime.bridge._intel_sectors import get_scanned_systems_for_ctx
from spaceinvasion.runtime.bridge._sweep_smart import has_urgent_p0_escape_flight_for_ctx


@dataclass
class ViewHooks:
    """Explicit collaborator callbacks bound into `DashboardViewService`."""

    on_plan_with_summary: Callable[..., tuple[list[PlannedAction], dict[str, object] | None]] | None = None
    on_serialize_action: Callable[..., dict[str, object]] | None = None
    on_get_co_building_goals: Callable[[], dict[str, object] | None] | None = None
    on_get_active_ai_planner: Callable[[], dict[str, object] | None] | None = None
    on_evaluate_ai_gate: Callable[..., dict[str, object]] | None = None
    on_get_planner_jobs_status: Callable[..., dict[str, object]] | None = None
    on_get_transmitter_status: Callable[[], dict[str, object]] | None = None
    on_get_galaxy_sweep_state: Callable[[], dict[str, object]] | None = None
    on_is_galaxy_scan_blocking: Callable[[], bool] | None = None
    on_has_urgent_p0_escape: Callable[[], tuple[bool, str]] | None = None
    on_get_scanned_systems: Callable[[], dict[str, float]] | None = None
    on_get_throttle_cooldowns: Callable[[], dict[int, float]] | None = None
    on_get_fleet_slot_cooldowns: Callable[[], dict[str, float]] | None = None
    on_get_hourly_production_history: Callable[..., list[dict[str, object]]] | None = None
    on_get_loot_recycle_hourly: Callable[..., dict[str, object]] | None = None
    on_build_building_resource_requests: (
        Callable[[GameState | None, list[PlannedAction], Mapping[int, dict[str, object]]], list[dict[str, object]]]
        | None
    ) = None
    on_attach_raid_stats: Callable[[GameState], GameState] | None = None
    on_get_raid_stats_cached: Callable[..., dict[str, object]] | None = None
    on_record_hourly_production: Callable[[GameState], dict[str, object]] | None = None
    on_maybe_trigger_ai_drift_replan: Callable[[], bool] | None = None


def get_fleet_slot_cooldowns_for_ctx(ctx: BridgeContext) -> dict[str, float]:
    """Return active per-planet fleet slot cooldowns in seconds and prune expired entries."""
    now_m = time.monotonic()
    active: dict[str, float] = {}
    expired: list[int] = []
    for pid, until_m in list(ctx.fleet_slot_cooldown_until.items()):
        if not isinstance(until_m, (int, float)) or not math.isfinite(until_m):
            expired.append(pid)
            continue
        rem = float(until_m) - now_m
        if rem > 0.0:
            active[str(int(pid))] = round(rem, 2)
        else:
            expired.append(pid)
    for pid in expired:
        ctx.fleet_slot_cooldown_until.pop(pid, None)
    return active


def build_health_payload_lock_free(
    ctx: BridgeContext,
    *,
    include_busy_if_locked: bool = True,
) -> dict[str, object]:
    """Build `/health` lock-free matching `140_get_health.json` keys without acquiring `ctx.lock`."""
    snap = ctx.snapshot_publisher.current()
    busy = ctx.lock.locked()
    port_open = ctx.is_browser_port_open()
    rate_rem = ctx.page_limiter.rate_limit_remaining()
    bot_ctrl_snap = dict(ctx.bot_control)
    cap_blocked = bool(bot_ctrl_snap.get("captcha_blocked"))
    sweep_active = bool(ctx.sweep_in_progress)
    snap_in_game = bool(snap.health.get("in_game", False)) if snap is not None else False
    in_game = (
        port_open and not cap_blocked and (ctx.last_state is not None or rate_rem > 0.0 or sweep_active or snap_in_game)
    )
    current_url: object = ctx.last_url
    if not current_url and snap is not None:
        current_url = snap.health.get("url")
    payload: dict[str, object] = {
        "ok": port_open,
        "debug_host": ctx.debug_host,
        "debug_port": ctx.debug_port,
        "debug_port_open": port_open,
        "novnc_url": ctx.novnc_url,
        "in_game": in_game,
        "rate_limited": rate_rem > 0.0,
        "retry_after": round(rate_rem, 1),
        "page_views_last_60s": ctx.page_limiter.views_last_60s(),
        "page_view_limit_per_minute": ctx.page_limiter.max_views_per_minute,
        "rate_limit_hits": ctx.page_limiter.rate_limit_hits,
        "url": current_url if current_url else None,
        "action_batch_interval_seconds": ctx.strategy_config.action_batch_interval_seconds,
        "remaining_hourly_actions": ctx.live_executor.remaining_hourly_budget(),
        "galaxy_sweep_in_progress": sweep_active,
        "bot_control": bot_ctrl_snap,
        "db_backend": ctx.build_db_backend_status(include_ping=False),
    }
    if include_busy_if_locked and busy:
        payload["busy"] = True
    return sanitize_json_dict(payload)


def build_dashboard_embedded_health(
    ctx: BridgeContext,
    *,
    port_open: bool,
    in_game: bool,
    current_url: object,
) -> dict[str, object]:
    """Build the 13-key embedded `health` dict matching `141_get_dashboard.json`."""
    rate_rem = ctx.page_limiter.rate_limit_remaining()
    return {
        "ok": port_open,
        "debug_port": ctx.debug_port,
        "debug_port_open": port_open,
        "in_game": in_game,
        "rate_limited": rate_rem > 0.0,
        "retry_after": round(rate_rem, 1),
        "page_views_last_60s": ctx.page_limiter.views_last_60s(),
        "page_view_limit_per_minute": ctx.page_limiter.max_views_per_minute,
        "rate_limit_hits": ctx.page_limiter.rate_limit_hits,
        "url": current_url if current_url else None,
        "novnc_url": ctx.novnc_url,
        "action_batch_interval_seconds": ctx.strategy_config.action_batch_interval_seconds,
        "remaining_hourly_actions": ctx.live_executor.remaining_hourly_budget(),
    }


def _resolve_sweep_blocking(ctx: BridgeContext, hooks: ViewHooks) -> bool:
    if hooks.on_is_galaxy_scan_blocking is not None:
        return bool(hooks.on_is_galaxy_scan_blocking())
    return bool(ctx.sweep_in_progress and ctx.strategy_config.galaxy_scan_block_other_actions)


def _resolve_throttle_cooldowns(ctx: BridgeContext, hooks: ViewHooks) -> dict[int, float]:
    if hooks.on_get_throttle_cooldowns is not None:
        return hooks.on_get_throttle_cooldowns()
    if ctx.db is not None:
        return dict(ctx.db.get_last_throttle_timestamps())
    return dict(ctx.throttle_timestamps)


def build_bot_control_payload_locked(ctx: BridgeContext, hooks: ViewHooks) -> dict[str, object]:
    """Build the complete `/bot_status` payload under `ctx.state_lock` (never `ctx.lock`)."""
    metal_prod = {
        str(pid): safe_api_int(rates.get("resource2", 100), 100) > 0
        for pid, rates in ctx.economy_rates.items()
        if isinstance(rates, Mapping)
    }
    p0_ready, _ = (
        hooks.on_has_urgent_p0_escape()
        if hooks.on_has_urgent_p0_escape is not None
        else has_urgent_p0_escape_flight_for_ctx(ctx)
    )
    scanned_sys = (
        hooks.on_get_scanned_systems() if hooks.on_get_scanned_systems is not None else get_scanned_systems_for_ctx(ctx)
    )
    fleet_cds = (
        hooks.on_get_fleet_slot_cooldowns()
        if hooks.on_get_fleet_slot_cooldowns is not None
        else get_fleet_slot_cooldowns_for_ctx(ctx)
    )
    tm_status = (
        hooks.on_get_transmitter_status()
        if hooks.on_get_transmitter_status is not None
        else dict(ctx.transmitter_state_mem)
    )
    sw_state = (
        hooks.on_get_galaxy_sweep_state() if hooks.on_get_galaxy_sweep_state is not None else dict(ctx.galaxy_sweep_mem)
    )
    manual_queue = ctx.db.get_manual_queue(status="pending", limit=200) if ctx.db is not None else []
    payload = sanitize_json_dict(
        {
            "ok": True,
            **dict(ctx.bot_control),
            "galaxy_sweep_in_progress": bool(ctx.sweep_in_progress),
            "galaxy_scan_blocking": _resolve_sweep_blocking(ctx, hooks),
            "p0_escape_flight_ready": bool(p0_ready),
            "scanned_systems": scanned_sys,
            "throttle_cooldowns": _resolve_throttle_cooldowns(ctx, hooks),
            "metal_overflow_paused_planets": sorted(int(pid) for pid in ctx.metal_overflow_paused_planets),
            "fleet_slot_cooldowns": fleet_cds,
            "economy_sync_timestamps": dict(ctx.economy_sync_timestamps),
            "metal_production_by_planet": metal_prod,
            "manual_queue": manual_queue,
            "transmitter_status": tm_status,
            "galaxy_sweep": sw_state,
            "novnc_url": ctx.novnc_url,
            "strategy_config": asdict(ctx.strategy_config),
        }
    )
    ctx.last_bot_control_payload = payload
    return payload


def build_bot_control_busy_fallback(ctx: BridgeContext) -> dict[str, object]:
    """Serve `/bot_status` lock-free from snapshot/cache when `ctx.lock` is busy."""
    snap = ctx.snapshot_publisher.current()
    cached: Mapping[str, object] | None = (
        snap.bot_control if (snap is not None and snap.bot_control) else ctx.last_bot_control_payload
    )
    bot_ctrl_snap = dict(ctx.bot_control)
    sw_active = bool(ctx.sweep_in_progress)
    sw_blocking = bool(sw_active and ctx.strategy_config.galaxy_scan_block_other_actions)
    if isinstance(cached, Mapping):
        fallback: dict[str, object] = dict(cached)
        fallback.update(bot_ctrl_snap)
        fallback["ok"] = True
        fallback["busy"] = True
        fallback["galaxy_sweep_in_progress"] = sw_active
        fallback["galaxy_scan_blocking"] = sw_blocking
        raw_fb_sw = fallback.get("galaxy_sweep")
        fb_sw = dict(raw_fb_sw) if isinstance(raw_fb_sw, Mapping) else {}
        fb_sw["in_progress"] = sw_active
        fb_sw["blocking_other_actions"] = sw_blocking
        fallback["galaxy_sweep"] = fb_sw
        return sanitize_json_dict(fallback)
    return sanitize_json_dict(
        {
            "ok": True,
            "busy": True,
            **bot_ctrl_snap,
            "galaxy_sweep_in_progress": sw_active,
            "galaxy_scan_blocking": sw_blocking,
            "p0_escape_flight_ready": False,
            "scanned_systems": dict(ctx.scanned_systems),
            "throttle_cooldowns": {},
            "metal_overflow_paused_planets": sorted(int(pid) for pid in ctx.metal_overflow_paused_planets),
            "fleet_slot_cooldowns": {},
            "economy_sync_timestamps": dict(ctx.economy_sync_timestamps),
            "metal_production_by_planet": {},
            "manual_queue": [],
            "transmitter_status": {},
            "galaxy_sweep": {"in_progress": sw_active, "blocking_other_actions": sw_blocking},
        }
    )


def default_evaluate_ai_gate(ctx: BridgeContext, state: GameState | None, cfg: StrategyConfig) -> dict[str, object]:
    """Fallback AI planner gate evaluation when `on_evaluate_ai_gate` hook is unbound."""
    from spaceinvasion.core.config import normalize_planner_mode
    from spaceinvasion.core.model import count_allocated_normal_planets

    colonies_owned = count_allocated_normal_planets(state)
    max_planets = max(1, safe_api_int(cfg.max_planets, 11))
    mode = normalize_planner_mode(cfg.planner_mode)
    live_unlocked = colonies_owned >= max_planets
    eff_mode = mode if (mode != "ai_planner" or live_unlocked) else "priority"
    fallback_reason = (
        f"Need {max_planets} allocated planets for live AI planner ({colonies_owned}/{max_planets})."
        if mode == "ai_planner" and not live_unlocked
        else None
    )
    return {
        "colonies_owned": colonies_owned,
        "max_planets": max_planets,
        "live_ai_unlocked": live_unlocked,
        "active_planner_mode": mode,
        "effective_planner_mode": eff_mode,
        "fallback_reason": fallback_reason,
    }
