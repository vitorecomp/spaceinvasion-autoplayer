"""Galaxy sweep finalization, action-log persistence, and end-to-end sweep workflow helper."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import random
import time

from spaceinvasion.core.model import PlannedAction, count_allocated_normal_planets
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import safe_api_int, sanitize_json_dict
from spaceinvasion.runtime.bridge._sweep_plan import (
    SweepBatchPlan,
    SweepRunCounters,
    build_dry_run_sweep_payload,
    prepare_sweep_batch_locked,
)
from spaceinvasion.runtime.bridge._sweep_smart import (
    SMART_SWEEP_STATE_KEYS,
    merge_smart_sweep_summary_for_ctx,
    refresh_smart_sweep_summary_for_ctx,
)
from spaceinvasion.runtime.bridge._sweep_step import compute_next_sweep_cursor, run_sweep_pages_loop
from spaceinvasion.runtime.bridge.intel_sync import IntelSyncService
from spaceinvasion.runtime.bridge.lifecycle import BridgeLifecycleService
from spaceinvasion.runtime.bridge.state_cache import StateCacheService


def _record_sweep_action_log(
    ctx: BridgeContext,
    batch: SweepBatchPlan,
    counters: SweepRunCounters,
    *,
    cur_gal: int,
    cur_sys: int,
    cov_txt: str,
    msg_summary: str,
) -> None:
    if ctx.db is None or counters.pages_scanned <= 0:
        return
    ap_log = ctx.last_state.active_planet() if ctx.last_state is not None else None
    ctx.db.record_action(
        source="sweep",
        command="galaxy_sweep",
        action=PlannedAction(
            priority="P2",
            kind="SCAN_GALAXY",
            planet_id=ap_log.planet_id if ap_log is not None else 0,
            planet_coords=ap_log.coords if ap_log is not None else "",
            target_name=f"Sweep {counters.pages_scanned} pages -> [{cur_gal:02d}:{cur_sys:03d}]",
            reason=(
                f"Smart 2-phase galaxy sweep ({batch.scan_phase}, coverage {cov_txt})"
                if batch.selection_mode == "smart"
                else "Periodic randomized 100-page galaxy sweep"
            ),
            executable_now=True,
        ),
        executed=True,
        dry_run=False,
        message=msg_summary,
    )


def finalize_live_sweep(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    batch: SweepBatchPlan,
    counters: SweepRunCounters,
    *,
    sweep_started_ts: float,
    aborted_reason: str | None,
    get_state_fn: Callable[..., dict[str, object]],
    save_state_fn: Callable[..., dict[str, object]],
    on_publish_snapshot: Callable[[], None] | None,
) -> dict[str, object]:
    """Persist cursor/counters after a live galaxy sweep and build the canonical summary response."""
    cur_gal, cur_sys = compute_next_sweep_cursor(ctx, batch, counters)
    now_done = round(time.time(), 3)
    with ctx.lock:
        ctx.sweep_in_progress = False
        cfg_iv = max(30.0, min(300.0, float(ctx.strategy_config.galaxy_sweep_interval_seconds or 270)))
        rand_cycle_s = random.uniform(max(60.0, cfg_iv * 0.78), min(296.0, cfg_iv))
        next_due = min(
            sweep_started_ts + min(296.0, cfg_iv),
            max(now_done + random.uniform(8.0, 25.0), sweep_started_ts + rand_cycle_s),
        )
        cur_prev = get_state_fn(now_ts=now_done, include_smart=False)
        updated_sw = save_state_fn(
            {
                "cursor_galaxy": cur_gal,
                "cursor_system": cur_sys,
                "last_sweep_started_ts": sweep_started_ts,
                "last_sweep_completed_ts": now_done,
                "next_sweep_due_ts": round(next_due, 3),
                "last_pages_scanned": counters.pages_scanned,
                "last_arrow_steps": counters.arrow_steps_used,
                "last_players_found": counters.players_found,
                "last_colonies_found": counters.colonies_found,
                "last_debris_found": counters.debris_found,
                "total_sweeps": safe_api_int(cur_prev.get("total_sweeps"), 0)
                + (1 if counters.pages_scanned > 0 else 0),
                "total_pages_scanned": safe_api_int(cur_prev.get("total_pages_scanned"), 0) + counters.pages_scanned,
            },
            ts=now_done,
        )
        if counters.pages_scanned > 0:
            state_cache.refresh_raid_targets_locked(limit=250)
            refresh_smart_sweep_summary_for_ctx(ctx, updated_sw, now_ts=now_done, force=True)
            updated_sw = merge_smart_sweep_summary_for_ctx(
                ctx, {k: v for k, v in updated_sw.items() if k not in SMART_SWEEP_STATE_KEYS}
            )
        ctx.invalidate_dashboard_cache()
        cov_txt = f"{updated_sw.get('coverage_pct', 0.0)}%"
        msg_summary = (
            f"Swept {counters.pages_scanned}/{len(batch.sweep_targets)} galaxy pages "
            f"[{batch.selection_mode} {batch.scan_phase}, coverage {cov_txt}] "
            f"(players={counters.players_found}, "
            f"colonies={counters.colonies_found if batch.collect_colonies else 'SKIP:full'}, "
            f"debris={counters.debris_found}); next cursor [{cur_gal:02d}:{cur_sys:03d}]."
        )
        if aborted_reason:
            msg_summary += f" ({aborted_reason})"
        _record_sweep_action_log(
            ctx, batch, counters, cur_gal=cur_gal, cur_sys=cur_sys, cov_txt=cov_txt, msg_summary=msg_summary
        )
    if on_publish_snapshot is not None:
        on_publish_snapshot()
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "galaxy_sweep",
            "executed": counters.pages_scanned > 0,
            "dry_run": False,
            "pages_planned": len(batch.sweep_targets),
            "pages_scanned": counters.pages_scanned,
            "arrow_steps": counters.arrow_steps_used,
            "players_found": counters.players_found,
            "colonies_found": counters.colonies_found,
            "debris_found": counters.debris_found,
            "collect_colonies": batch.collect_colonies,
            "selection_mode": batch.selection_mode,
            "scan_phase": batch.scan_phase,
            "explored_systems": updated_sw.get("explored_systems", 0),
            "total_systems": updated_sw.get("total_systems", 0),
            "coverage_pct": updated_sw.get("coverage_pct", 0.0),
            "sample_targets": batch.smart_summary.get("sample_targets", []),
            "visited_targets": [f"{g:02d}:{s:03d}" for g, s in batch.sweep_targets[: counters.pages_scanned]],
            "click_delay_seconds": updated_sw.get("click_delay_seconds", batch.cfg_delay_s),
            "click_jitter_seconds": updated_sw.get("click_jitter_seconds", batch.cfg_jitter_s),
            "aborted_reason": aborted_reason,
            "message": msg_summary,
            "galaxy_sweep": updated_sw,
        }
    )


def _check_sweep_start_preconditions(
    ctx: BridgeContext,
    *,
    dry_run: bool,
    get_state_fn: Callable[..., dict[str, object]],
    has_p0_escape_fn: Callable[[], tuple[bool, str]],
) -> dict[str, object] | None:
    with ctx.lock:
        if ctx.bot_control.get("captcha_blocked"):
            return sanitize_json_dict(
                {
                    "ok": True,
                    "command": "galaxy_sweep",
                    "executed": False,
                    "dry_run": dry_run,
                    "captcha_blocked": True,
                    "message": "Paused for CAPTCHA — confirm solved in UI before sweeping galaxy.",
                    "galaxy_sweep": get_state_fn(),
                }
            )
        if not dry_run:
            p0_urg, p0_rsn = has_p0_escape_fn()
            if p0_urg:
                return sanitize_json_dict(
                    {
                        "ok": True,
                        "command": "galaxy_sweep",
                        "executed": False,
                        "dry_run": False,
                        "preempted": True,
                        "message": p0_rsn,
                        "galaxy_sweep": get_state_fn(),
                    }
                )
    return None


def _init_sweep_batch_locked(
    ctx: BridgeContext,
    *,
    pages: int | None,
    galaxy: int | None,
    start_system: int | None,
    dry_run: bool,
    is_manual: bool,
    sweep_started_ts: float,
    get_state_fn: Callable[..., dict[str, object]],
    save_state_fn: Callable[[Mapping[str, object], float | None], dict[str, object]],
) -> SweepBatchPlan:
    with ctx.lock:
        ctx.sweep_preempt_event.clear()
        ctx.sweep_wake_event.clear()
        ctx.sweep_preempt_reason = None
        if not dry_run:
            ctx.sweep_in_progress = True
            ctx.sweep_is_manual = is_manual
            ctx.sweep_done_event.clear()
        sw_state = get_state_fn(now_ts=sweep_started_ts, include_smart=False)
        batch = prepare_sweep_batch_locked(
            ctx,
            sw_state,
            pages=pages,
            galaxy=galaxy,
            start_system=start_system,
            sweep_started_ts=sweep_started_ts,
            allocated_normal=count_allocated_normal_planets(ctx.last_state),
        )
        save_state_fn(
            {
                "last_sweep_started_ts": sweep_started_ts,
                "click_delay_seconds": batch.cfg_delay_s,
                "click_jitter_seconds": batch.cfg_jitter_s,
            },
            sweep_started_ts,
        )
    return batch


def run_galaxy_sweep_workflow(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    intel_sync: IntelSyncService,
    lifecycle: BridgeLifecycleService,
    *,
    pages: int | None,
    galaxy: int | None,
    start_system: int | None,
    delay_range: tuple[float, float] | None,
    dry_run: bool,
    priority: str,
    automatic: bool,
    manual: bool | None,
    get_state_fn: Callable[..., dict[str, object]],
    save_state_fn: Callable[[Mapping[str, object], float | None], dict[str, object]],
    sweep_views_fn: Callable[[], int],
    has_p0_escape_fn: Callable[[], tuple[bool, str]],
    on_publish_snapshot: Callable[[], None] | None,
) -> dict[str, object]:
    """Execute one complete dry-run or live galaxy sweep batch safely."""
    early = _check_sweep_start_preconditions(
        ctx, dry_run=dry_run, get_state_fn=get_state_fn, has_p0_escape_fn=has_p0_escape_fn
    )
    if early is not None:
        return early
    if not ctx.sweep_run_lock.acquire(blocking=False):
        with ctx.lock:
            cur_st = get_state_fn()
        return sanitize_json_dict(
            {
                "ok": True,
                "command": "galaxy_sweep",
                "executed": False,
                "dry_run": dry_run,
                "in_progress": True,
                "message": "Galaxy sweep is already in progress.",
                "galaxy_sweep": cur_st,
            }
        )
    sweep_started_ts = round(time.time(), 3)
    started_with_scans_allowed = bool(ctx.strategy_config.allow_galaxy_scan)
    enforce_scan_toggle = (automatic or started_with_scans_allowed) and priority != "interactive"
    is_manual = bool(manual) if manual is not None else (not enforce_scan_toggle)
    try:
        batch = _init_sweep_batch_locked(
            ctx,
            pages=pages,
            galaxy=galaxy,
            start_system=start_system,
            dry_run=dry_run,
            is_manual=is_manual,
            sweep_started_ts=sweep_started_ts,
            get_state_fn=get_state_fn,
            save_state_fn=save_state_fn,
        )
        if dry_run:
            with ctx.lock:
                dry_state = get_state_fn(now_ts=sweep_started_ts, include_smart=False)
            return build_dry_run_sweep_payload(batch, dry_state)
        counters = SweepRunCounters(prev_browser_coords=ctx.last_galaxy_browser_coords)
        aborted_reason = run_sweep_pages_loop(
            ctx,
            intel_sync,
            lifecycle,
            batch,
            counters,
            delay_range=delay_range,
            priority=priority,
            is_manual=is_manual,
            sweep_views_fn=sweep_views_fn,
            has_p0_escape_fn=has_p0_escape_fn,
            on_publish_snapshot=on_publish_snapshot,
        )
        return finalize_live_sweep(
            ctx,
            state_cache,
            batch,
            counters,
            sweep_started_ts=sweep_started_ts,
            aborted_reason=aborted_reason,
            get_state_fn=get_state_fn,
            save_state_fn=lambda upd, ts=None: save_state_fn(upd, ts),
            on_publish_snapshot=on_publish_snapshot,
        )
    finally:
        with ctx.lock:
            ctx.sweep_in_progress = False
            ctx.sweep_is_manual = False
            ctx.sweep_done_event.set()
        ctx.sweep_run_lock.release()
