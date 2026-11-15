"""Undersized secondary colony abandonment collaborator via `internalSettings`."""

from __future__ import annotations

from bs4 import BeautifulSoup

from backend.core import catalog
from backend.parsing.models import GameState
from backend.session.executor._colony_helpers import (
    is_undersized_fresh_colony_state,
    trigger_colony_deletion_dom,
    validate_settings_colony_row,
)
from backend.session.executor._planet_state import (
    ActionInput,
    as_planned_action,
    homeworld_planet_id_from_state,
    navigate_settings_with_fallback,
    read_planets_overview_safe,
    try_select_planet_on_client,
)
from backend.session.executor._protocols import BrowserClientLike, ExecutorContext
from backend.session.executor._types import ExecutionResult, safe_str
from backend.session.executor.planet_select import PlanetSelector


class ColonyOps:
    """Explicit collaborator owning safe undersized colony deletion on `internalSettings`."""

    def __init__(self, ctx: ExecutorContext, selector: PlanetSelector) -> None:
        self._ctx = ctx
        self._selector = selector

    def execute_abandon_colony(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: ActionInput,
    ) -> ExecutionResult:
        """Delete an undersized newly settled secondary colony (`< min_colony_fields`, 0 buildings/ships)."""
        act = as_planned_action(action)
        planet = next((p for p in state.planets if p.planet_id == act.planet_id), None)
        if planet is None:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=act,
                message=f"Aborted: colony planet_id={act.planet_id} no longer exists in empire state.",
            )

        raw_min = catalog.safe_optional_int(act.target_level, None)
        min_fields = max(50, int(raw_min)) if (raw_min is not None and raw_min >= 50) else 300
        coords_s = safe_str(planet.coords or act.planet_coords)
        if not is_undersized_fresh_colony_state(planet, state, min_fields):
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=act,
                message=(
                    f"Aborted safety check: refusing to delete planet [{coords_s}] "
                    f"({planet.fields_used}/{planet.fields_max} fields) — Homeworld, developed, "
                    f"stationed fleet/defense, or >= {min_fields} fields."
                ),
            )

        settings_html = navigate_settings_with_fallback(client)
        soup = BeautifulSoup(settings_html, "html.parser")
        row_err = validate_settings_colony_row(soup, planet, coords_s, min_fields)
        if row_err is not None:
            return ExecutionResult(executed=False, dry_run=False, action=act, message=row_err)

        nav_token, dom_err = trigger_colony_deletion_dom(self._ctx, client, planet.planet_id, coords_s)
        if dom_err is not None:
            return ExecutionResult(executed=False, dry_run=False, action=act, message=dom_err)

        after_html = client._wait_frame_ready("F_Hauptframe", expected_action="internalSettings", nav_token=nav_token)
        self._ctx.executed_timestamps.append(self._ctx.clock())
        after_soup = BeautifulSoup(after_html if isinstance(after_html, str) else "", "html.parser")
        gone_on_settings = after_soup.find("input", attrs={"name": f"planet_{planet.planet_id}"}) is None

        hw_pid = homeworld_planet_id_from_state(state)
        if hw_pid is not None and hw_pid != planet.planet_id:
            try_select_planet_on_client(client, hw_pid)

        updated = read_planets_overview_safe(client)
        updated_state = updated if isinstance(updated, GameState) else None
        planets_after = updated_state.planets if updated_state is not None else ()
        gone_in_overview = all(p.planet_id != planet.planet_id for p in planets_after)
        deleted_ok = bool(gone_on_settings and gone_in_overview)
        return ExecutionResult(
            executed=deleted_ok,
            dry_run=False,
            action=act,
            message=(
                f"Deleted undersized colony [{coords_s}] ({planet.fields_max} fields < {min_fields} target) to re-colonize."
                if deleted_ok
                else f"Failed to verify deletion of colony [{coords_s}] (planet_{planet.planet_id} still present)."
            ),
            updated_state=updated_state,
            validated=deleted_ok,
            validation_error=(
                None
                if deleted_ok
                else f"Colony planet_{planet.planet_id} ([{coords_s}]) still present after executeDeletePlanet."
            ),
        )
