"""Two-step fleet dispatch collaborator (`internalFleet` -> `internalSendFleet` -> `internalSendFleetFinish`)."""

from __future__ import annotations

from collections.abc import Mapping
import re

from bs4 import BeautifulSoup

from backend.core.model import PlannedAction
from backend.core.model import Resources as ModelResources
from backend.parsing.models import GameState, Resources
from backend.session.executor._fleet_errors import (
    extract_fleet_server_error,
    extract_mission_forbidden_reason,
    is_unattackable_target_html,
    pick_send_fleet_form_css,
)
from backend.session.executor._fleet_radio import match_mission_radio
from backend.session.executor._fleet_step1 import (
    BuildUpdatedStateFn,
    NavigateAndSelectFn,
    SelectPlanetFn,
    ensure_fleet_origin_planet,
    submit_step1_form,
)
from backend.session.executor._fleet_step2 import submit_step2_launch
from backend.session.executor._fleet_step2_checks import (
    build_dispatch_success_result,
    check_confirm_response_blocks,
    check_step1_response_blocks,
)
from backend.session.executor._protocols import (
    BrowserClientLike,
    ExecutorContext,
    PlannedActionLike,
    coerce_planned_action,
)
from backend.session.executor._types import ExecutionResult, call_with_interactive_priority, safe_str

_TARGET_COORDS_RE = re.compile(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", re.ASCII)
_ASTEROID_MARKER_RE = re.compile(r"\[\s*a\s*\]|\basteroid\b", re.IGNORECASE)


def coerce_action(action: PlannedAction | PlannedActionLike) -> PlannedAction:
    """Return a canonical `core.model.PlannedAction` from either canonical or legacy actions."""
    return action if isinstance(action, PlannedAction) else coerce_planned_action(action)


def to_parsing_resources(res: Resources | ModelResources | None) -> Resources | None:
    """Normalize either `parsing.models.Resources` or `core.model.Resources` into `parsing.models.Resources`."""
    if res is None:
        return None
    if isinstance(res, Resources):
        return res
    return Resources(
        pig_iron=res.pig_iron,
        metal=res.metal,
        kryptonite=res.kryptonite,
        spice=res.spice,
    )


class FleetMissionDispatcher:
    """Executes the two-step SpaceInvasion fleet dispatch flow with explicit collaborator injection."""

    def __init__(
        self,
        ctx: ExecutorContext,
        *,
        navigate_and_select_fn: NavigateAndSelectFn,
        select_planet_fn: SelectPlanetFn | None = None,
        build_updated_state_fn: BuildUpdatedStateFn,
    ) -> None:
        self._ctx = ctx
        self._navigate_and_select_fn = navigate_and_select_fn
        self._select_planet_fn = select_planet_fn
        self._build_updated_state_fn = build_updated_state_fn

    def _run_step2_and_confirm(
        self,
        client: BrowserClientLike,
        state: GameState,
        act: PlannedAction,
        *,
        nav_token1: str | None,
        slot_status: tuple[int, int] | None,
        ships_to_send: Mapping[str, int],
        mission_kind: str,
        tname_s: str,
        coords_s: str,
        t_gal: int,
        t_sys: int,
        t_pos: int,
        cargo: Resources | None,
    ) -> ExecutionResult:
        send_html = client._wait_frame_ready("F_Hauptframe", expected_action="internalSendFleet", nav_token=nav_token1)
        send_soup = BeautifulSoup(send_html or "", "html.parser")
        step1_block_res = check_step1_response_blocks(
            send_html,
            send_soup,
            action=act,
            mission_kind=mission_kind,
            tname_s=tname_s,
            ships_to_send=ships_to_send,
        )
        if step1_block_res is not None:
            return step1_block_res

        form_css = pick_send_fleet_form_css(send_soup)
        nav_token2, step2_abort = submit_step2_launch(
            self._ctx,
            client,
            action=act,
            send_html=send_html,
            send_soup=send_soup,
            form_css=form_css,
            mission_kind=mission_kind,
            tname_s=tname_s,
            cargo_resources=cargo,
        )
        if step2_abort is not None:
            return step2_abort

        confirm_html = client._wait_frame_ready("F_Hauptframe", nav_token=nav_token2)
        confirm_abort = check_confirm_response_blocks(
            confirm_html,
            action=act,
            mission_kind=mission_kind,
            tname_s=tname_s,
            ships_to_send=ships_to_send,
        )
        if confirm_abort is not None:
            return confirm_abort

        self._ctx.executed_timestamps.append(self._ctx.clock())
        updated = call_with_interactive_priority(client.read_planets_overview, refresh=True)
        updated_state = self._build_updated_state_fn(client, state, act, updated, is_fleet=True)
        return build_dispatch_success_result(
            action=act,
            updated_state=updated_state,
            ships_to_send=ships_to_send,
            mission_kind=mission_kind,
            t_gal=t_gal,
            t_sys=t_sys,
            t_pos=t_pos,
            coords_s=coords_s,
            cargo_resources=cargo,
            slot_status=slot_status,
        )

    def dispatch_fleet_mission(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: PlannedAction | PlannedActionLike,
        *,
        ships_to_send: Mapping[str, int],
        mission_kind: str,
        active_planet_confirmed: bool = True,
        cargo_resources: Resources | ModelResources | None = None,
    ) -> ExecutionResult:
        """Dispatch a fleet mission through `internalFleet` Step 1 and `internalSendFleet` Step 2."""
        act = coerce_action(action)
        tname_s = safe_str(act.target_name)
        reason_s = safe_str(act.reason)
        coords_s = safe_str(act.planet_coords)
        m = _TARGET_COORDS_RE.search(tname_s)
        if not m:
            bad_tname = None if act.target_name is None else tname_s
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=act,
                message=f"Aborted: could not parse target coordinates from {bad_tname!r}.",
            )
        t_gal, t_sys, t_pos = int(m.group(1)), int(m.group(2)), int(m.group(3))
        is_asteroid = bool(_ASTEROID_MARKER_RE.search(f"{tname_s} {reason_s}"))
        cargo = to_parsing_resources(cargo_resources)

        fleet_html, did_force_reconfirm, origin_abort = ensure_fleet_origin_planet(
            self._ctx,
            client,
            state,
            act,
            navigate_and_select_fn=self._navigate_and_select_fn,
            active_planet_confirmed=active_planet_confirmed,
        )
        if origin_abort is not None:
            return origin_abort

        nav_token1, slot_status, step1_abort = submit_step1_form(
            self._ctx,
            client,
            state,
            act,
            fleet_html=fleet_html,
            did_force_reconfirm=did_force_reconfirm,
            ships_to_send=ships_to_send,
            t_gal=t_gal,
            t_sys=t_sys,
            t_pos=t_pos,
            is_asteroid=is_asteroid,
            mission_kind=mission_kind,
            coords_s=coords_s,
            navigate_and_select_fn=self._navigate_and_select_fn,
            build_updated_state_fn=self._build_updated_state_fn,
        )
        if step1_abort is not None:
            return step1_abort

        return self._run_step2_and_confirm(
            client,
            state,
            act,
            nav_token1=nav_token1,
            slot_status=slot_status,
            ships_to_send=ships_to_send,
            mission_kind=mission_kind,
            tname_s=tname_s,
            coords_s=coords_s,
            t_gal=t_gal,
            t_sys=t_sys,
            t_pos=t_pos,
            cargo=cargo,
        )


__all__ = [
    "FleetMissionDispatcher",
    "coerce_action",
    "extract_fleet_server_error",
    "extract_mission_forbidden_reason",
    "is_unattackable_target_html",
    "match_mission_radio",
    "pick_send_fleet_form_css",
    "to_parsing_resources",
]
