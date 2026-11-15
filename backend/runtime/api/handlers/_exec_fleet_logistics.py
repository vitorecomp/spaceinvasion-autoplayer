"""Inter-planet transport and one-way fleet stationing API handlers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import re

from backend.core import catalog as game_data
from backend.core.coords import normalize_coords
from backend.core.model import GameState, PlanetState, PlannedAction, Resources, normalize_planet_type
from backend.parsing import format_station_ships_spec, parse_station_ships_spec
from backend.runtime.api.handlers._browser_proto import BrowserBridgeFacade
from backend.runtime.api.handlers._browser_session import BrowserCommandSession, run_browser_command
from backend.runtime.api.handlers._exec_fleet_helpers import aborted_action_response
from backend.runtime.api.registry import CommandRegistry
from backend.runtime.api.validation import ApiRequestError
from backend.runtime.bridge._helpers import parse_command_int, safe_api_int


@dataclass(frozen=True)
class TransportParams:
    """Parameters for `transport`."""

    target_coords: object = None
    dest_planet_id: object = None
    resources: object = None
    planet_id: object = None
    dry_run: bool = False
    refresh: bool = False


@dataclass(frozen=True)
class StationParams:
    """Parameters for `station`."""

    target_coords: object = None
    dest_planet_id: object = None
    ships: object = None
    ships_spec: object = None
    planet_id: object = None
    dry_run: bool = False
    refresh: bool = False


def _parse_dest_coords(cmd: str, raw_target_coords: object, dest_pid: int | None) -> str:
    norm_target = ""
    if isinstance(raw_target_coords, str) and raw_target_coords.strip():
        match = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", raw_target_coords.strip(), re.ASCII)
        if not match:
            raise ApiRequestError(f"'{cmd}' command 'target_coords' must be formatted as GG:SSS:PP.")
        norm_target = normalize_coords(match.group(1))
    if not norm_target and dest_pid is None:
        raise ApiRequestError(f"'{cmd}' command requires 'target_coords' or 'dest_planet_id'.")
    return norm_target


def _resolve_donor_planet(
    bridge: BrowserBridgeFacade,
    state: GameState,
    pid: int | None,
    dest_pid: int,
    ship_keys: tuple[str, ...],
    *,
    exclude_raid: bool = False,
) -> PlanetState:
    if pid is not None:
        return bridge.ctx.resolve_planet(state, pid)
    donor_p = next(
        (
            p
            for p in state.planets
            if p.planet_id != dest_pid
            and (not exclude_raid or normalize_planet_type(p) != "raid")
            and any(safe_api_int(p.ships.get(k, 0)) > 0 for k in ship_keys)
        ),
        None,
    )
    return donor_p or bridge.ctx.resolve_planet(state, None)


def _cmd_transport(bridge: BrowserBridgeFacade, params: TransportParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        dest_pid = parse_command_int(
            "dest_planet_id", params.dest_planet_id, allow_none=True, min_val=1, max_val=2**63 - 1
        )
        norm_target = _parse_dest_coords("transport", params.target_coords, dest_pid)
        if not isinstance(params.resources, Mapping):
            raise ApiRequestError(
                "'transport' command requires a 'resources' object with pig_iron/metal/kryptonite/spice."
            )
        cargo_res = Resources(
            pig_iron=parse_command_int(
                "resources.pig_iron",
                params.resources.get("pig_iron", 0),
                min_val=0,
                max_val=game_data.MAX_JSON_SAFE_INT,
            )
            or 0,
            metal=parse_command_int(
                "resources.metal", params.resources.get("metal", 0), min_val=0, max_val=game_data.MAX_JSON_SAFE_INT
            )
            or 0,
            kryptonite=parse_command_int(
                "resources.kryptonite",
                params.resources.get("kryptonite", 0),
                min_val=0,
                max_val=game_data.MAX_JSON_SAFE_INT,
            )
            or 0,
            spice=parse_command_int(
                "resources.spice", params.resources.get("spice", 0), min_val=0, max_val=game_data.MAX_JSON_SAFE_INT
            )
            or 0,
        )
        if cargo_res.is_zero():
            raise ApiRequestError("'transport' command requires at least one positive resource amount in 'resources'.")
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        state = session.resolve_fleet_command_state(pid, is_spy=True)
        dest_planet = (
            next((p for p in state.planets if p.planet_id == dest_pid), None)
            if dest_pid is not None
            else next((p for p in state.planets if normalize_coords(p.coords) == norm_target), None)
        )
        if dest_planet is None:
            active_p = state.active_planet()
            planned_bad = PlannedAction(
                priority="P1",
                kind="TRANSPORT",
                planet_id=pid or (active_p.planet_id if active_p else 0),
                planet_coords=(active_p.coords if active_p else ""),
                target_id=dest_pid,
                target_name=f"Transport -> {norm_target or dest_pid}",
                cost=cargo_res,
                reason="Own-planet transport safety check",
                executable_now=False,
            )
            return aborted_action_response(
                bridge,
                cmd="transport",
                dry_run=params.dry_run,
                message=f"Aborted: destination {norm_target or dest_pid} is not one of your own planets.",
                planned=planned_bad,
                state=state,
            )
        norm_dest = normalize_coords(dest_planet.coords)
        planet = _resolve_donor_planet(bridge, state, pid, dest_planet.planet_id, ("s1", "s2", "s3"))
        dest_label = f"Transport -> {norm_dest}" + (f" ({dest_planet.name})" if dest_planet.name else "")
        planned = PlannedAction(
            priority="P1",
            kind="TRANSPORT",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=dest_planet.planet_id,
            target_name=dest_label,
            cost=cargo_res,
            reason="API inter-planet transport command",
            executable_now=True,
        )
        if planet.planet_id == dest_planet.planet_id:
            return aborted_action_response(
                bridge,
                cmd="transport",
                dry_run=params.dry_run,
                message=f"Aborted: origin and destination are the same planet ({norm_dest}).",
                planned=replace(planned, reason="Own-planet transport safety check", executable_now=False),
                state=state,
            )
        return bridge.execute_and_summarize_browser(
            "transport", session.exec_client(planned), state, planned, dry_run=params.dry_run
        )

    return run_browser_command(bridge, "transport", {"dry_run": params.dry_run, "refresh": params.refresh}, _run)


def _parse_station_ships(params: StationParams) -> dict[str, int]:
    clean_ships: dict[str, int] = {}
    if isinstance(params.ships, Mapping):
        for raw_sk, raw_cnt in params.ships.items():
            sk_norm = game_data.safe_key(raw_sk).strip().lower()
            if not re.fullmatch(r"s\d{1,3}", sk_norm, re.ASCII) or sk_norm == "s9":
                continue
            cnt_val = (
                parse_command_int(f"ships.{sk_norm}", raw_cnt, min_val=0, max_val=game_data.MAX_JSON_SAFE_INT) or 0
            )
            if cnt_val > 0:
                clean_ships[sk_norm] = cnt_val
    elif isinstance(params.ships, str) and params.ships.strip():
        clean_ships = parse_station_ships_spec(params.ships)
    elif isinstance(params.ships_spec, str) and params.ships_spec.strip():
        clean_ships = parse_station_ships_spec(params.ships_spec)
    if not clean_ships:
        raise ApiRequestError(
            "'station' command requires a non-empty 'ships' map (e.g. {'s11': 10, 's4': 6}) or 'ships_spec'."
        )
    return clean_ships


def _cmd_station(bridge: BrowserBridgeFacade, params: StationParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        dest_pid = parse_command_int(
            "dest_planet_id", params.dest_planet_id, allow_none=True, min_val=1, max_val=2**63 - 1
        )
        norm_target = _parse_dest_coords("station", params.target_coords, dest_pid)
        clean_ships = _parse_station_ships(params)
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        state = session.resolve_fleet_command_state(pid, is_spy=True)
        dest_planet = (
            next((p for p in state.planets if p.planet_id == dest_pid), None)
            if dest_pid is not None
            else next((p for p in state.planets if normalize_coords(p.coords) == norm_target), None)
        )
        ship_spec = format_station_ships_spec(clean_ships)
        if dest_planet is None or normalize_planet_type(dest_planet) == "raid":
            active_p = state.active_planet()
            planned_bad = PlannedAction(
                priority="P2",
                kind="STATION",
                planet_id=pid or (active_p.planet_id if active_p else 0),
                planet_coords=(active_p.coords if active_p else ""),
                target_id=dest_pid,
                target_level=sum(clean_ships.values()),
                target_name=f"Station -> {norm_target or dest_pid} [{ship_spec}]",
                reason="Own-planet station safety check",
                executable_now=False,
            )
            return aborted_action_response(
                bridge,
                cmd="station",
                dry_run=params.dry_run,
                message=f"Aborted: destination {norm_target or dest_pid} is not one of your own non-raid planets.",
                planned=planned_bad,
                state=state,
            )
        norm_dest = normalize_coords(dest_planet.coords)
        planet = _resolve_donor_planet(
            bridge, state, pid, dest_planet.planet_id, tuple(clean_ships.keys()), exclude_raid=True
        )
        dest_label = (
            f"Station -> {norm_dest}" + (f" ({dest_planet.name})" if dest_planet.name else "") + f" [{ship_spec}]"
        )
        planned_st = PlannedAction(
            priority="P2",
            kind="STATION",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=dest_planet.planet_id,
            target_level=sum(clean_ships.values()),
            target_name=dest_label,
            reason="API one-way fleet stationing command",
            executable_now=True,
        )
        if planet.planet_id == dest_planet.planet_id:
            return aborted_action_response(
                bridge,
                cmd="station",
                dry_run=params.dry_run,
                message=f"Aborted: origin and destination are the same planet ({norm_dest}).",
                planned=replace(planned_st, reason="Own-planet station safety check", executable_now=False),
                state=state,
            )
        return bridge.execute_and_summarize_browser(
            "station", session.exec_client(planned_st), state, planned_st, dry_run=params.dry_run
        )

    return run_browser_command(bridge, "station", {"dry_run": params.dry_run, "refresh": params.refresh}, _run)


def register_fleet_logistics_handlers(registry: CommandRegistry[BrowserBridgeFacade]) -> None:
    """Register `transport` and `station` commands."""
    registry.command(
        "transport",
        needs_browser=True,
        mutates=True,
        summary="Transport resources between own planets.",
        params=TransportParams,
    )(_cmd_transport)
    registry.command(
        "station",
        needs_browser=True,
        mutates=True,
        summary="Rebase ships permanently to another owned non-raid planet.",
        params=StationParams,
    )(_cmd_station)
