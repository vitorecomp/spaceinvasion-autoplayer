"""Shared fleet safety helpers and `abandon_colony` API handler."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import re

from spaceinvasion.combat.fleet import normalize_coords
from spaceinvasion.core.state import GameState, is_undersized_fresh_colony
from spaceinvasion.planner.strategy import PlannedAction
from spaceinvasion.runtime.api.handlers._browser_proto import BrowserBridgeFacade
from spaceinvasion.runtime.api.handlers._browser_session import BrowserCommandSession, run_browser_command
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._helpers import (
    parse_command_int,
    safe_api_float,
    safe_api_int,
    sanitize_json_dict,
)


@dataclass(frozen=True)
class AbandonColonyParams:
    """Parameters for `abandon_colony`."""

    planet_id: object = None
    target_coords: object = None
    min_colony_fields: object = None
    dry_run: bool = False
    refresh: bool = False


def aborted_action_response(
    bridge: BrowserBridgeFacade,
    *,
    cmd: str,
    dry_run: bool,
    message: str,
    planned: PlannedAction,
    state: GameState,
    validated: bool | None = None,
) -> dict[str, object]:
    """Build a canonical non-executed aborted fleet response payload."""
    out: dict[str, object] = {
        "ok": True,
        "command": cmd,
        "executed": False,
        "dry_run": dry_run,
        "message": message,
        "action": bridge.serialize_action(planned, state),
        "next_action": None,
    }
    if validated is not None:
        out["validated"] = validated
    return sanitize_json_dict(out)


def lookup_target_slot_and_row(
    bridge: BrowserBridgeFacade,
    norm_target: str,
) -> tuple[Mapping[str, object] | None, Mapping[str, object] | None]:
    """Look up the cached spy target row and freshest galaxy slot for `norm_target`."""
    ctx = bridge.ctx
    target_row: Mapping[str, object] | None = next(
        (
            r
            for r in (ctx.raid_targets or ())
            if normalize_coords(str(r.get("coords") or r.get("target_coords") or "")) == norm_target
        ),
        None,
    )
    if target_row is None and ctx.db is not None:
        found = ctx.db.get_spy_target_by_coords(norm_target)
        if isinstance(found, Mapping):
            target_row = found
    gs_slot: Mapping[str, object] | None = None
    if ctx.db is not None:
        m_gsp = re.match(r"^(\d+):(\d+):(\d+)$", norm_target)
        if m_gsp:
            sys_slots = ctx.db.get_galaxy_slots(galaxy=int(m_gsp.group(1)), system=int(m_gsp.group(2)), limit=20)
            gs_slot = next(
                (
                    s
                    for s in sys_slots
                    if isinstance(s, Mapping) and normalize_coords(str(s.get("coords") or "")) == norm_target
                ),
                None,
            )
    eff_slot = (
        gs_slot
        if (
            gs_slot is not None
            and safe_api_float(gs_slot.get("last_scanned_ts", 0.0))
            >= safe_api_float((target_row or {}).get("last_scanned_ts", 0.0))
        )
        else target_row
    )
    return target_row, eff_slot


def cmd_abandon_colony(bridge: BrowserBridgeFacade, params: AbandonColonyParams) -> dict[str, object]:
    """Handle `abandon_colony` command."""

    def _run(session: BrowserCommandSession) -> dict[str, object]:
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        norm_tc = (
            normalize_coords(params.target_coords.strip())
            if isinstance(params.target_coords, str)
            and re.fullmatch(r"\d{1,2}:\d{1,3}:\d{1,2}", params.target_coords.strip(), re.ASCII)
            else ""
        )
        if pid is None and not norm_tc:
            raise ApiRequestError("'abandon_colony' command requires 'planet_id' or 'target_coords'.")
        min_col_f = parse_command_int(
            "min_colony_fields", params.min_colony_fields, allow_none=True, min_val=50, max_val=1000
        ) or max(50, safe_api_int(bridge.ctx.strategy_config.min_colony_fields, 300))
        state = session.cached_or_fetch(
            include_buildings=False, include_research=False, include_shipyard=False, include_home=False
        )
        if pid is not None:
            planet = bridge.ctx.resolve_planet(state, pid)
        else:
            p_found = next((p for p in state.planets if normalize_coords(p.coords) == norm_tc), None)
            if p_found is None:
                raise ApiRequestError(f"Unknown colony coordinates {norm_tc}.")
            planet = p_found
        safe_ab = is_undersized_fresh_colony(planet, state, min_col_f)
        planned = PlannedAction(
            priority="P1",
            kind="ABANDON_COLONY",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_level=min_col_f,
            target_name=f"Delete colony {planet.coords} ({planet.fields_max}/{min_col_f} fields)",
            reason=(
                f"Delete undersized colony {planet.coords} ({planet.fields_max} < {min_col_f} fields)"
                if safe_ab
                else f"Safety check blocked deleting {planet.coords} ({planet.fields_used}/{planet.fields_max} fields)"
            ),
            executable_now=True,
        )
        if not params.dry_run and not bridge.ctx.live_executor.allow_colonize:
            return bridge.execute_and_summarize_browser(
                "abandon_colony", session.exec_client(planned), state, planned, dry_run=params.dry_run
            )
        if not safe_ab:
            return aborted_action_response(
                bridge,
                cmd="abandon_colony",
                dry_run=params.dry_run,
                message=(
                    f"Aborted safety check: refusing to delete [{planet.coords}] "
                    f"({planet.fields_used}/{planet.fields_max} fields) — Homeworld, developed, "
                    f"stationed fleet/defense, or >= {min_col_f} fields."
                ),
                planned=replace(planned, executable_now=False),
                state=state,
            )
        res_ab = bridge.execute_and_summarize_browser(
            "abandon_colony", session.exec_client(planned), state, planned, dry_run=params.dry_run
        )
        if res_ab.get("executed"):
            ctx = bridge.ctx
            ctx.last_buildings_fetched = False
            ctx.last_shipyard_sync_at = 0.0
            ctx.last_shipyard_sync_sig = None
            ctx.economy_rates.pop(planet.planet_id, None)
            ctx.economy_sync_timestamps.pop(planet.planet_id, None)
            ctx.throttle_timestamps.pop(planet.planet_id, None)
            if ctx.metal_overflow_paused_planets.pop(planet.planet_id, None) is not None:
                bridge.lifecycle.persist_runtime_state()
            ctx.fleet_slot_cooldown_until.pop(planet.planet_id, None)
            ctx.invalidate_dashboard_cache()
            bridge.publish_snapshot()
        return res_ab

    return run_browser_command(bridge, "abandon_colony", {"dry_run": params.dry_run, "refresh": params.refresh}, _run)
