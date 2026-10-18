"""Shared lazy browser session, snapshot cache, and error boundary for browser-capable handlers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import contextlib
from dataclasses import replace
import logging
import time

from spaceinvasion.core.logging_utils import append_crash_debug_log
from spaceinvasion.core.state import (
    FleetAlert,
    GameState,
    QueueJob,
    TutorialStatus,
    parse_home_metadata,
    parse_tutorial_from_home,
)
from spaceinvasion.planner.strategy import PlannedAction
from spaceinvasion.runtime.api.handlers._browser_proto import BrowserBridgeFacade
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._helpers import BRIDGE_RUNTIME_ERRORS, GalaxyScanInProgressError
from spaceinvasion.runtime.bridge._helpers_sig import call_with_optional_priority
from spaceinvasion.session.auth import AntiBotChallengeError, SpaceInvasionAuthError
from spaceinvasion.session.client import GameBrowserClient, RateLimitError, is_server_rate_limit_html

_LOGGER = logging.getLogger(__name__)
_READONLY_LIVE_CMDS = frozenset({"plan", "action_graph", "buildings_eta", "combat_overview", "fleetsave_plan"})
_CMD_CRASH_ERRORS: tuple[type[Exception], ...] = (SpaceInvasionAuthError, *BRIDGE_RUNTIME_ERRORS)


class BrowserCommandSession:
    """Manages lazy Chrome client attach and cached/live state resolution inside a browser-locked command."""

    def __init__(
        self,
        bridge: BrowserBridgeFacade,
        *,
        cmd: str,
        dry_run: bool,
        force_refresh: bool,
        now_mono: float,
        stack: contextlib.ExitStack,
    ) -> None:
        self._bridge = bridge
        self.cmd = cmd
        self.dry_run = dry_run
        self.force_refresh = force_refresh
        self.now_mono = now_mono
        self._stack = stack
        self._client: GameBrowserClient | None = None

    def get_client(self) -> GameBrowserClient:
        """Lazily open and return the Selenium `GameBrowserClient`, enforcing CAPTCHA & sweep guards."""
        ctx = self._bridge.ctx
        if ctx.bot_control.get("captcha_blocked"):
            raise AntiBotChallengeError(
                str(ctx.bot_control.get("captcha_message") or "Paused for CAPTCHA — confirm solved in UI to resume.")
            )
        if self._bridge.is_galaxy_scan_blocking_locked() and not (self.cmd == "escape_flight" and not self.dry_run):
            raise GalaxyScanInProgressError(self._bridge.galaxy_scan_busy_error_message())
        if self._client is None:
            self._client = self._stack.enter_context(ctx.client_factory(ctx.debug_port))
        if self._client.driver is not None:
            cur_u = str(self._client.driver.current_url or "")
            if cur_u:
                ctx.last_url = cur_u
        if not self.dry_run and self.cmd not in _READONLY_LIVE_CMDS:
            ctx.last_live_command_mono = time.monotonic()
        return self._client

    def exec_client(self, planned: PlannedAction) -> GameBrowserClient | None:
        """Return a live client only when `planned` can and is permitted to execute now."""
        if self.dry_run or not planned.executable_now:
            return None
        live_exec = self._bridge.ctx.live_executor
        if planned.kind == "ATTACK" and not live_exec.allow_raids:
            return None
        if planned.kind == "ESCAPE_FLIGHT" and not live_exec.allow_fleetsave:
            return None
        if planned.kind in ("COLONIZE", "ABANDON_COLONY") and not live_exec.allow_colonize:
            return None
        if planned.kind in ("TRANSPORT", "STATION") and not live_exec.allow_transport:
            return None
        return self.get_client()

    def cached_or_fetch(
        self,
        *,
        include_buildings: bool,
        include_research: bool,
        include_shipyard: bool,
        include_home: bool = True,
        priority: str = "interactive",
    ) -> GameState:
        """Return a fresh cached `GameState` or read a live browser snapshot."""
        ctx = self._bridge.ctx
        sc = self._bridge.state_cache
        if ctx.bot_control.get("captcha_blocked") and ctx.last_state is not None:
            return sc.refresh_cached_economy(ctx.last_state)
        if self._bridge.is_galaxy_scan_blocking_locked() and not self.force_refresh and ctx.last_state is not None:
            return sc.refresh_cached_economy(ctx.last_state)
        last_active = ctx.last_state.active_planet() if ctx.last_state is not None else None
        if (
            not self.force_refresh
            and ctx.last_state is not None
            and (self.now_mono - ctx.last_snapshot_at) <= 15.0
            and (not include_buildings or ctx.last_buildings_fetched)
            and (not include_research or bool(ctx.last_state.research_offers))
            and (not include_shipyard or bool(last_active is not None and last_active.ship_offers))
            and (not include_home or sc.is_home_fresh(self.now_mono))
        ):
            return sc.refresh_cached_economy(ctx.last_state)
        live_client = self.get_client()
        snap = sc.read_snapshot(
            live_client,
            include_buildings=include_buildings,
            include_research=include_research,
            include_shipyard=include_shipyard,
            include_home=include_home,
            return_home=False,
            priority=priority,
        )
        obs_active = bool(live_client.last_snapshot_observed_active_planet)
        return sc.record_state(
            snap,
            fetched_buildings=include_buildings,
            fetched_research=include_research,
            fetched_shipyard=include_shipyard,
            fetched_home=include_home,
            observed_active_planet=obs_active,
        )

    def resolve_fleet_command_state(self, target_pid: int | None, *, is_spy: bool) -> GameState:
        """Resolve state cheaply for fleet commands, falling back to lightweight overview read on cold start."""
        ctx = self._bridge.ctx
        sc = self._bridge.state_cache
        if self._bridge.is_galaxy_scan_blocking_locked() and not self.force_refresh and ctx.last_state is not None:
            return sc.refresh_cached_economy(ctx.last_state)
        if self.dry_run or self.force_refresh:
            return self.cached_or_fetch(
                include_buildings=False,
                include_research=False,
                include_shipyard=False,
                include_home=False,
                priority="interactive",
            )
        if ctx.last_state is not None and ctx.last_snapshot_at >= 0.0:
            is_warm = (self.now_mono - ctx.last_snapshot_at) <= 15.0
            is_cross = target_pid is not None and target_pid != ctx.last_state.active_planet_id
            unconfirmed_multi = not ctx.active_planet_confirmed and len(ctx.last_state.planets) > 1
            if is_warm or is_cross or unconfirmed_multi or (is_spy and target_pid is None):
                return sc.refresh_cached_economy(ctx.last_state)
            return self.cached_or_fetch(
                include_buildings=False,
                include_research=False,
                include_shipyard=False,
                include_home=False,
                priority="interactive",
            )
        return self._read_cold_fleet_overview()

    def _read_cold_fleet_overview(self) -> GameState:
        sc = self._bridge.state_cache
        live_client = self.get_client()
        call_with_optional_priority(live_client.ensure_in_game, priority="interactive")
        ov_obj: object = None
        try:
            ov_obj = call_with_optional_priority(
                live_client.read_planets_overview, refresh=False, priority="interactive"
            )
        except TypeError:
            ov_obj = None
        if not isinstance(ov_obj, GameState):
            return self.cached_or_fetch(
                include_buildings=False,
                include_research=False,
                include_shipyard=False,
                include_home=False,
                priority="interactive",
            )
        unread, account_points, account_rank = _read_menu_overlay(live_client, ov_obj)
        active_pid, obs_active, fetched_home, tutorial, speedup, newbie, queues, alerts = _inspect_live_frame_meta(
            live_client, ov_obj
        )
        ov = replace(
            ov_obj,
            active_planet_id=active_pid or ov_obj.active_planet_id,
            unread_messages=unread,
            tutorial=tutorial,
            active_queues=queues,
            fleet_alerts=alerts,
            speedup_factor=speedup,
            newbie_protection=newbie,
            account_points=account_points,
            account_rank=account_rank,
        )
        return sc.record_state(ov, fetched_home=fetched_home, observed_active_planet=obs_active)


def _read_menu_overlay(client: GameBrowserClient, ov: GameState) -> tuple[dict[str, int], int | None, int | None]:
    unread = dict(ov.unread_messages)
    account_points = ov.account_points
    account_rank = ov.account_rank
    try:
        menu_res = client.read_menu_status()
        if isinstance(menu_res, tuple) and len(menu_res) == 3 and isinstance(menu_res[0], dict):
            unread = dict(menu_res[0])
            account_points = menu_res[1]
            account_rank = menu_res[2]
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.debug("Menu status overlay skipped during cold fleet read", exc_info=True)
    return unread, account_points, account_rank


def _inspect_live_frame_meta(
    client: GameBrowserClient,
    ov: GameState,
) -> tuple[
    int | None,
    bool,
    bool,
    TutorialStatus | None,
    float | None,
    bool,
    tuple[QueueJob, ...],
    tuple[FleetAlert, ...],
]:
    planet_ids = {p.planet_id for p in ov.planets}
    active_pid: int | None = None
    obs_active = False
    fetched_home = False
    tutorial = ov.tutorial
    speedup = ov.speedup_factor
    newbie = ov.newbie_protection
    queues = ov.active_queues
    alerts = ov.fleet_alerts
    drv = client.driver
    if drv is None:
        return active_pid, obs_active, fetched_home, tutorial, speedup, newbie, queues, alerts
    try:
        with client._driver_lock:
            client._switch_frame("F_Hauptframe")
            cur_url = str(drv.execute_script("return location.href;") or "")
            cur_html = str(drv.page_source or "")
            try:
                drv.switch_to.default_content()
            except BRIDGE_RUNTIME_ERRORS:
                _LOGGER.debug("Ignoring default_content switch failure", exc_info=True)
        if "action=internalHome" in cur_url and cur_html:
            low_h = cur_html.lower()
            if (
                not is_server_rate_limit_html(cur_html)
                and "bitmeup-login" not in low_h
                and "sip-tab-universe" not in low_h
            ):
                home_pid, h_spd, h_newbie, h_q, h_al = parse_home_metadata(cur_html)
                if home_pid is not None and home_pid in planet_ids:
                    active_pid = home_pid
                    obs_active = True
                tutorial = parse_tutorial_from_home(cur_html)
                speedup = h_spd
                newbie = h_newbie
                queues = h_q
                alerts = h_al
                fetched_home = True
        elif cur_html:
            sel_pid = GameBrowserClient._extract_selected_planet_id(cur_html, cur_url)
            if sel_pid is not None and sel_pid in planet_ids:
                active_pid = sel_pid
                obs_active = True
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.debug("Live frame inspection skipped during cold fleet read", exc_info=True)
    return active_pid, obs_active, fetched_home, tutorial, speedup, newbie, queues, alerts


def run_browser_command(
    bridge: BrowserBridgeFacade,
    cmd: str,
    payload: Mapping[str, object],
    fn: Callable[[BrowserCommandSession], dict[str, object]],
) -> dict[str, object]:
    """Run a browser-locked command body with standardized rate-limit, anti-bot, and crash handling."""
    ctx = bridge.ctx
    ctx.raise_if_rate_limited()
    if ctx.last_state is None:
        ctx.active_planet_confirmed = False
        ctx.last_home_fetched = False
        ctx.last_home_at = None
    dry_run = bool(payload.get("dry_run", False))
    force_refresh = bool(payload.get("refresh", False))
    now_mono = time.monotonic()
    try:
        with contextlib.ExitStack() as stack:
            session = BrowserCommandSession(
                bridge,
                cmd=cmd,
                dry_run=dry_run,
                force_refresh=force_refresh,
                now_mono=now_mono,
                stack=stack,
            )
            return fn(session)
    except RateLimitError as exc:
        ctx.record_server_rate_limit(exc)
        if ctx.log_dir is not None:
            append_crash_debug_log(
                component="api_server.handle_command",
                event=f"rate_limit:{cmd}",
                error=exc,
                details={"command": cmd, "payload": dict(payload)},
            )
        raise
    except AntiBotChallengeError as exc:
        bridge.lifecycle.record_antibot_pause(exc)
        bridge.publish_snapshot()
        if ctx.log_dir is not None:
            append_crash_debug_log(
                component="api_server.handle_command",
                event=f"antibot_challenge:{cmd}",
                error=exc,
                details={"command": cmd, "payload": dict(payload)},
            )
        raise
    except _CMD_CRASH_ERRORS as exc:
        if ctx.log_dir is not None and not isinstance(exc, (ApiRequestError, GalaxyScanInProgressError)):
            append_crash_debug_log(
                component="api_server.handle_command",
                event=f"command_error:{cmd}",
                error=exc,
                details={"command": cmd, "payload": dict(payload)},
            )
        raise
