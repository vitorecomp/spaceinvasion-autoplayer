"""Lock-free health, bot-control, dashboard, and Phase 4.2/4.3 snapshot view collaborator."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from spaceinvasion.core.model import GameState, PlannedAction
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import sanitize_finite_json
from spaceinvasion.runtime.bridge._intel_messages import extract_client_current_url
from spaceinvasion.runtime.bridge._views_assembly import (
    build_dashboard_payload_from_state,
    extract_snapshot_sections,
)
from spaceinvasion.runtime.bridge._views_health import (
    ViewHooks,
    build_bot_control_busy_fallback,
    build_bot_control_payload_locked,
    build_health_payload_lock_free,
)
from spaceinvasion.runtime.bridge.lifecycle import BridgeLifecycleService
from spaceinvasion.runtime.bridge.planet_names import PlanetNameService
from spaceinvasion.runtime.bridge.snapshot import PublishedBridgeSnapshot, build_dashboard_delta_response
from spaceinvasion.runtime.bridge.state_cache import StateCacheService
from spaceinvasion.session.auth import AntiBotChallengeError, SpaceInvasionAuthError
from spaceinvasion.session.client import RateLimitError


class DashboardViewService:
    """Serves lock-free `/health`, `/bot_status`, `/dashboard`, and versioned delta snapshots."""

    def __init__(
        self,
        ctx: BridgeContext,
        state_cache: StateCacheService,
        names: PlanetNameService,
        lifecycle: BridgeLifecycleService,
    ) -> None:
        self._ctx = ctx
        self._state_cache = state_cache
        self._names = names
        self._lifecycle = lifecycle
        self._hooks = ViewHooks()

    def bind_hooks(
        self,
        *,
        on_plan_with_summary: Callable[..., tuple[list[PlannedAction], dict[str, object] | None]] | None = None,
        on_serialize_action: Callable[..., dict[str, object]] | None = None,
        on_get_co_building_goals: Callable[[], dict[str, object] | None] | None = None,
        on_get_active_ai_planner: Callable[[], dict[str, object] | None] | None = None,
        on_evaluate_ai_gate: Callable[..., dict[str, object]] | None = None,
        on_get_planner_jobs_status: Callable[..., dict[str, object]] | None = None,
        on_get_transmitter_status: Callable[[], dict[str, object]] | None = None,
        on_get_galaxy_sweep_state: Callable[[], dict[str, object]] | None = None,
        on_is_galaxy_scan_blocking: Callable[[], bool] | None = None,
        on_has_urgent_p0_escape: Callable[[], tuple[bool, str]] | None = None,
        on_get_scanned_systems: Callable[[], dict[str, float]] | None = None,
        on_get_throttle_cooldowns: Callable[[], dict[int, float]] | None = None,
        on_get_fleet_slot_cooldowns: Callable[[], dict[str, float]] | None = None,
        on_get_hourly_production_history: Callable[..., list[dict[str, object]]] | None = None,
        on_get_loot_recycle_hourly: Callable[..., dict[str, object]] | None = None,
        on_build_building_resource_requests: Callable[
            [GameState | None, list[PlannedAction], Mapping[int, dict[str, object]]], list[dict[str, object]]
        ]
        | None = None,
        on_attach_raid_stats: Callable[[GameState], GameState] | None = None,
        on_get_raid_stats_cached: Callable[..., dict[str, object]] | None = None,
        on_record_hourly_production: Callable[[GameState], dict[str, object]] | None = None,
        on_maybe_trigger_ai_drift_replan: Callable[[], bool] | None = None,
    ) -> None:
        """Bind explicit collaborator callbacks for planning, ledgers, sweeps, and telemetry."""
        if on_plan_with_summary is not None:
            self._hooks.on_plan_with_summary = on_plan_with_summary
        if on_serialize_action is not None:
            self._hooks.on_serialize_action = on_serialize_action
        if on_get_co_building_goals is not None:
            self._hooks.on_get_co_building_goals = on_get_co_building_goals
        if on_get_active_ai_planner is not None:
            self._hooks.on_get_active_ai_planner = on_get_active_ai_planner
        if on_evaluate_ai_gate is not None:
            self._hooks.on_evaluate_ai_gate = on_evaluate_ai_gate
        if on_get_planner_jobs_status is not None:
            self._hooks.on_get_planner_jobs_status = on_get_planner_jobs_status
        if on_get_transmitter_status is not None:
            self._hooks.on_get_transmitter_status = on_get_transmitter_status
        if on_get_galaxy_sweep_state is not None:
            self._hooks.on_get_galaxy_sweep_state = on_get_galaxy_sweep_state
        if on_is_galaxy_scan_blocking is not None:
            self._hooks.on_is_galaxy_scan_blocking = on_is_galaxy_scan_blocking
        if on_has_urgent_p0_escape is not None:
            self._hooks.on_has_urgent_p0_escape = on_has_urgent_p0_escape
        if on_get_scanned_systems is not None:
            self._hooks.on_get_scanned_systems = on_get_scanned_systems
        if on_get_throttle_cooldowns is not None:
            self._hooks.on_get_throttle_cooldowns = on_get_throttle_cooldowns
        if on_get_fleet_slot_cooldowns is not None:
            self._hooks.on_get_fleet_slot_cooldowns = on_get_fleet_slot_cooldowns
        if on_get_hourly_production_history is not None:
            self._hooks.on_get_hourly_production_history = on_get_hourly_production_history
        if on_get_loot_recycle_hourly is not None:
            self._hooks.on_get_loot_recycle_hourly = on_get_loot_recycle_hourly
        if on_build_building_resource_requests is not None:
            self._hooks.on_build_building_resource_requests = on_build_building_resource_requests
        if on_attach_raid_stats is not None:
            self._hooks.on_attach_raid_stats = on_attach_raid_stats
        if on_get_raid_stats_cached is not None:
            self._hooks.on_get_raid_stats_cached = on_get_raid_stats_cached
        if on_record_hourly_production is not None:
            self._hooks.on_record_hourly_production = on_record_hourly_production
        if on_maybe_trigger_ai_drift_replan is not None:
            self._hooks.on_maybe_trigger_ai_drift_replan = on_maybe_trigger_ai_drift_replan

    def _is_sweep_blocking(self) -> bool:
        if self._hooks.on_is_galaxy_scan_blocking is not None:
            return bool(self._hooks.on_is_galaxy_scan_blocking())
        return bool(self._ctx.sweep_in_progress and self._ctx.strategy_config.galaxy_scan_block_other_actions)

    def publish_snapshot(
        self,
        *,
        dashboard_payload: Mapping[str, object] | None = None,
    ) -> PublishedBridgeSnapshot:
        """Build and atomically publish an immutable `PublishedBridgeSnapshot` without `ctx.lock`."""
        ctx = self._ctx
        with ctx.state_lock:
            port_open = ctx.is_browser_port_open()
            rate_rem = ctx.page_limiter.rate_limit_remaining()
            cap_blocked = bool(ctx.bot_control.get("captcha_blocked"))
            in_game = (
                port_open
                and not cap_blocked
                and (ctx.last_state is not None or rate_rem > 0.0 or bool(ctx.sweep_in_progress))
            )
            dash_dict: Mapping[str, object] = (
                dashboard_payload
                if isinstance(dashboard_payload, Mapping)
                else build_dashboard_payload_from_state(
                    ctx,
                    self._state_cache,
                    self._names,
                    self._lifecycle,
                    self._hooks,
                    refresh=False,
                    port_open=port_open,
                    in_game=in_game,
                    current_url=ctx.last_url,
                )
            )
            health_dict = build_health_payload_lock_free(ctx, include_busy_if_locked=False)
            bot_ctrl_dict = build_bot_control_payload_locked(ctx, self._hooks)
            raw_state = dash_dict.get("state")
            state_dict: Mapping[str, object] = raw_state if isinstance(raw_state, Mapping) else {}
            sections, prod_rows, loot_rows = extract_snapshot_sections(dash_dict)
            return ctx.snapshot_publisher.publish(
                health=health_dict,
                bot_control=bot_ctrl_dict,
                state=state_dict,
                sections=sections,
                hourly_production_history=prod_rows,
                loot_recycle_hourly=loot_rows,
            )

    def health(self) -> dict[str, object]:
        """Serve `/health` completely lock-free (never acquiring `ctx.lock`)."""
        return build_health_payload_lock_free(self._ctx, include_busy_if_locked=True)

    def get_bot_control(self) -> dict[str, object]:
        """Serve `/bot_status` lock-free when busy and under `ctx.state_lock` when idle."""
        ctx = self._ctx
        if ctx.lock.locked():
            return build_bot_control_busy_fallback(ctx)
        with ctx.state_lock:
            return build_bot_control_payload_locked(ctx, self._hooks)

    def _fetch_live_dashboard_snapshot_locked(self) -> tuple[bool, bool, object, bool]:
        ctx = self._ctx
        port_open = ctx.is_browser_port_open()
        rate_rem = ctx.page_limiter.rate_limit_remaining()
        cap_blocked = bool(ctx.bot_control.get("captcha_blocked"))
        sweep_blocking = self._is_sweep_blocking()
        in_game = (
            port_open
            and not cap_blocked
            and (ctx.last_state is not None or rate_rem > 0.0 or bool(ctx.sweep_in_progress))
        )
        current_url: object = ctx.last_url
        if port_open and rate_rem <= 0.0 and not cap_blocked and not sweep_blocking:
            try:
                with ctx.client_factory(ctx.debug_port) as client:
                    snap = self._state_cache.read_snapshot(
                        client,
                        include_buildings=True,
                        include_research=True,
                        include_shipyard=True,
                        return_home=False,
                        priority="normal",
                    )
                    obs_active = bool(client.last_snapshot_observed_active_planet)
                    self._state_cache.record_state(
                        snap,
                        fetched_buildings=True,
                        fetched_research=True,
                        fetched_shipyard=True,
                        fetched_home=True,
                        observed_active_planet=obs_active,
                    )
                    current_url = extract_client_current_url(client)
                    ctx.last_url = str(current_url) if current_url else None
                    in_game = True
            except RateLimitError as exc:
                ctx.record_server_rate_limit(exc)
                in_game = port_open
            except AntiBotChallengeError as exc:
                self._lifecycle.record_antibot_pause(exc)
                in_game = False
            except SpaceInvasionAuthError:
                in_game = False
        return port_open, in_game, current_url, sweep_blocking

    def dashboard(self, *, refresh: bool = False) -> dict[str, object]:
        """Return full legacy dashboard payload (lock-free on `refresh=False`, browser fetch on `refresh=True`)."""
        ctx = self._ctx
        if not refresh:
            if ctx.lock.locked():
                cached_dash = ctx.last_dashboard_payload
                if isinstance(cached_dash, Mapping):
                    fallback: dict[str, object] = dict(cached_dash)
                    fallback["bot_control"] = sanitize_finite_json(dict(ctx.bot_control))
                    fallback["busy"] = True
                    return fallback
                with ctx.state_lock:
                    port_open = ctx.is_browser_port_open()
                    rate_rem = ctx.page_limiter.rate_limit_remaining()
                    cap_blocked = bool(ctx.bot_control.get("captcha_blocked"))
                    in_game = (
                        port_open
                        and not cap_blocked
                        and (ctx.last_state is not None or rate_rem > 0.0 or bool(ctx.sweep_in_progress))
                    )
                    cold_res = build_dashboard_payload_from_state(
                        ctx,
                        self._state_cache,
                        self._names,
                        self._lifecycle,
                        self._hooks,
                        refresh=False,
                        port_open=port_open,
                        in_game=in_game,
                        current_url=ctx.last_url,
                    )
                cold_res["busy"] = True
                return cold_res

            with ctx.state_lock:
                port_open = ctx.is_browser_port_open()
                rate_rem = ctx.page_limiter.rate_limit_remaining()
                cap_blocked = bool(ctx.bot_control.get("captcha_blocked"))
                in_game = (
                    port_open
                    and not cap_blocked
                    and (ctx.last_state is not None or rate_rem > 0.0 or bool(ctx.sweep_in_progress))
                )
                result = build_dashboard_payload_from_state(
                    ctx,
                    self._state_cache,
                    self._names,
                    self._lifecycle,
                    self._hooks,
                    refresh=False,
                    port_open=port_open,
                    in_game=in_game,
                    current_url=ctx.last_url,
                )
            self.publish_snapshot(dashboard_payload=result)
            if self._hooks.on_maybe_trigger_ai_drift_replan is not None:
                self._hooks.on_maybe_trigger_ai_drift_replan()
            return result

        with ctx.lock:
            port_open, in_game, current_url, sweep_blocking = self._fetch_live_dashboard_snapshot_locked()
            with ctx.state_lock:
                result = build_dashboard_payload_from_state(
                    ctx,
                    self._state_cache,
                    self._names,
                    self._lifecycle,
                    self._hooks,
                    refresh=True,
                    port_open=port_open,
                    in_game=in_game,
                    current_url=current_url,
                )
            self.publish_snapshot(dashboard_payload=result)
            if sweep_blocking:
                result = {
                    **result,
                    "sweep_deferred_refresh": True,
                    "sweep_message": (
                        "Galaxy scan in progress — wait for scan to finish, or Pause / disable Galaxy Scan first."
                    ),
                }
        if self._hooks.on_maybe_trigger_ai_drift_replan is not None:
            self._hooks.on_maybe_trigger_ai_drift_replan()
        return result

    get_dashboard = dashboard

    def dashboard_delta(
        self,
        *,
        client_section_versions: Mapping[str, str] | None = None,
        since_minute_bucket: int | None = None,
        since_loot_hour: int | None = None,
    ) -> dict[str, object]:
        """Serve Phase 4.3 versioned dashboard delta lock-free from `ctx.snapshot_publisher`."""
        snap = self._ctx.snapshot_publisher.current()
        if snap is None:
            snap = self.publish_snapshot()
        return build_dashboard_delta_response(
            snap,
            client_section_versions=client_section_versions,
            since_minute_bucket=since_minute_bucket,
            since_loot_hour=since_loot_hour,
        )
