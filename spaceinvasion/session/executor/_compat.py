"""Backwards-compatible static and private method delegates for `ActionExecutor`."""

from __future__ import annotations

from collections.abc import Callable

from spaceinvasion.parsing.fleet_page import (
    extract_internal_fleet_planet_coords,
    parse_internal_fleet_slot_status,
)
from spaceinvasion.parsing.models import GameState
from spaceinvasion.session.client.reader import extract_selected_planet_id
from spaceinvasion.session.executor._dom import find_newplanet_switch_target
from spaceinvasion.session.executor._fleet_errors import (
    extract_mission_forbidden_reason,
    is_unattackable_target_html,
    pick_send_fleet_form_css,
)
from spaceinvasion.session.executor._fleet_radio import match_mission_radio
from spaceinvasion.session.executor._planet_state import ActionInput, pace_client_action
from spaceinvasion.session.executor._protocols import BrowserClientLike, ExecutorContext
from spaceinvasion.session.executor._types import ExecutionResult
from spaceinvasion.session.executor.build import BuildOps
from spaceinvasion.session.executor.colony import ColonyOps
from spaceinvasion.session.executor.missions import MissionRunner
from spaceinvasion.session.executor.planet_select import PlanetSelector
from spaceinvasion.session.executor.transmitter import TransmitterOps


class ExecutorCompatMixin:
    """Exposes legacy static parsers and private helper methods on `ActionExecutor`."""

    _ctx: ExecutorContext
    _selector: PlanetSelector
    _build_ops: BuildOps
    _missions: MissionRunner
    _transmitter_ops: TransmitterOps
    _colony_ops: ColonyOps

    _extract_selected_planet_id = staticmethod(extract_selected_planet_id)
    _extract_internal_fleet_planet_coords = staticmethod(extract_internal_fleet_planet_coords)
    _parse_internal_fleet_slot_status = staticmethod(parse_internal_fleet_slot_status)
    _match_mission_radio = staticmethod(match_mission_radio)
    _find_newplanet_switch_target = staticmethod(find_newplanet_switch_target)
    _pick_send_fleet_form_css = staticmethod(pick_send_fleet_form_css)
    _is_unattackable_target_html = staticmethod(is_unattackable_target_html)
    _extract_mission_forbidden_reason = staticmethod(extract_mission_forbidden_reason)

    def _pace_action(
        self,
        client: BrowserClientLike,
        *,
        priority: str = "interactive",
        cost: int = 1,
    ) -> None:
        pace_client_action(self._ctx, client, priority=priority, cost=cost)

    def _navigate_and_select_planet(
        self,
        client: BrowserClientLike,
        state: GameState,
        menu_action: str,
        planet_id: int,
        *,
        active_planet_confirmed: bool = True,
    ) -> str:
        return self._selector.navigate_and_select_planet_impl(
            client,
            state,
            menu_action,
            planet_id,
            active_planet_confirmed=active_planet_confirmed,
        )

    def _select_planet_if_needed(
        self,
        client: BrowserClientLike,
        planet_id: int,
        *,
        html: str = "",
        require_confirm: bool = False,
    ) -> tuple[bool, str | None]:
        return self._selector.select_planet_if_needed(
            client,
            planet_id,
            html=html,
            require_confirm=require_confirm,
        )

    def _build_updated_state(
        self,
        client: BrowserClientLike,
        prev_state: GameState | None,
        action: ActionInput,
        updated_overview: object,
        *,
        page_html: str = "",
        is_home: bool = False,
        is_fleet: bool = False,
        include_buildings: bool = False,
        include_research: bool = False,
        include_shipyard: bool = False,
    ) -> GameState | None:
        return self._selector.build_updated_state(
            client,
            prev_state,
            action,
            updated_overview,
            page_html=page_html,
            is_home=is_home,
            is_fleet=is_fleet,
            include_buildings=include_buildings,
            include_research=include_research,
            include_shipyard=include_shipyard,
        )

    def _click_dom_link_with_retry(
        self,
        client: BrowserClientLike,
        action: ActionInput,
        initial_html: str,
        selector: str,
        is_ready_fn: Callable[[str], bool],
    ) -> tuple[str | None, ExecutionResult | None]:
        return self._selector.click_dom_link_with_retry(client, action, initial_html, selector, is_ready_fn)

    def _execute_claim_tutorial(
        self,
        client: BrowserClientLike,
        action: ActionInput,
        state: GameState | None = None,
    ) -> ExecutionResult:
        return self._build_ops.execute_claim_tutorial(client, action, state=state)

    def _execute_build(self, client: BrowserClientLike, state: GameState, action: ActionInput) -> ExecutionResult:
        return self._build_ops.execute_build(client, state, action)

    def _execute_demolish_building(
        self, client: BrowserClientLike, state: GameState, action: ActionInput
    ) -> ExecutionResult:
        return self._build_ops.execute_demolish_building(client, state, action)

    def _execute_research(self, client: BrowserClientLike, state: GameState, action: ActionInput) -> ExecutionResult:
        return self._build_ops.execute_research(client, state, action)

    def _execute_build_ship(self, client: BrowserClientLike, state: GameState, action: ActionInput) -> ExecutionResult:
        return self._build_ops.execute_build_ship(client, state, action)

    def _execute_throttle_building(
        self, client: BrowserClientLike, state: GameState, action: ActionInput
    ) -> ExecutionResult:
        return self._build_ops.execute_throttle_building(client, state, action)

    def _execute_abandon_colony(
        self, client: BrowserClientLike, state: GameState, action: ActionInput
    ) -> ExecutionResult:
        return self._colony_ops.execute_abandon_colony(client, state, action)

    def _execute_activate_transmitter(
        self, client: BrowserClientLike, state: GameState, action: ActionInput
    ) -> ExecutionResult:
        return self._transmitter_ops.execute_activate_transmitter(client, state, action)

    def _execute_scan_galaxy(self, client: BrowserClientLike, state: GameState, action: ActionInput) -> ExecutionResult:
        return self._missions.execute_scan_galaxy(client, state, action)

    def _execute_spy(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: ActionInput,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        return self._missions.execute_spy(client, state, action, active_planet_confirmed=active_planet_confirmed)

    def _execute_recycle(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: ActionInput,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        return self._missions.execute_recycle(client, state, action, active_planet_confirmed=active_planet_confirmed)

    def _execute_colonize(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: ActionInput,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        return self._missions.execute_colonize(client, state, action, active_planet_confirmed=active_planet_confirmed)

    def _execute_transport(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: ActionInput,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        return self._missions.execute_transport(client, state, action, active_planet_confirmed=active_planet_confirmed)

    def _execute_station(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: ActionInput,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        return self._missions.execute_station(client, state, action, active_planet_confirmed=active_planet_confirmed)

    def _execute_attack(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: ActionInput,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        return self._missions.execute_attack(client, state, action, active_planet_confirmed=active_planet_confirmed)

    def _execute_escape_flight(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: ActionInput,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        return self._missions.execute_escape_flight(
            client, state, action, active_planet_confirmed=active_planet_confirmed
        )
