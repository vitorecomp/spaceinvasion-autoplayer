"""Spy, recycle, colonize, and core fleet handler registration."""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
import time

from backend.core import catalog as game_data
from backend.core.coords import normalize_coords
from backend.core.model import (
    GameState,
    PlanetState,
    PlannedAction,
    count_in_flight_spy_fleets_for_planet,
    normalize_planet_type,
)
from backend.runtime.api.handlers._browser_proto import BrowserBridgeFacade
from backend.runtime.api.handlers._browser_session import BrowserCommandSession, run_browser_command
from backend.runtime.api.handlers._exec_fleet_helpers import (
    AbandonColonyParams,
    aborted_action_response,
    cmd_abandon_colony,
    lookup_target_slot_and_row,
)
from backend.runtime.api.registry import CommandRegistry
from backend.runtime.api.validation import ApiRequestError
from backend.runtime.bridge._helpers import parse_command_int, safe_api_float, safe_api_int


@dataclass(frozen=True)
class SpyParams:
    """Parameters for `spy`."""

    target_coords: object = None
    quantity: object = 1
    planet_id: object = None
    dry_run: bool = False
    refresh: bool = False


@dataclass(frozen=True)
class RecycleParams:
    """Parameters for `recycle`."""

    target_coords: object = None
    quantity: object = None
    planet_id: object = None
    dry_run: bool = False
    refresh: bool = False


@dataclass(frozen=True)
class ColonizeParams:
    """Parameters for `colonize`."""

    target_coords: object = None
    planet_id: object = None
    dry_run: bool = False
    refresh: bool = False


def _select_spy_origin_planet(bridge: BrowserBridgeFacade, state: GameState, pid: int | None) -> PlanetState:
    if pid is not None:
        return bridge.ctx.resolve_planet(state, pid)
    desig_pid = max(0, safe_api_int(bridge.ctx.strategy_config.spy_dispatch_planet_id, 0))
    if desig_pid > 0:
        desig_p = next(
            (p for p in state.planets if p.planet_id == desig_pid and normalize_planet_type(p) != "raid"), None
        )
        if desig_p is not None:
            return desig_p
    probe_p = next(
        (p for p in state.planets if normalize_planet_type(p) != "raid" and safe_api_int(p.ships.get("s10", 0)) > 0),
        None,
    )
    return probe_p or bridge.ctx.resolve_planet(state, None)


def _validate_live_spy_target(
    bridge: BrowserBridgeFacade,
    state: GameState,
    planet: PlanetState,
    norm_target: str,
    planned: PlannedAction,
) -> tuple[dict[str, object] | None, PlannedAction]:
    ctx = bridge.ctx
    if ctx.strategy_config.spy_reserved_slots_only:
        max_spy_sl = bridge.max_fleet_slots_for_planet_locked(planet)
        spy_cap = min(max_spy_sl, max(0, safe_api_int(ctx.strategy_config.reserved_spy_slots, 1)))
        spy_used = count_in_flight_spy_fleets_for_planet(state, planet)
        if spy_used >= spy_cap:
            return (
                aborted_action_response(
                    bridge,
                    cmd="spy",
                    dry_run=False,
                    validated=True,
                    message=f"Aborted: Spy restricted to reserved slot(s) ({spy_used}/{spy_cap} spy slots in use on [{planet.coords}]).",
                    planned=replace(planned, executable_now=False),
                    state=state,
                ),
                planned,
            )
    now_s = time.time()
    spy_trow, eff_slot = lookup_target_slot_and_row(bridge, norm_target)
    blocked_until = safe_api_float((spy_trow or {}).get("blocked_until_ts", 0.0))
    if norm_target in ctx.target_blocks:
        blocked_until = max(blocked_until, float(ctx.target_blocks[norm_target][0]))
    if blocked_until > now_s:
        rem_cd = max(0.0, blocked_until - now_s)
        return (
            aborted_action_response(
                bridge,
                cmd="spy",
                dry_run=False,
                validated=True,
                message=f"Aborted: target {norm_target} is on protection cooldown ({rem_cd:.0f}s remaining).",
                planned=replace(planned, executable_now=False),
                state=state,
            ),
            planned,
        )
    if eff_slot is not None:
        spy_color = game_data.safe_key(eff_slot.get("status_color") or "").strip().lower()
        spy_spacia = bool(eff_slot.get("is_spacia", False))
        if spy_color == "free" and not spy_spacia:
            planned = replace(planned, target_name=f"{norm_target} (Free slot)")
        if spy_color in ("blue", "green", "white", "dark_red") and not spy_spacia:
            return (
                aborted_action_response(
                    bridge,
                    cmd="spy",
                    dry_run=False,
                    validated=True,
                    message=f"Aborted: target {norm_target} is protected ({spy_color}).",
                    planned=replace(planned, executable_now=False),
                    state=state,
                ),
                planned,
            )
    return None, planned


def _cmd_spy(bridge: BrowserBridgeFacade, params: SpyParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        if not isinstance(params.target_coords, str):
            raise ApiRequestError("'spy' command requires 'target_coords' formatted as GG:SSS:PP.")
        target_coords = params.target_coords.strip()
        if not re.fullmatch(r"\d{1,2}:\d{1,3}:\d{1,2}", target_coords, re.ASCII):
            raise ApiRequestError("'spy' command requires 'target_coords' formatted as GG:SSS:PP.")
        norm_target = normalize_coords(target_coords)
        qty = parse_command_int("quantity", params.quantity, min_val=1, max_val=1_000_000_000)
        assert qty is not None
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        state = session.resolve_fleet_command_state(pid, is_spy=True)
        planet = _select_spy_origin_planet(bridge, state, pid)
        if any(normalize_coords(p.coords) == norm_target for p in state.planets):
            planned_own = PlannedAction(
                priority="P2",
                kind="SPY",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=10,
                target_level=qty,
                target_name=norm_target,
                reason="Own colony safety check",
                executable_now=False,
            )
            return aborted_action_response(
                bridge,
                cmd="spy",
                dry_run=params.dry_run,
                message=f"Aborted: {norm_target} is one of your own planets.",
                planned=planned_own,
                state=state,
            )
        planned = PlannedAction(
            priority="P2",
            kind="SPY",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=10,
            target_level=qty,
            target_name=norm_target,
            reason="API spy command",
            executable_now=True,
        )
        if not params.dry_run:
            abort_resp, planned = _validate_live_spy_target(bridge, state, planet, norm_target, planned)
            if abort_resp is not None:
                return abort_resp
        return bridge.execute_and_summarize_browser(
            "spy", session.exec_client(planned), state, planned, dry_run=params.dry_run
        )

    return run_browser_command(bridge, "spy", {"dry_run": params.dry_run, "refresh": params.refresh}, _run)


def _cmd_recycle(bridge: BrowserBridgeFacade, params: RecycleParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        if not isinstance(params.target_coords, str):
            raise ApiRequestError("'recycle' command requires 'target_coords' formatted as GG:SSS:PP.")
        m_rec = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", params.target_coords.strip(), re.ASCII)
        if not m_rec:
            raise ApiRequestError("'recycle' command requires 'target_coords' formatted as GG:SSS:PP.")
        norm_target = normalize_coords(m_rec.group(1))
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        state = session.resolve_fleet_command_state(pid, is_spy=True)
        if pid is None:
            rec_p = next((p for p in state.planets if safe_api_int(p.ships.get("s11", 0)) > 0), None)
            planet = rec_p or bridge.ctx.resolve_planet(state, None)
        else:
            planet = bridge.ctx.resolve_planet(state, pid)
        default_qty = max(1, safe_api_int(planet.ships.get("s11", 0), 1))
        qty = parse_command_int(
            "quantity", default_qty if params.quantity is None else params.quantity, min_val=1, max_val=1_000_000_000
        )
        assert qty is not None
        planned = PlannedAction(
            priority="P2",
            kind="RECYCLE",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=11,
            target_level=qty,
            target_name=f"Rubble -> {norm_target}",
            reason="API recycle command",
            executable_now=True,
        )
        return bridge.execute_and_summarize_browser(
            "recycle", session.exec_client(planned), state, planned, dry_run=params.dry_run
        )

    return run_browser_command(bridge, "recycle", {"dry_run": params.dry_run, "refresh": params.refresh}, _run)


def _cmd_colonize(bridge: BrowserBridgeFacade, params: ColonizeParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        if not isinstance(params.target_coords, str):
            raise ApiRequestError("'colonize' command requires 'target_coords' formatted as GG:SSS:PP.")
        m_col = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", params.target_coords.strip(), re.ASCII)
        if not m_col:
            raise ApiRequestError("'colonize' command requires 'target_coords' formatted as GG:SSS:PP.")
        norm_target = normalize_coords(m_col.group(1))
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        state = session.resolve_fleet_command_state(pid, is_spy=True)
        if pid is None:
            col_p = next((p for p in state.planets if safe_api_int(p.ships.get("s8", 0)) > 0), None)
            planet = col_p or bridge.ctx.resolve_planet(state, None)
        else:
            planet = bridge.ctx.resolve_planet(state, pid)
        planned = PlannedAction(
            priority="P2",
            kind="COLONIZE",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=8,
            target_level=1,
            target_name=f"Colonize -> {norm_target}",
            reason="API colonize command",
            executable_now=True,
        )
        if not params.dry_run and not bridge.ctx.live_executor.allow_colonize:
            return bridge.execute_and_summarize_browser(
                "colonize", session.exec_client(planned), state, planned, dry_run=params.dry_run
            )
        if any(normalize_coords(p.coords) == norm_target for p in state.planets):
            return aborted_action_response(
                bridge,
                cmd="colonize",
                dry_run=params.dry_run,
                message=f"Aborted: {norm_target} is already one of your own planets.",
                planned=replace(planned, reason="Own colony safety check", executable_now=False),
                state=state,
            )
        return bridge.execute_and_summarize_browser(
            "colonize", session.exec_client(planned), state, planned, dry_run=params.dry_run
        )

    return run_browser_command(bridge, "colonize", {"dry_run": params.dry_run, "refresh": params.refresh}, _run)


def register_fleet_core_handlers(registry: CommandRegistry[BrowserBridgeFacade]) -> None:
    """Register `spy`, `recycle`, `colonize`, and `abandon_colony` commands."""
    registry.command(
        "spy",
        needs_browser=True,
        mutates=True,
        summary="Dispatch spy probe(s) to target coordinates.",
        params=SpyParams,
    )(_cmd_spy)
    registry.command(
        "recycle",
        needs_browser=True,
        mutates=True,
        summary="Dispatch recyclers to harvest a rubble field.",
        params=RecycleParams,
    )(_cmd_recycle)
    registry.command(
        "colonize",
        needs_browser=True,
        mutates=True,
        summary="Send a colony ship to settle target coordinates.",
        params=ColonizeParams,
    )(_cmd_colonize)
    registry.command(
        "abandon_colony",
        needs_browser=True,
        mutates=True,
        summary="Safely delete an undersized newly settled colony.",
        params=AbandonColonyParams,
    )(cmd_abandon_colony)
