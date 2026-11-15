"""Attack and emergency escape_flight API handlers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import re
import time

from backend.combat.simulator import simulate_raid_outcome
from backend.core import catalog as game_data
from backend.core.coords import normalize_coords
from backend.core.model import GameState, PlanetState, PlannedAction, Resources
from backend.parsing import SpyReport
from backend.runtime.api.handlers._browser_proto import BrowserBridgeFacade
from backend.runtime.api.handlers._browser_session import BrowserCommandSession, run_browser_command
from backend.runtime.api.handlers._exec_fleet_helpers import aborted_action_response, lookup_target_slot_and_row
from backend.runtime.api.registry import CommandRegistry
from backend.runtime.api.validation import ApiRequestError
from backend.runtime.bridge._helpers import (
    bounded_api_str,
    parse_command_int,
    safe_api_float,
    safe_api_int,
)

_ATTACK_SHIP_KEYS: tuple[str, ...] = ("s4", "s5", "s6", "s7", "s13", "s14", "s15", "s16", "s1", "s2", "s3")


@dataclass(frozen=True)
class AttackParams:
    """Parameters for `attack`."""

    target_coords: object = None
    planet_id: object = None
    dry_run: bool = False
    refresh: bool = False


@dataclass(frozen=True)
class EscapeFlightParams:
    """Parameters for `escape_flight`."""

    planet_id: object = None
    target_coords: object = None
    target_name: object = None
    reason: object = None
    dry_run: bool = False
    refresh: bool = False


def _check_attack_slot_status(
    bridge: BrowserBridgeFacade,
    *,
    state: GameState,
    planned: PlannedAction,
    norm_target: str,
    dry_run: bool,
    target_row: Mapping[str, object] | None,
    eff_slot: Mapping[str, object] | None,
) -> tuple[dict[str, object] | None, bool, bool]:
    meta = eff_slot or target_row or {}
    is_inactive = bool(meta.get("is_inactive", False))
    is_spacia = bool(meta.get("is_spacia", False))
    status_color = game_data.safe_key(meta.get("status_color") or "").strip().lower()
    atk_owner = game_data.safe_key(meta.get("owner") or "").strip()
    atk_pname = game_data.safe_key(meta.get("planet_name") or meta.get("name") or "").strip()
    if status_color == "yellow":
        is_inactive = True
    if eff_slot is not None and (
        (status_color == "free" and not is_spacia) or (not is_spacia and not atk_owner and not atk_pname)
    ):
        return (
            aborted_action_response(
                bridge,
                cmd="attack",
                dry_run=dry_run,
                message=f"Aborted: target {norm_target} is uninhabited/ownerless and cannot be attacked.",
                planned=replace(planned, executable_now=False),
                state=state,
            ),
            is_inactive,
            is_spacia,
        )
    if status_color in ("blue", "green", "white", "purple", "dark_red") and not is_spacia:
        return (
            aborted_action_response(
                bridge,
                cmd="attack",
                dry_run=dry_run,
                message=f"Aborted: target {norm_target} is protected ({status_color}).",
                planned=planned,
                state=state,
            ),
            is_inactive,
            is_spacia,
        )
    if (
        status_color == "light_red"
        and not is_inactive
        and not is_spacia
        and not bridge.ctx.strategy_config.allow_pvp_in_az
    ):
        return (
            aborted_action_response(
                bridge,
                cmd="attack",
                dry_run=dry_run,
                message=f"Aborted: target {norm_target} is an active player and allow_pvp_in_az=false.",
                planned=planned,
                state=state,
            ),
            is_inactive,
            is_spacia,
        )
    return None, is_inactive, is_spacia


def _check_attack_bash_and_cooldown(
    bridge: BrowserBridgeFacade,
    *,
    state: GameState,
    planned: PlannedAction,
    norm_target: str,
    dry_run: bool,
    target_row: Mapping[str, object] | None,
    is_inactive: bool,
    is_spacia: bool,
) -> dict[str, object] | None:
    ctx = bridge.ctx
    now_s = time.time()
    if ctx.db is not None:
        bash = ctx.db.load_bash_tracker()
        if not bash.can_attack(norm_target, is_inactive=is_inactive, is_spacia=is_spacia):
            until_cd = bash.blocked_until(norm_target)
            rem_cd = max(0.0, until_cd - now_s) if until_cd > 0.0 else 0.0
            cd_note = f" ({rem_cd:.0f}s cooldown remaining)" if rem_cd > 0.0 else ""
            return aborted_action_response(
                bridge,
                cmd="attack",
                dry_run=dry_run,
                message=f"Aborted: BashTracker 3/24h limit reached for {norm_target}{cd_note}.",
                planned=planned,
                state=state,
            )
    elif norm_target in ctx.target_blocks:
        mem_until, mem_reason = ctx.target_blocks[norm_target]
        rem_cd = mem_until - now_s
        if rem_cd > 0.0 and "bash" in str(mem_reason).lower():
            return aborted_action_response(
                bridge,
                cmd="attack",
                dry_run=dry_run,
                message=f"Aborted: BashTracker 3/24h limit reached for {norm_target} ({rem_cd:.0f}s cooldown remaining).",
                planned=planned,
                state=state,
            )
    blocked_until = safe_api_float((target_row or {}).get("blocked_until_ts", 0.0))
    if norm_target in ctx.target_blocks:
        blocked_until = max(blocked_until, float(ctx.target_blocks[norm_target][0]))
    if blocked_until > now_s:
        rem_cd = max(0.0, blocked_until - now_s)
        return aborted_action_response(
            bridge,
            cmd="attack",
            dry_run=dry_run,
            validated=True,
            message=f"Aborted: target {norm_target} is on protection cooldown ({rem_cd:.0f}s remaining).",
            planned=replace(planned, executable_now=False),
            state=state,
        )
    return None


def _check_attack_combat_safety(
    bridge: BrowserBridgeFacade,
    *,
    state: GameState,
    planet: PlanetState,
    planned: PlannedAction,
    norm_target: str,
    dry_run: bool,
    target_row: Mapping[str, object] | None,
    is_spacia: bool,
) -> dict[str, object] | None:
    if target_row is None:
        return None
    raw_res = target_row.get("resources")
    res_d: Mapping[object, object] = raw_res if isinstance(raw_res, Mapping) else {}
    rep_res = Resources(
        pig_iron=max(0, safe_api_int(res_d.get("pig_iron", 0), 0)),
        metal=max(0, safe_api_int(res_d.get("metal", 0), 0)),
        kryptonite=max(0, safe_api_int(res_d.get("kryptonite", 0), 0)),
        spice=max(0, safe_api_int(res_d.get("spice", 0), 0)),
    )
    raw_fleet = target_row.get("fleet")
    rep_fleet = (
        {game_data.safe_key(k): cnt for k, v in raw_fleet.items() if (cnt := max(0, safe_api_int(v, 0))) > 0}
        if isinstance(raw_fleet, Mapping)
        else {}
    )
    raw_def = target_row.get("defense")
    rep_def = (
        {game_data.safe_key(k): cnt for k, v in raw_def.items() if (cnt := max(0, safe_api_int(v, 0))) > 0}
        if isinstance(raw_def, Mapping)
        else {}
    )
    if not (bool(target_row.get("has_spy_intel")) or not rep_res.is_zero() or rep_fleet or rep_def):
        return None
    atk_ships = {k: cnt for k in _ATTACK_SHIP_KEYS if (cnt := max(0, safe_api_int(planet.ships.get(k, 0), 0))) > 0}
    if not atk_ships:
        return None
    rep_obj = SpyReport(
        target_coords=norm_target,
        owner=game_data.safe_key(target_row.get("owner") or ""),
        resources=rep_res,
        fleet=rep_fleet,
        defense=rep_def,
        dug_in=bool(target_row.get("dug_in", False)),
        is_spacia=is_spacia,
    )
    sim = simulate_raid_outcome(
        atk_ships,
        rep_obj,
        attacker_researches=state.researches,
        safety_margin=bridge.ctx.strategy_config.combat_safety_margin,
    )
    if not sim.safe_to_attack:
        return aborted_action_response(
            bridge,
            cmd="attack",
            dry_run=dry_run,
            message=f"Aborted: combat safety check failed ({sim.reason}).",
            planned=planned,
            state=state,
        )
    return None


def _cmd_attack(bridge: BrowserBridgeFacade, params: AttackParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        if not isinstance(params.target_coords, str):
            raise ApiRequestError("'attack' command requires 'target_coords' formatted as GG:SSS:PP.")
        target_coords = params.target_coords.strip()
        if not re.fullmatch(r"\d{1,2}:\d{1,3}:\d{1,2}", target_coords, re.ASCII):
            raise ApiRequestError("'attack' command requires 'target_coords' formatted as GG:SSS:PP.")
        norm_target = normalize_coords(target_coords)
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        state = session.resolve_fleet_command_state(pid, is_spy=False)
        planet = bridge.ctx.resolve_planet(state, pid)
        planned = PlannedAction(
            priority="P2",
            kind="ATTACK",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_name=norm_target,
            reason="API attack command",
            executable_now=True,
        )
        if not params.dry_run and not bridge.ctx.live_executor.allow_raids:
            return bridge.execute_and_summarize_browser(
                "attack", session.exec_client(planned), state, planned, dry_run=params.dry_run
            )
        if any(normalize_coords(p.coords) == norm_target for p in state.planets):
            return aborted_action_response(
                bridge,
                cmd="attack",
                dry_run=params.dry_run,
                message=f"Aborted: {norm_target} is one of your own planets.",
                planned=planned,
                state=state,
            )
        target_row, eff_slot = lookup_target_slot_and_row(bridge, norm_target)
        status_abort, is_inactive, is_spacia = _check_attack_slot_status(
            bridge,
            state=state,
            planned=planned,
            norm_target=norm_target,
            dry_run=params.dry_run,
            target_row=target_row,
            eff_slot=eff_slot,
        )
        if status_abort is not None:
            return status_abort
        bash_abort = _check_attack_bash_and_cooldown(
            bridge,
            state=state,
            planned=planned,
            norm_target=norm_target,
            dry_run=params.dry_run,
            target_row=target_row,
            is_inactive=is_inactive,
            is_spacia=is_spacia,
        )
        if bash_abort is not None:
            return bash_abort
        sim_abort = _check_attack_combat_safety(
            bridge,
            state=state,
            planet=planet,
            planned=planned,
            norm_target=norm_target,
            dry_run=params.dry_run,
            target_row=target_row,
            is_spacia=is_spacia,
        )
        if sim_abort is not None:
            return sim_abort
        return bridge.execute_and_summarize_browser(
            "attack", session.exec_client(planned), state, planned, dry_run=params.dry_run
        )

    return run_browser_command(bridge, "attack", {"dry_run": params.dry_run, "refresh": params.refresh}, _run)


def _cmd_escape_flight(bridge: BrowserBridgeFacade, params: EscapeFlightParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        if params.target_coords is not None and not isinstance(params.target_coords, str):
            raise ApiRequestError("'escape_flight' field 'target_coords' must be a string.")
        if params.target_name is not None and not isinstance(params.target_name, str):
            raise ApiRequestError("'escape_flight' field 'target_name' must be a string.")
        if params.reason is not None and not isinstance(params.reason, str):
            raise ApiRequestError("'escape_flight' field 'reason' must be a string.")
        state = session.resolve_fleet_command_state(pid, is_spy=False)
        planet = bridge.ctx.resolve_planet(state, pid)
        t_coords = ""
        tc_str = params.target_coords.strip() if isinstance(params.target_coords, str) else ""
        tn_str = params.target_name.strip() if isinstance(params.target_name, str) else ""
        if tc_str:
            m_tc = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", tc_str, re.ASCII)
            if not m_tc:
                raise ApiRequestError("'escape_flight' field 'target_coords' must contain GG:SSS:PP.")
            t_coords = normalize_coords(m_tc.group(0))
        elif tn_str:
            m_tn = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", tn_str, re.ASCII)
            if m_tn:
                t_coords = normalize_coords(m_tn.group(0))
        if not t_coords:
            m_origin = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", str(planet.coords or ""), re.ASCII)
            t_coords = (
                f"{int(m_origin.group(1)):02d}:{int(m_origin.group(2)):03d}:{(int(m_origin.group(3)) % 16) + 1:02d}"
                if m_origin
                else "01:001:02"
            )
        esc_reason = (
            bounded_api_str(params.reason.strip(), default="API escape_flight command")
            if isinstance(params.reason, str) and params.reason.strip()
            else "API escape_flight command"
        )
        planned = PlannedAction(
            priority="P2",
            kind="ESCAPE_FLIGHT",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_name=f"Save -> {t_coords}",
            reason=esc_reason,
            executable_now=bool(bridge.ctx.strategy_config.allow_fleetsave),
        )
        return bridge.execute_and_summarize_browser(
            "escape_flight", session.exec_client(planned), state, planned, dry_run=params.dry_run, return_home=True
        )

    return run_browser_command(bridge, "escape_flight", {"dry_run": params.dry_run, "refresh": params.refresh}, _run)


def register_fleet_combat_handlers(registry: CommandRegistry[BrowserBridgeFacade]) -> None:
    """Register `attack` and `escape_flight` commands."""
    registry.command(
        "attack",
        needs_browser=True,
        mutates=True,
        summary="Dispatch an attack fleet after protection, bash, and 1:1 combat safety checks.",
        params=AttackParams,
    )(_cmd_attack)
    registry.command(
        "escape_flight",
        needs_browser=True,
        mutates=True,
        summary="Dispatch an invisible Escape Flight to save fleet and resources.",
        params=EscapeFlightParams,
    )(_cmd_escape_flight)
