"""Planning, session verification, fleetsave plan, and tick_once API handlers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from spaceinvasion.combat.cargo import plan_escape_flight
from spaceinvasion.core.config import StrategyConfig, normalize_planner_mode
from spaceinvasion.core.model import GameState
from spaceinvasion.runtime.api.handlers._browser_proto import BrowserBridgeFacade
from spaceinvasion.runtime.api.handlers._browser_session import BrowserCommandSession, run_browser_command
from spaceinvasion.runtime.api.handlers._planning_views import (
    ActionGraphParams,
    PlanetViewParams,
    handle_action_graph,
    handle_buildings_eta,
    handle_combat_overview,
    parse_planner_mode_override,
)
from spaceinvasion.runtime.api.registry import CommandRegistry
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._context_init import BridgeConstants
from spaceinvasion.runtime.bridge._helpers import (
    GalaxyScanInProgressError,
    bounded_api_str,
    parse_command_int,
    sanitize_json_dict,
)
from spaceinvasion.session.client import RateLimitError


@dataclass(frozen=True)
class EnsureSessionParams:
    """Parameters for `ensure_session`."""


@dataclass(frozen=True)
class PlanParams:
    """Parameters for `plan`."""

    planner_mode: object = None
    refresh: object = None
    dry_run: bool = False


@dataclass(frozen=True)
class FleetsavePlanParams:
    """Parameters for `fleetsave_plan`."""

    planet_id: object = None
    fuel_reserve_spice: object = None
    target_coords: object = None
    refresh: bool = False


@dataclass(frozen=True)
class TickOnceParams:
    """Parameters for `tick_once`."""

    dry_run: bool = False


def _build_plan_response(
    bridge: BrowserBridgeFacade,
    state: GameState,
    *,
    override_cfg: StrategyConfig | None,
    serialize_with_state: bool,
) -> dict[str, object]:
    effective_cfg = override_cfg or bridge.ctx.strategy_config
    effective_mode = normalize_planner_mode(effective_cfg.planner_mode)
    disp_state = bridge.names.apply_custom_planet_names_to_state(state) or state
    actions, opt_summary = bridge.plan_with_summary(disp_state, config=override_cfg)
    gate = bridge.evaluate_ai_planner_gate_locked(disp_state, cfg=effective_cfg)
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "plan",
            "planner_mode": effective_mode,
            "effective_planner_mode": gate["effective_planner_mode"],
            "fallback_reason": gate["fallback_reason"],
            "live_ai_unlocked": gate["live_ai_unlocked"],
            "colonies_owned": gate["colonies_owned"],
            "max_planets": gate["max_planets"],
            "actions": [bridge.serialize_action(a, disp_state if serialize_with_state else None) for a in actions],
            "optimizer_summary": opt_summary,
        }
    )


def _cmd_ensure_session(bridge: BrowserBridgeFacade, _params: EnsureSessionParams) -> dict[str, object]:
    if bridge.is_galaxy_scan_blocking_locked():
        raise GalaxyScanInProgressError(bridge.galaxy_scan_busy_error_message())
    bridge.ctx.raise_if_rate_limited()
    try:
        bridge.ctx.ensure_session_fn()
    except RateLimitError as exc:
        bridge.ctx.record_server_rate_limit(exc)
        raise
    return sanitize_json_dict({"ok": True, "command": "ensure_session", "message": "Session verified."})


def _cmd_plan(bridge: BrowserBridgeFacade, params: PlanParams) -> dict[str, object]:
    req_mode = parse_planner_mode_override(params.planner_mode)
    override_cfg = replace(bridge.ctx.strategy_config, planner_mode=req_mode) if req_mode is not None else None
    if params.refresh is False and bridge.ctx.last_state is not None:
        fast_state = bridge.state_cache.refresh_cached_economy(bridge.ctx.last_state)
        return _build_plan_response(bridge, fast_state, override_cfg=override_cfg, serialize_with_state=False)

    def _run_live(session: BrowserCommandSession) -> dict[str, object]:
        state = session.cached_or_fetch(
            include_buildings=True, include_research=False, include_shipyard=False, priority="normal"
        )
        if (
            bridge.sync_spio_if_unread_ex(
                None,
                state,
                priority="normal",
                get_client=session.get_client,
                ignore_auth_error=not session.force_refresh,
            )
            > 0
        ):
            state = bridge.state_cache.record_state(
                state,
                fetched_buildings=bridge.ctx.last_buildings_fetched,
                fetched_home=False,
                observed_active_planet=False,
                update_snapshot_ts=False,
            )
        return _build_plan_response(bridge, state, override_cfg=override_cfg, serialize_with_state=True)

    return run_browser_command(bridge, "plan", {"refresh": bool(params.refresh), "dry_run": params.dry_run}, _run_live)


def _cmd_fleetsave_plan(bridge: BrowserBridgeFacade, params: FleetsavePlanParams) -> dict[str, object]:
    pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
    reserve_ov = parse_command_int(
        "fuel_reserve_spice", params.fuel_reserve_spice, allow_none=True, min_val=0, max_val=10**200
    )

    def _run_live(session: BrowserCommandSession) -> dict[str, object]:
        state = session.cached_or_fetch(
            include_buildings=False, include_research=False, include_shipyard=False, include_home=False
        )
        planet = bridge.ctx.resolve_planet(state, pid)
        if params.target_coords is None:
            target_coords = ""
        elif not isinstance(params.target_coords, str):
            raise ApiRequestError("'fleetsave_plan' field 'target_coords' must be a string.")
        else:
            target_coords = params.target_coords.strip()
        if not target_coords:
            target_coords = next((p.coords for p in state.planets if p.planet_id != planet.planet_id), planet.coords)
        fuel_reserve = (
            reserve_ov if reserve_ov is not None else bridge.ctx.strategy_config.fleet_save_fuel_reserve_spice
        )
        escape = plan_escape_flight(planet, target_coords=target_coords, fuel_reserve_spice=fuel_reserve)
        return sanitize_json_dict({"ok": True, "command": "fleetsave_plan", "escape_plan": asdict(escape)})

    return run_browser_command(bridge, "fleetsave_plan", {"refresh": params.refresh}, _run_live)


def _cmd_tick_once(bridge: BrowserBridgeFacade, params: TickOnceParams) -> dict[str, object]:
    def _run_live(session: BrowserCommandSession) -> dict[str, object]:
        ctx = bridge.ctx
        client = session.get_client()
        inc_r = (
            ctx.last_research_sync_at <= 0.0
            or (session.now_mono - ctx.last_research_sync_at) >= BridgeConstants.RESEARCH_SYNC_TTL_S
        )
        state = bridge.state_cache.read_snapshot(
            client,
            include_buildings=True,
            include_research=inc_r,
            include_shipyard=False,
            return_home=False,
            priority="normal",
        )
        state = bridge.state_cache.record_state(
            state,
            fetched_buildings=True,
            fetched_research=inc_r,
            fetched_home=True,
            observed_active_planet=bool(client.last_snapshot_observed_active_planet),
        )
        if bridge.sync_spio_if_unread(client, state, priority="normal") > 0:
            state = bridge.state_cache.record_state(
                state,
                fetched_buildings=ctx.last_buildings_fetched,
                fetched_research=inc_r,
                fetched_home=False,
                observed_active_planet=False,
                update_snapshot_ts=False,
            )
        actions = bridge.plan_with_context(state)
        ready = next((a for a in actions if a.executable_now), None)
        if ready is None:
            msg = (
                f"Waiting: {bounded_api_str(actions[0].target_name, default='')} "
                f"({bounded_api_str(actions[0].reason, default='')})"
                if actions
                else "No pending actions."
            )
            return sanitize_json_dict(
                {
                    "ok": True,
                    "command": "tick_once",
                    "executed": False,
                    "dry_run": params.dry_run,
                    "message": msg,
                    "action": bridge.serialize_action(actions[0], state) if actions else None,
                    "next_action": bridge.serialize_action(actions[1], state) if len(actions) > 1 else None,
                }
            )
        return bridge.execute_and_summarize_browser("tick_once", client, state, ready, dry_run=params.dry_run)

    return run_browser_command(bridge, "tick_once", {"dry_run": params.dry_run}, _run_live)


def register_planning_handlers(registry: CommandRegistry[BrowserBridgeFacade]) -> None:
    """Register all 7 session/planning/read-view API commands on `registry`."""
    registry.command(
        "ensure_session",
        needs_browser=True,
        mutates=False,
        summary="Verify Chrome debug port and logged-in SpaceInvasion game session.",
        params=EnsureSessionParams,
    )(_cmd_ensure_session)
    registry.command(
        "plan",
        needs_browser=True,
        mutates=False,
        summary="Compute prioritized next actions for the active planner mode.",
        params=PlanParams,
    )(_cmd_plan)
    registry.command(
        "action_graph",
        needs_browser=False,
        mutates=False,
        summary="Build the Kahn dependency DAG for current or simulated strategy settings.",
        params=ActionGraphParams,
    )(handle_action_graph)
    registry.command(
        "buildings_eta",
        needs_browser=False,
        mutates=False,
        summary="Compute building upgrade ETAs and Config Optimizer goals for a planet.",
        params=PlanetViewParams,
    )(handle_buildings_eta)
    registry.command(
        "combat_overview",
        needs_browser=False,
        mutates=False,
        summary="Summarize planet fleet/defense combat strength and raid targets.",
        params=PlanetViewParams,
    )(handle_combat_overview)
    registry.command(
        "fleetsave_plan",
        needs_browser=True,
        mutates=False,
        summary="Compute an Escape Flight packing plan for a planet.",
        params=FleetsavePlanParams,
    )(_cmd_fleetsave_plan)
    registry.command(
        "tick_once",
        needs_browser=True,
        mutates=True,
        summary="Refresh live state and execute the highest-priority ready action once.",
        params=TickOnceParams,
    )(_cmd_tick_once)
