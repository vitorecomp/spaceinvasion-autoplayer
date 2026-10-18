"""Per-page galaxy sweep browser stepping, pacing, XHR budgeting, and page loop helpers."""

from __future__ import annotations

from collections.abc import Callable
import random
import time

from spaceinvasion.combat.fleet import ARROW_NAV_MAX_DISTANCE, extract_galaxy_system_coords, parse_galaxy_system_html
from spaceinvasion.core.state import count_allocated_normal_planets
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import safe_api_float, safe_api_int
from spaceinvasion.runtime.bridge._helpers_sig import call_with_optional_priority, supports_kwarg
from spaceinvasion.runtime.bridge._intel_messages import extract_client_current_url, is_real_client_method
from spaceinvasion.runtime.bridge._intel_sectors import max_galaxies_for_ctx
from spaceinvasion.runtime.bridge._sweep_plan import SweepBatchPlan, SweepRunCounters
from spaceinvasion.runtime.bridge._sweep_smart import update_live_smart_summary_for_sector
from spaceinvasion.runtime.bridge.intel_sync import IntelSyncService
from spaceinvasion.runtime.bridge.lifecycle import BridgeLifecycleService
from spaceinvasion.runtime.bridge.state_cache import StateCacheService
from spaceinvasion.session.auth import AntiBotChallengeError
from spaceinvasion.session.client import GameBrowserClient, RateLimitError


def record_sweep_page_locked(
    ctx: BridgeContext,
    intel_sync: IntelSyncService,
    counters: SweepRunCounters,
    batch: SweepBatchPlan,
    page_html: str,
    page_gal: int,
    page_sys: int,
    *,
    collect_col_flag: bool,
    count_as_planned: bool,
) -> None:
    """Parse and persist one scanned galaxy system page and advance sweep counters."""
    ctx.sweep_xhr_timestamps.append(time.monotonic())
    ctx.last_galaxy_browser_coords = (page_gal, page_sys)
    counters.prev_browser_coords = (page_gal, page_sys)
    slots = parse_galaxy_system_html(page_html, galaxy=page_gal, system=page_sys)
    players_on_page = sum(1 for s in slots if s.status_color in ("yellow", "light_red") or s.is_spacia)
    debris_on_page = sum(1 for s in slots if s.has_debris)
    free_on_page = sum(1 for s in slots if s.status_color == "free")

    slots_for_scan = (
        slots
        if collect_col_flag
        else tuple(s for s in slots if s.status_color != "free" or s.has_debris or s.is_spacia)
    )
    intel_sync.persist_galaxy_scan(page_gal, page_sys, slots_for_scan)
    intel_sync.persist_galaxy_slots(slots_for_scan, refresh_targets=False)
    if count_as_planned:
        counters.pages_scanned += 1
    counters.players_found += players_on_page
    counters.debris_found += debris_on_page
    if collect_col_flag:
        counters.colonies_found += free_on_page
    update_live_smart_summary_for_sector(
        ctx, f"{page_gal:02d}:{page_sys:03d}", page_gal, batch.known_scanned_keys, batch.scan_gal_set
    )


def fetch_sweep_page_with_client(
    ctx: BridgeContext,
    client: GameBrowserClient,
    *,
    step_idx: int,
    cur_gal: int,
    cur_sys: int,
    priority: str,
    on_intermediate: Callable[[str, int, int], None],
) -> tuple[str, int, int, bool]:
    """Navigate `client` to `(cur_gal, cur_sys)` and return `(html, actual_gal, actual_sys, used_arrow)`."""
    if step_idx == 0:
        call_with_optional_priority(
            client.ensure_in_game, priority="interactive" if priority == "interactive" else "normal"
        )
        url_now = extract_client_current_url(client)
        if url_now:
            ctx.last_url = url_now
    if is_real_client_method(client, "step_galaxy_system"):
        if supports_kwarg(client.step_galaxy_system, "on_intermediate_page"):
            html, actual_gal, actual_sys = client.step_galaxy_system(
                galaxy=cur_gal, system=cur_sys, pace=False, priority=priority, on_intermediate_page=on_intermediate
            )
        else:
            html, actual_gal, actual_sys = client.step_galaxy_system(
                galaxy=cur_gal, system=cur_sys, pace=False, priority=priority
            )
        try:
            nav_method = str(client.last_galaxy_nav_method or "")
        except AttributeError:
            nav_method = ""
        return html, actual_gal, actual_sys, nav_method == "arrow"
    if is_real_client_method(client, "open_galaxy_with_coords"):
        html, actual_gal, actual_sys = client.open_galaxy_with_coords(galaxy=cur_gal, system=cur_sys, priority=priority)
        return html, actual_gal, actual_sys, False
    html = StateCacheService.open_galaxy(client, galaxy=cur_gal, system=cur_sys, priority=priority)
    actual_gal, actual_sys = extract_galaxy_system_coords(html, default=(cur_gal, cur_sys))
    return html, actual_gal, actual_sys, False


def wait_sweep_pacing(
    ctx: BridgeContext,
    duration_s: float,
    *,
    is_manual: bool,
    priority: str,
) -> bool:
    """Sleep for `duration_s` while waking immediately on stop, preemption, pause, or disable."""
    rem = max(0.0, float(duration_s))
    if rem <= 0.0:
        return False
    if (
        ctx.sweep_stop_event.is_set()
        or ctx.sweep_preempt_event.is_set()
        or bool(ctx.bot_control.get("captcha_blocked"))
        or (bool(ctx.bot_control.get("paused")) and priority != "interactive")
        or (not is_manual and not bool(ctx.strategy_config.allow_galaxy_scan))
    ):
        return True
    stop_ev = ctx.sweep_stop_event
    if "wait" in vars(stop_ev):
        return bool(stop_ev.wait(rem))
    if ctx.sweep_wake_event.wait(rem):
        return True
    return bool(stop_ev.is_set())


def wait_for_xhr_budget(
    ctx: BridgeContext,
    *,
    prev_coords: tuple[int, int] | None,
    cur_gal: int,
    cur_sys: int,
    is_manual: bool,
    priority: str,
    sweep_views_fn: Callable[[], int],
) -> None:
    """Wait until the 60-second page-view + sweep-XHR budget allows the next sector request."""
    needed_xhr = 1
    if prev_coords is not None and prev_coords[0] == cur_gal and 1 <= prev_coords[1] <= 400 and 1 <= cur_sys <= 400:
        delta_est = abs(cur_sys - prev_coords[1])
        circ_est = min(delta_est, 400 - delta_est)
        if 1 <= circ_est < ARROW_NAV_MAX_DISTANCE:
            needed_xhr = circ_est
    budget_deadline = time.monotonic() + 75.0
    while not ctx.sweep_stop_event.is_set() and time.monotonic() < budget_deadline:
        if ctx.sweep_preempt_event.is_set():
            break
        if bool(ctx.bot_control.get("captcha_blocked")) or (
            bool(ctx.bot_control.get("paused")) and priority != "interactive"
        ):
            break
        if not is_manual and not bool(ctx.strategy_config.allow_galaxy_scan):
            break
        rem_rl = ctx.rate_limit_remaining()
        total_60s = ctx.page_limiter.views_last_60s() + sweep_views_fn()
        sweep_cap = max(6, int(ctx.page_limiter.max_views_per_minute))
        if rem_rl <= 0.0 and (total_60s + needed_xhr) <= sweep_cap:
            break
        sleep_s = max(1.0, min(5.0, rem_rl if rem_rl > 0.0 else random.uniform(1.1, 2.2)))
        if wait_sweep_pacing(ctx, sleep_s, is_manual=is_manual, priority=priority):
            break


def compute_next_sweep_cursor(
    ctx: BridgeContext,
    batch: SweepBatchPlan,
    counters: SweepRunCounters,
) -> tuple[int, int]:
    """Compute the persisted cursor position after a live sweep finishes or aborts."""
    max_g = max_galaxies_for_ctx(ctx)
    if batch.selection_mode == "contiguous":
        if counters.pages_scanned < len(batch.sweep_targets):
            cur_gal, cur_sys = batch.sweep_targets[counters.pages_scanned]
        elif batch.contiguous_tail is not None:
            cur_gal, cur_sys = batch.contiguous_tail
        else:
            cur_gal, cur_sys = batch.sweep_targets[-1]
    elif counters.prev_browser_coords is not None and counters.pages_scanned > 0:
        last_g, last_s = counters.prev_browser_coords
        cur_gal = max(1, min(max_g, int(last_g)))
        cur_sys = 1 if int(last_s) >= 400 else max(1, int(last_s) + 1)
    elif counters.pages_scanned > 0:
        last_g, last_s = batch.sweep_targets[counters.pages_scanned - 1]
        cur_gal = max(1, min(max_g, int(last_g)))
        cur_sys = 1 if int(last_s) >= 400 else max(1, int(last_s) + 1)
    else:
        cur_gal, cur_sys = batch.sweep_targets[0]
    return max(1, min(max_g, int(cur_gal))), max(1, min(400, int(cur_sys)))


def execute_single_sweep_step(
    ctx: BridgeContext,
    intel_sync: IntelSyncService,
    lifecycle: BridgeLifecycleService,
    batch: SweepBatchPlan,
    counters: SweepRunCounters,
    *,
    step_idx: int,
    cur_gal: int,
    cur_sys: int,
    priority: str,
    is_manual: bool,
    has_p0_escape_fn: Callable[[], tuple[bool, str]],
    on_publish_snapshot: Callable[[], None] | None,
) -> str | None:
    """Acquire `ctx.lock`, check abort invariants, fetch one sector page, and record slots."""
    with ctx.lock:
        if ctx.sweep_preempt_event.is_set():
            return str(ctx.sweep_preempt_reason or "Preempted by P0 emergency Escape Flight.")
        p0_urg, p0_rsn = has_p0_escape_fn()
        if p0_urg:
            return p0_rsn
        if ctx.bot_control.get("captcha_blocked"):
            return str(ctx.bot_control.get("captcha_message") or "CAPTCHA active")
        if ctx.bot_control.get("paused") and priority != "interactive":
            return "Bot paused via operator UI."
        if not is_manual and not bool(ctx.strategy_config.allow_galaxy_scan):
            return "Galaxy scans suspended via operator UI."
        ctx.raise_if_rate_limited()
        allocated_now = count_allocated_normal_planets(ctx.last_state)
        max_cap = max(1, safe_api_int(ctx.strategy_config.max_planets, 11))
        collect_colonies = bool(allocated_now < max_cap)

        def _on_inter(inter_html: str, ig: int, isys: int) -> None:
            record_sweep_page_locked(
                ctx,
                intel_sync,
                counters,
                batch,
                inter_html,
                ig,
                isys,
                collect_col_flag=collect_colonies,
                count_as_planned=False,
            )

        try:
            with ctx.client_factory(ctx.debug_port) as client:
                html, actual_gal, actual_sys, used_arrow = fetch_sweep_page_with_client(
                    ctx,
                    client,
                    step_idx=step_idx,
                    cur_gal=cur_gal,
                    cur_sys=cur_sys,
                    priority=priority,
                    on_intermediate=_on_inter,
                )
                if used_arrow:
                    counters.arrow_steps_used += 1
                eff_max_g = intel_sync.observe_galaxy_universe_bounds_locked(client=client, html=html)
        except RateLimitError as rl_exc:
            ctx.record_server_rate_limit(rl_exc)
            return f"Rate-limit cooldown triggered ({rl_exc.retry_after:.0f}s)."
        except AntiBotChallengeError as ab_exc:
            lifecycle.record_antibot_pause(ab_exc)
            if on_publish_snapshot is not None:
                on_publish_snapshot()
            return f"Anti-bot challenge detected: {ab_exc}"

        record_sweep_page_locked(
            ctx,
            intel_sync,
            counters,
            batch,
            html,
            actual_gal,
            actual_sys,
            collect_col_flag=collect_colonies,
            count_as_planned=True,
        )
        if cur_gal > eff_max_g:
            return f"Observed universe maxGalaxies={eff_max_g} (target galaxy {cur_gal} out of bounds); re-anchoring sweep."
    return None


def run_sweep_pages_loop(
    ctx: BridgeContext,
    intel_sync: IntelSyncService,
    lifecycle: BridgeLifecycleService,
    batch: SweepBatchPlan,
    counters: SweepRunCounters,
    *,
    delay_range: tuple[float, float] | None,
    priority: str,
    is_manual: bool,
    sweep_views_fn: Callable[[], int],
    has_p0_escape_fn: Callable[[], tuple[bool, str]],
    on_publish_snapshot: Callable[[], None] | None,
) -> str | None:
    """Iterate planned sweep targets with randomized pacing, XHR throttling, and abort guards."""
    lo_d = max(0.0, float(min(delay_range[0], delay_range[1]))) if delay_range is not None else 0.0
    hi_d = max(lo_d, float(max(delay_range[0], delay_range[1]))) if delay_range is not None else 0.0
    has_pacing = (delay_range is None) or (hi_d > 0.0)

    for step_idx, (cur_gal, cur_sys) in enumerate(batch.sweep_targets):
        if ctx.sweep_stop_event.is_set():
            return "Sweep stopped on shutdown."
        if ctx.sweep_preempt_event.is_set():
            return str(ctx.sweep_preempt_reason or "Preempted by P0 emergency Escape Flight.")
        if not is_manual and not bool(ctx.strategy_config.allow_galaxy_scan):
            return "Galaxy scans suspended via operator UI."
        if step_idx > 0 and has_pacing:
            if delay_range is not None:
                wait_jitter = random.uniform(lo_d, hi_d)
            else:
                base_d = max(0.2, min(60.0, safe_api_float(ctx.strategy_config.galaxy_scan_click_delay_seconds, 1.5)))
                jit = max(0.0, min(30.0, safe_api_float(ctx.strategy_config.galaxy_scan_click_jitter_seconds, 0.6)))
                wait_jitter = max(0.2, base_d + (random.uniform(-jit, jit) if jit > 0.0 else 0.0))
            if wait_jitter > 0.0 and wait_sweep_pacing(ctx, wait_jitter, is_manual=is_manual, priority=priority):
                if ctx.sweep_stop_event.is_set():
                    return "Sweep stopped on shutdown."
        if has_pacing:
            wait_for_xhr_budget(
                ctx,
                prev_coords=counters.prev_browser_coords,
                cur_gal=cur_gal,
                cur_sys=cur_sys,
                is_manual=is_manual,
                priority=priority,
                sweep_views_fn=sweep_views_fn,
            )
        abort = execute_single_sweep_step(
            ctx,
            intel_sync,
            lifecycle,
            batch,
            counters,
            step_idx=step_idx,
            cur_gal=cur_gal,
            cur_sys=cur_sys,
            priority=priority,
            is_manual=is_manual,
            has_p0_escape_fn=has_p0_escape_fn,
            on_publish_snapshot=on_publish_snapshot,
        )
        if abort is not None:
            return abort
    return None
