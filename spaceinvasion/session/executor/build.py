"""Building, tutorial, demolish, research, shipyard, and economy throttle execution collaborator."""

from __future__ import annotations

import contextlib

from spaceinvasion.core import catalog
from spaceinvasion.parsing.models import GameState
from spaceinvasion.parsing.overview import parse_home_metadata
from spaceinvasion.parsing.pages import parse_buildings_page, parse_research_page, parse_unit_factory_page
from spaceinvasion.session.executor._build_helpers import (
    resolve_tutorial_form_locators,
    submit_shipyard_order_dom,
    submit_tutorial_complete_dom,
    verify_build_outcome,
    verify_research_outcome,
)
from spaceinvasion.session.executor._build_throttle import execute_throttle_building_impl
from spaceinvasion.session.executor._planet_state import (
    ActionInput,
    as_planned_action,
    read_planets_overview_safe,
    record_observed_planet,
)
from spaceinvasion.session.executor._protocols import BrowserClientLike, ExecutorContext
from spaceinvasion.session.executor._types import ExecutionResult, call_with_interactive_priority, safe_str
from spaceinvasion.session.executor.planet_select import PlanetSelector


class BuildOps:
    """Explicit collaborator owning tutorial, building, demolish, research, shipyard, and throttle ops."""

    def __init__(self, ctx: ExecutorContext, selector: PlanetSelector) -> None:
        self._ctx = ctx
        self._selector = selector

    @property
    def last_economy_rates(self) -> dict[int, dict[str, int]]:
        return self._selector.last_economy_rates

    def execute_claim_tutorial(
        self,
        client: BrowserClientLike,
        action: ActionInput,
        state: GameState | None = None,
    ) -> ExecutionResult:
        """Expand `#tutorial.text` if collapsed and submit `Mission complete` in `internalHome`."""
        act = as_planned_action(action)
        call_with_interactive_priority(client.ensure_in_game)
        home_raw = call_with_interactive_priority(client.navigate_menu, "internalHome")
        home_html = home_raw if isinstance(home_raw, str) else ""
        if home_html:
            home_pid, *_ = parse_home_metadata(home_html)
            if home_pid is not None:
                record_observed_planet(self._ctx, home_pid, confirmed=True)
        client._switch_frame("F_Hauptframe")

        submit_css, has_tut_div, has_complete = resolve_tutorial_form_locators(home_html)
        nav_token, dom_err = submit_tutorial_complete_dom(
            self._ctx,
            client,
            act,
            submit_css,
            has_tut_div=has_tut_div,
            has_complete=has_complete,
        )
        if dom_err is not None:
            return dom_err

        after_html = client._wait_frame_ready("F_Hauptframe", nav_token=nav_token)
        if isinstance(after_html, str) and after_html:
            after_pid, *_ = parse_home_metadata(after_html)
            if after_pid is not None:
                record_observed_planet(self._ctx, after_pid, confirmed=True)
        self._ctx.executed_timestamps.append(self._ctx.clock())
        updated = read_planets_overview_safe(client)
        updated_state = self._selector.build_updated_state(
            client,
            state,
            act,
            updated,
            page_html=after_html if isinstance(after_html, str) else "",
            is_home=True,
        )
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=act,
            message=f"Claimed {safe_str(act.target_name)}.",
            updated_state=updated_state,
        )

    def execute_build(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: ActionInput,
    ) -> ExecutionResult:
        """Click an actionable building upgrade link on `internalBuildings` and verify queue/spend."""
        act = as_planned_action(action)
        html = self._selector.navigate_and_select_planet(client, state, "internalBuildings", act.planet_id)
        mismatch_res = self._selector.planet_mismatch_abort(act)
        if mismatch_res is not None:
            return mismatch_res

        planet_map = next((p.buildings for p in state.planets if p.planet_id == act.planet_id), None)
        tid = catalog.cap_json_int(catalog.safe_int(act.target_id, 0))
        css = f'a.bau[href*="internalBuildItem"][href*="buildingID={tid}"]'

        def _ready(page_html: str) -> bool:
            offers = parse_buildings_page(page_html, planet_map)
            match = next((o for o in offers if o.building_id == act.target_id), None)
            return bool(match and match.can_build)

        after_html, err = self._selector.click_dom_link_with_retry(client, act, html, css, _ready)
        if err is not None:
            return err
        assert after_html is not None

        updated = read_planets_overview_safe(client)
        assert isinstance(updated, GameState)
        detail, is_validated, val_err = verify_build_outcome(state, updated, act, after_html)
        updated_state = self._selector.build_updated_state(
            client,
            state,
            act,
            updated,
            page_html=after_html,
            include_buildings=True,
        )
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=act,
            message=detail,
            updated_state=updated_state,
            validated=is_validated,
            validation_error=val_err,
        )

    def execute_demolish_building(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: ActionInput,
    ) -> ExecutionResult:
        """Open building detail when needed and click the demolish trigger for `action.target_id`."""
        act = as_planned_action(action)
        html = self._selector.navigate_and_select_planet(client, state, "internalBuildings", act.planet_id)
        mismatch_res = self._selector.planet_mismatch_abort(act)
        if mismatch_res is not None:
            return mismatch_res

        planet = next((p for p in state.planets if p.planet_id == act.planet_id), None)
        tid = catalog.cap_json_int(catalog.safe_int(act.target_id, 0))
        if planet is not None and planet.building_level(tid) <= 0:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=act,
                message=f"Aborted: {safe_str(act.target_name)} is already at level 0.",
            )

        detail_html = html
        if f"destructableid={tid}" not in (html or "").lower():
            with contextlib.suppress(AttributeError):
                d_res = call_with_interactive_priority(client.open_building_detail, tid)
                if isinstance(d_res, str) and d_res:
                    detail_html = d_res

        css = (
            f'input[onclick*="destructableID={tid}"], '
            f'button[onclick*="destructableID={tid}"], '
            f'a[href*="destructableID={tid}"], '
            f'a[onclick*="destructableID={tid}"]'
        )

        def _ready(page_html: str) -> bool:
            return f"destructableid={tid}" in (page_html or "").lower()

        after_html, err = self._selector.click_dom_link_with_retry(client, act, detail_html, css, _ready)
        if err is not None:
            return err
        assert after_html is not None

        updated = read_planets_overview_safe(client)
        assert isinstance(updated, GameState)
        after_planet = next((p for p in updated.planets if p.planet_id == act.planet_id), updated.active_planet())
        new_lvl = after_planet.building_level(tid) if after_planet else None
        tgt_lvl_opt = catalog.safe_optional_int(act.target_level, None)
        tgt_txt = catalog.fmt_compact_int(tgt_lvl_opt) if tgt_lvl_opt is not None else "0"
        new_lvl_txt = catalog.fmt_compact_int(new_lvl) if new_lvl is not None else "None"
        detail = (
            f"Demolished/queued downgrade of {safe_str(act.target_name)} -> L{tgt_txt} (current_lvl={new_lvl_txt})."
        )
        updated_state = self._selector.build_updated_state(
            client,
            state,
            act,
            updated,
            page_html=after_html,
            include_buildings=True,
        )
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=act,
            message=detail,
            updated_state=updated_state,
        )

    def execute_research(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: ActionInput,
    ) -> ExecutionResult:
        """Click an actionable research link on `internalResearch` and verify queue/level."""
        act = as_planned_action(action)
        html = self._selector.navigate_and_select_planet(client, state, "internalResearch", act.planet_id)
        mismatch_res = self._selector.planet_mismatch_abort(act)
        if mismatch_res is not None:
            return mismatch_res

        tid = catalog.cap_json_int(catalog.safe_int(act.target_id, 0))
        css = f'a.bau[href*="internalBuildItem"][href*="researchID={tid}"]'

        def _ready(page_html: str) -> bool:
            offers = parse_research_page(page_html, state.researches)
            match = next((o for o in offers if o.research_id == act.target_id), None)
            return bool(match and match.can_research)

        after_html, err = self._selector.click_dom_link_with_retry(client, act, html, css, _ready)
        if err is not None:
            return err
        assert after_html is not None

        updated = read_planets_overview_safe(client)
        assert isinstance(updated, GameState)
        detail, res_validated, res_val_err = verify_research_outcome(updated, act, after_html)
        updated_state = self._selector.build_updated_state(
            client,
            state,
            act,
            updated,
            page_html=after_html,
            include_research=True,
        )
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=act,
            message=detail,
            updated_state=updated_state,
            validated=res_validated,
            validation_error=res_val_err,
        )

    def execute_build_ship(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: ActionInput,
    ) -> ExecutionResult:
        """Order ship production on `internalWeaponFactory` and rebuild updated shipyard state."""
        act = as_planned_action(action)
        html = self._selector.navigate_and_select_planet(client, state, "internalWeaponFactory", act.planet_id)
        mismatch_res = self._selector.planet_mismatch_abort(act)
        if mismatch_res is not None:
            return mismatch_res

        planet = next((p for p in state.planets if p.planet_id == act.planet_id), None)
        offers = parse_unit_factory_page(html, unit_type="ship", owned_counts=planet.ships if planet else None)
        tname_s = safe_str(act.target_name)
        coords_s = safe_str(act.planet_coords)
        matching = next((o for o in offers if o.unit_id == act.target_id), None)
        if matching is None or not matching.can_build or not matching.input_name:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=act,
                message=f"Aborted: ship {tname_s} input unavailable in Weapons factory.",
            )

        qty = max(1, catalog.cap_json_int(catalog.safe_int(act.target_level, 1)))
        nav_token, dom_err = submit_shipyard_order_dom(self._ctx, client, act, matching.input_name, qty, tname_s)
        if dom_err is not None:
            return dom_err

        after_html = client._wait_frame_ready("F_Hauptframe", nav_token=nav_token)
        self._ctx.executed_timestamps.append(self._ctx.clock())
        updated = read_planets_overview_safe(client)
        updated_state = self._selector.build_updated_state(
            client,
            state,
            act,
            updated,
            page_html=after_html if isinstance(after_html, str) else "",
            include_shipyard=True,
        )
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=act,
            message=f"Ordered {catalog.fmt_compact_int(qty)}x {tname_s} on [{coords_s}].",
            updated_state=updated_state,
        )

    def execute_throttle_building(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: ActionInput,
    ) -> ExecutionResult:
        """Adjust a resource building's production percentage on `internalEconomy`."""
        act = as_planned_action(action)
        return execute_throttle_building_impl(self._ctx, self._selector, client, state, act)
