"""Galaxy sweep orchestration, background worker, preemption, and cursor management."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import threading
import time

from spaceinvasion.core.state import GameState
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import safe_api_float, safe_api_int, sanitize_json_dict
from spaceinvasion.runtime.bridge._intel_sectors import max_galaxies_for_ctx
from spaceinvasion.runtime.bridge._sweep_finalize import run_galaxy_sweep_workflow
from spaceinvasion.runtime.bridge._sweep_smart import (
    has_urgent_p0_escape_flight_for_ctx,
    merge_smart_sweep_summary_for_ctx,
    plan_smart_galaxy_sweep_for_ctx,
    refresh_smart_sweep_summary_for_ctx,
    store_smart_sweep_summary_for_ctx,
)
from spaceinvasion.runtime.bridge._sweep_worker import (
    launch_background_galaxy_sweep,
    run_galaxy_sweep_worker_loop,
)
from spaceinvasion.runtime.bridge.intel_sync import IntelSyncService
from spaceinvasion.runtime.bridge.lifecycle import BridgeLifecycleService
from spaceinvasion.runtime.bridge.state_cache import StateCacheService


class GalaxySweepService:
    """Owns smart 2-phase galaxy scanning, background worker loop, preemption, and cursor state."""

    def __init__(
        self,
        ctx: BridgeContext,
        state_cache: StateCacheService,
        intel_sync: IntelSyncService,
        lifecycle: BridgeLifecycleService,
    ) -> None:
        self._ctx = ctx
        self._state_cache = state_cache
        self._intel_sync = intel_sync
        self._lifecycle = lifecycle
        self._on_has_urgent_p0_escape: Callable[[], tuple[bool, str]] | None = None
        self._on_publish_snapshot: Callable[[], None] | None = None

    def bind_hooks(
        self,
        *,
        on_has_urgent_p0_escape: Callable[[], tuple[bool, str]] | None = None,
        on_publish_snapshot: Callable[[], None] | None = None,
    ) -> None:
        """Bind optional post-construction collaborator callbacks explicitly."""
        if on_has_urgent_p0_escape is not None:
            self._on_has_urgent_p0_escape = on_has_urgent_p0_escape
        if on_publish_snapshot is not None:
            self._on_publish_snapshot = on_publish_snapshot

    def is_galaxy_scan_blocking_locked(self) -> bool:
        """Return True when an active galaxy sweep blocks non-emergency browser actions."""
        return bool(self._ctx.sweep_in_progress and self._ctx.strategy_config.galaxy_scan_block_other_actions)

    @staticmethod
    def galaxy_scan_busy_error_message() -> str:
        """Return canonical error message surfaced when galaxy scanning blocks an action."""
        return "Galaxy scan in progress — wait for scan to finish, or Pause / disable Galaxy Scan first."

    def has_urgent_p0_escape_flight_locked(self) -> tuple[bool, str]:
        """Return `(True, reason)` when an emergency P0 Escape Flight must preempt sweeps."""
        if self._on_has_urgent_p0_escape is not None:
            return self._on_has_urgent_p0_escape()
        return has_urgent_p0_escape_flight_for_ctx(self._ctx)

    def sweep_views_last_60s(self) -> int:
        """Return the number of galaxy sweep XHR steps recorded within the last 60 seconds."""
        cutoff = time.monotonic() - 60.0
        ts_q = self._ctx.sweep_xhr_timestamps
        while ts_q and ts_q[0] <= cutoff:
            ts_q.popleft()
        return len(ts_q)

    def _apply_sweep_timing_fields(self, st: dict[str, object], *, include_blocking: bool) -> dict[str, object]:
        cfg = self._ctx.strategy_config
        st["click_delay_seconds"] = round(
            max(0.2, min(60.0, safe_api_float(cfg.galaxy_scan_click_delay_seconds, 1.5))), 3
        )
        st["click_jitter_seconds"] = round(
            max(0.0, min(30.0, safe_api_float(cfg.galaxy_scan_click_jitter_seconds, 0.6))), 3
        )
        st["in_progress"] = bool(self._ctx.sweep_in_progress)
        if include_blocking:
            st["blocking_other_actions"] = bool(self.is_galaxy_scan_blocking_locked())
        return st

    def get_galaxy_sweep_state_locked(
        self,
        *,
        now_ts: float | None = None,
        include_smart: bool = True,
    ) -> dict[str, object]:
        """Return persisted/in-memory galaxy sweep telemetry merged with smart coverage metrics."""
        ctx = self._ctx
        eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if ctx.db is not None:
            st: dict[str, object] = dict(ctx.db.get_galaxy_sweep_state(now_ts=eff_now))
        else:
            st = dict(ctx.galaxy_sweep_mem)
            st["due"] = eff_now >= safe_api_float(st.get("next_sweep_due_ts"), 0.0)
        self._apply_sweep_timing_fields(st, include_blocking=True)
        if include_smart:
            refresh_smart_sweep_summary_for_ctx(ctx, st, now_ts=eff_now)
        return merge_smart_sweep_summary_for_ctx(ctx, st)

    def save_galaxy_sweep_state_locked(
        self,
        updates: Mapping[str, object],
        *,
        ts: float | None = None,
    ) -> dict[str, object]:
        """Persist galaxy sweep cursor/counter updates and return normalized state."""
        ctx = self._ctx
        eff_now = round(safe_api_float(ts, time.time()) if ts is not None else time.time(), 3)
        if ctx.db is not None:
            st: dict[str, object] = dict(ctx.db.save_galaxy_sweep_state(updates, ts=eff_now))
        else:
            for key, val in updates.items():
                if key != "due":
                    ctx.galaxy_sweep_mem[str(key)] = val
            st = dict(ctx.galaxy_sweep_mem)
            st["due"] = eff_now >= safe_api_float(st.get("next_sweep_due_ts"), 0.0)
        self._apply_sweep_timing_fields(st, include_blocking=False)
        return merge_smart_sweep_summary_for_ctx(ctx, st)

    def reset_galaxy_sweep_cursor_locked(self, *, galaxy: int = 1, system: int = 1) -> dict[str, object]:
        """Reset sweep cursor to `(galaxy, system)` and mark sweep due immediately."""
        ctx = self._ctx
        max_g = max_galaxies_for_ctx(ctx)
        eff_gal = max(1, min(max_g, safe_api_int(galaxy, 1)))
        eff_sys = max(1, min(400, safe_api_int(system, 1)))
        ctx.last_galaxy_browser_coords = None
        ctx.smart_sweep_summary_ts = 0.0
        updated = self.save_galaxy_sweep_state_locked(
            {"cursor_galaxy": eff_gal, "cursor_system": eff_sys, "next_sweep_due_ts": 0.0}
        )
        ctx.invalidate_dashboard_cache()
        if self._on_publish_snapshot is not None:
            self._on_publish_snapshot()
        return sanitize_json_dict(updated)

    def plan_smart_galaxy_sweep_locked(
        self,
        *,
        batch_size: int,
        galaxies: Sequence[int],
        collect_colonies: bool,
        now_ts: float,
        sector_intel_override: Mapping[str, object] | None = None,
        explore_first_override: bool | None = None,
        refresh_pct_override: int | None = None,
        scanned_systems_override: Mapping[str, float] | None = None,
        state_override: GameState | None = None,
        raid_targets_override: Sequence[Mapping[str, object]] | None = None,
        current_cursor: tuple[int, int] | None = None,
    ) -> dict[str, object] | None:
        """Select the next smart galaxy sweep batch using DB sector intel."""
        sw_st = self.get_galaxy_sweep_state_locked(now_ts=now_ts, include_smart=False)
        return plan_smart_galaxy_sweep_for_ctx(
            self._ctx,
            sw_st,
            batch_size=batch_size,
            galaxies=galaxies,
            collect_colonies=collect_colonies,
            now_ts=now_ts,
            sector_intel_override=sector_intel_override,
            explore_first_override=explore_first_override,
            refresh_pct_override=refresh_pct_override,
            scanned_systems_override=scanned_systems_override,
            state_override=state_override,
            raid_targets_override=raid_targets_override,
            current_cursor=current_cursor,
        )

    def store_smart_sweep_summary_locked(
        self,
        plan: Mapping[str, object] | None,
        *,
        selection_mode: str,
        now_ts: float,
        sample_targets: Sequence[Mapping[str, object]] | None = None,
    ) -> dict[str, object]:
        """Persist computed smart sweep summary into `ctx.smart_sweep_summary`."""
        return store_smart_sweep_summary_for_ctx(
            self._ctx, plan, selection_mode=selection_mode, now_ts=now_ts, sample_targets=sample_targets
        )

    def preempt_galaxy_sweep_if_running(
        self,
        reason: str = "Preempted by P0 emergency Escape Flight.",
        *,
        wait_timeout: float = 3.0,
    ) -> bool:
        """Signal an in-progress sweep to abort immediately and wait up to `wait_timeout` seconds."""
        ctx = self._ctx
        if not ctx.sweep_in_progress:
            return False
        ctx.sweep_preempt_reason = reason
        ctx.sweep_preempt_event.set()
        ctx.sweep_wake_event.set()
        if threading.current_thread() is not ctx.sweep_thread:
            ctx.sweep_done_event.wait(timeout=max(0.1, float(wait_timeout)))
        return True

    def abort_auto_galaxy_sweep_if_disabled(self) -> None:
        """Immediately preempt any active AUTOMATIC sweep when `allow_galaxy_scan` becomes False."""
        ctx = self._ctx
        if bool(ctx.strategy_config.allow_galaxy_scan):
            return
        ctx.sweep_wake_event.set()
        ctx.smart_sweep_summary_ts = 0.0
        if ctx.sweep_in_progress and not ctx.sweep_is_manual:
            ctx.sweep_preempt_reason = "Galaxy scans suspended via operator UI."
            ctx.sweep_preempt_event.set()

    def stop_galaxy_sweep_now(self) -> dict[str, object]:
        """Explicitly stop any in-progress galaxy sweep (manual or automatic)."""
        was_running = self.preempt_galaxy_sweep_if_running("Galaxy sweep stopped by operator.", wait_timeout=3.0)
        with self._ctx.state_lock:
            sw_state = self.get_galaxy_sweep_state_locked(include_smart=False)
        if self._on_publish_snapshot is not None:
            self._on_publish_snapshot()
        return sanitize_json_dict(
            {
                "ok": True,
                "command": "galaxy_sweep",
                "stopped": was_running,
                "in_progress": bool(self._ctx.sweep_in_progress),
                "galaxy_sweep": sw_state,
                "message": "Stopped active galaxy sweep." if was_running else "No galaxy sweep is currently running.",
            }
        )

    def start_galaxy_sweep_background(
        self,
        *,
        manual: bool = False,
        pages: int | None = None,
        start_galaxy: int | None = None,
        start_system: int | None = None,
    ) -> dict[str, object]:
        """Launch a background galaxy sweep thread (manual or automatic) without blocking HTTP callers."""
        return launch_background_galaxy_sweep(
            self._ctx,
            manual=manual,
            pages=pages,
            start_galaxy=start_galaxy,
            start_system=start_system,
            get_state_fn=self.get_galaxy_sweep_state_locked,
            run_sweep_fn=self.run_galaxy_sweep,
        )

    def run_galaxy_sweep(
        self,
        *,
        pages: int | None = None,
        galaxy: int | None = None,
        start_system: int | None = None,
        delay_range: tuple[float, float] | None = None,
        dry_run: bool = False,
        priority: str = "normal",
        automatic: bool = False,
        manual: bool | None = None,
    ) -> dict[str, object]:
        """Scan up to `pages` solar systems, releasing `ctx.lock` between steps."""
        return run_galaxy_sweep_workflow(
            self._ctx,
            self._state_cache,
            self._intel_sync,
            self._lifecycle,
            pages=pages,
            galaxy=galaxy,
            start_system=start_system,
            delay_range=delay_range,
            dry_run=dry_run,
            priority=priority,
            automatic=automatic,
            manual=manual,
            get_state_fn=self.get_galaxy_sweep_state_locked,
            save_state_fn=lambda upd, ts: self.save_galaxy_sweep_state_locked(upd, ts=ts),
            sweep_views_fn=self.sweep_views_last_60s,
            has_p0_escape_fn=self.has_urgent_p0_escape_flight_locked,
            on_publish_snapshot=self._on_publish_snapshot,
        )

    def _galaxy_sweep_worker_loop(self) -> None:
        run_galaxy_sweep_worker_loop(
            self._ctx,
            get_state_fn=self.get_galaxy_sweep_state_locked,
            has_p0_escape_fn=self.has_urgent_p0_escape_flight_locked,
            run_sweep_fn=self.run_galaxy_sweep,
        )

    def start_galaxy_sweep_worker(self) -> None:
        """Start the background periodic galaxy sweep worker thread."""
        ctx = self._ctx
        if ctx.sweep_thread is not None and ctx.sweep_thread.is_alive():
            return
        ctx.sweep_stop_event.clear()
        worker = threading.Thread(target=self._galaxy_sweep_worker_loop, name="galaxy-sweep-worker", daemon=True)
        ctx.sweep_thread = worker
        worker.start()

    def stop_galaxy_sweep_worker(self) -> None:
        """Signal and join the background galaxy sweep worker if active."""
        ctx = self._ctx
        ctx.sweep_stop_event.set()
        ctx.sweep_wake_event.set()
        worker = ctx.sweep_thread
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=3.0)
        ctx.sweep_thread = None
