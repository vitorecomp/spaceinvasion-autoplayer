"""Fleet mission execution collaborator (`SCAN_GALAXY`, `ESCAPE_FLIGHT`, `ATTACK`, `SPY`, `RECYCLE`, `COLONIZE`, `TRANSPORT`, `STATION`)."""

from __future__ import annotations

from dataclasses import replace

from spaceinvasion.core import catalog
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import PlannedAction
from spaceinvasion.parsing.models import GalaxySlot, GameState
from spaceinvasion.session.exec_ops._fleet_step1 import (
    BuildUpdatedStateFn,
    NavigateAndSelectFn,
    SelectPlanetFn,
)
from spaceinvasion.session.exec_ops._missions_galaxy import (
    scan_galaxy_system,
    try_quick_galaxy_free_slot_spy,
)
from spaceinvasion.session.exec_ops._missions_helpers import (
    compute_transport_cargo_and_ships,
    find_origin_planet,
    resolve_own_origin_and_dest,
    resolve_station_ships,
    select_attack_ships,
    select_escape_ships,
)
from spaceinvasion.session.exec_ops._protocols import (
    BrowserClientLike,
    ExecutorContext,
    PlannedActionLike,
)
from spaceinvasion.session.exec_ops._types import ExecutionResult, safe_str
from spaceinvasion.session.exec_ops.fleet_dispatch import FleetMissionDispatcher, coerce_action


class MissionRunner:
    """Executes all Galaxy scan and fleet mission actions using `FleetMissionDispatcher`."""

    def __init__(
        self,
        ctx: ExecutorContext,
        dispatcher: FleetMissionDispatcher,
        *,
        navigate_and_select_fn: NavigateAndSelectFn,
        select_planet_fn: SelectPlanetFn | None = None,
        build_updated_state_fn: BuildUpdatedStateFn,
    ) -> None:
        self._ctx = ctx
        self._dispatcher = dispatcher
        self._navigate_and_select_fn = navigate_and_select_fn
        self._select_planet_fn = select_planet_fn
        self._build_updated_state_fn = build_updated_state_fn
        self.last_galaxy_slots: list[GalaxySlot] = []

    def execute_scan_galaxy(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: PlannedAction | PlannedActionLike,
    ) -> ExecutionResult:
        """Scan the requested Galaxy `[GG:SSS]` system without consuming hourly mutation budget."""
        _ = state
        act = coerce_action(action)
        result, slots = scan_galaxy_system(client, act)
        self.last_galaxy_slots = slots
        return result

    def execute_escape_flight(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: PlannedAction | PlannedActionLike,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        """Dispatch an `ESCAPE` fleetsave flight (all mobile ships, or 1 ship for tutorial)."""
        act = coerce_action(action)
        planet = find_origin_planet(state, act.planet_id)
        ships_to_send = select_escape_ships(planet, act)
        if not ships_to_send:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=act,
                message=f"Aborted: no stationed mobile ships on [{safe_str(act.planet_coords)}].",
            )
        return self._dispatcher.dispatch_fleet_mission(
            client,
            state,
            act,
            ships_to_send=ships_to_send,
            mission_kind="ESCAPE",
            active_planet_confirmed=active_planet_confirmed,
        )

    def execute_attack(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: PlannedAction | PlannedActionLike,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        """Dispatch an `ATTACK` fleet with all stationed combat and cargo ships."""
        act = coerce_action(action)
        planet = find_origin_planet(state, act.planet_id)
        ships_to_send = select_attack_ships(planet)
        if not ships_to_send:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=act,
                message=f"Aborted: no stationed combat or transport ships on [{safe_str(act.planet_coords)}].",
            )
        return self._dispatcher.dispatch_fleet_mission(
            client,
            state,
            act,
            ships_to_send=ships_to_send,
            mission_kind="ATTACK",
            active_planet_confirmed=active_planet_confirmed,
        )

    def execute_spy(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: PlannedAction | PlannedActionLike,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        """Dispatch espionage probes (`s10`), preferring 2D Galaxy quick-spy for free-slot probes."""
        act = coerce_action(action)
        planet = find_origin_planet(state, act.planet_id)
        avail = catalog.cap_json_int(catalog.safe_int(planet.ships.get("s10", 0), 0)) if planet is not None else 0
        if avail <= 0:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=act,
                message=f"Aborted: no spy probes (s10) stationed on [{safe_str(act.planet_coords)}].",
            )
        probe_count = min(avail, max(1, catalog.cap_json_int(catalog.safe_int(act.target_level, 1))))
        q_res, q_slots = try_quick_galaxy_free_slot_spy(
            self._ctx,
            client,
            state,
            act,
            probe_count=probe_count,
            active_planet_confirmed=active_planet_confirmed,
            navigate_and_select_fn=self._navigate_and_select_fn,
            build_updated_state_fn=self._build_updated_state_fn,
        )
        if q_slots is not None:
            self.last_galaxy_slots = q_slots
        if q_res is not None:
            return q_res
        return self._dispatcher.dispatch_fleet_mission(
            client,
            state,
            act,
            ships_to_send={"s10": probe_count},
            mission_kind="SPY",
            active_planet_confirmed=active_planet_confirmed,
        )

    def execute_recycle(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: PlannedAction | PlannedActionLike,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        """Dispatch stationed recyclers (`s11`) to harvest a target debris/rubble field."""
        act = coerce_action(action)
        planet = find_origin_planet(state, act.planet_id)
        avail = catalog.cap_json_int(catalog.safe_int(planet.ships.get("s11", 0), 0)) if planet is not None else 0
        if avail <= 0:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=act,
                message=f"Aborted: no recyclers (s11) stationed on [{safe_str(act.planet_coords)}].",
            )
        req_qty = catalog.safe_optional_int(act.target_level, None)
        rec_count = min(avail, max(1, catalog.cap_json_int(req_qty))) if req_qty is not None else avail
        return self._dispatcher.dispatch_fleet_mission(
            client,
            state,
            act,
            ships_to_send={"s11": rec_count},
            mission_kind="RECYCLE",
            active_planet_confirmed=active_planet_confirmed,
        )

    def execute_colonize(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: PlannedAction | PlannedActionLike,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        """Dispatch a single colony ship (`s8`) to settle an uninhabited planet slot."""
        act = coerce_action(action)
        planet = find_origin_planet(state, act.planet_id)
        avail = catalog.cap_json_int(catalog.safe_int(planet.ships.get("s8", 0), 0)) if planet is not None else 0
        if avail <= 0:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=act,
                message=f"Aborted: no colony ship (s8) stationed on [{safe_str(act.planet_coords)}].",
            )
        return self._dispatcher.dispatch_fleet_mission(
            client,
            state,
            act,
            ships_to_send={"s8": 1},
            mission_kind="COLONIZE",
            active_planet_confirmed=active_planet_confirmed,
        )

    def execute_transport(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: PlannedAction | PlannedActionLike,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        """Dispatch an inter-planet resource Transport strictly between two owned planets."""
        act = coerce_action(action)
        origin, dest, guard_err = resolve_own_origin_and_dest(state, act, mission_label="Transport")
        if guard_err is not None or origin is None or dest is None:
            return guard_err or ExecutionResult(executed=False, dry_run=False, action=act, message="Aborted.")
        safe_cargo, ships_to_send, cargo_err = compute_transport_cargo_and_ships(state, origin, act)
        if cargo_err is not None or safe_cargo is None:
            return cargo_err or ExecutionResult(executed=False, dry_run=False, action=act, message="Aborted.")
        norm_act = replace(
            act,
            target_id=dest.planet_id,
            target_name=f"Transport -> {normalize_coords(dest.coords)} ({dest.name})",
        )
        return self._dispatcher.dispatch_fleet_mission(
            client,
            state,
            norm_act,
            ships_to_send=ships_to_send,
            mission_kind="TRANSPORT",
            active_planet_confirmed=active_planet_confirmed,
            cargo_resources=safe_cargo,
        )

    def execute_station(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: PlannedAction | PlannedActionLike,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        """Dispatch a one-way Station (ship deployment) strictly between two owned non-raid planets."""
        act = coerce_action(action)
        origin, dest, guard_err = resolve_own_origin_and_dest(state, act, mission_label="Station")
        if guard_err is not None or origin is None or dest is None:
            return guard_err or ExecutionResult(executed=False, dry_run=False, action=act, message="Aborted.")
        ships_to_send, ship_err = resolve_station_ships(origin, dest, act)
        if ship_err is not None:
            return ship_err
        norm_act = replace(
            act,
            target_id=dest.planet_id,
            target_name=f"Station -> {normalize_coords(dest.coords)} ({dest.name})",
        )
        return self._dispatcher.dispatch_fleet_mission(
            client,
            state,
            norm_act,
            ships_to_send=ships_to_send,
            mission_kind="STATION",
            active_planet_confirmed=active_planet_confirmed,
        )


__all__ = ["MissionRunner"]
