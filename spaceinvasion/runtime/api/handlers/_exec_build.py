"""Building, demolition, research, and shipyard API handlers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math

from spaceinvasion.core import game_data
from spaceinvasion.core.state import GameState, normalize_planet_type
from spaceinvasion.planner.strategy import PlannedAction
from spaceinvasion.runtime.api.handlers._browser_proto import BrowserBridgeFacade
from spaceinvasion.runtime.api.handlers._browser_session import BrowserCommandSession, run_browser_command
from spaceinvasion.runtime.api.registry import CommandRegistry
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._helpers import parse_command_int, safe_api_int


@dataclass(frozen=True)
class BuildParams:
    """Parameters for `build`."""

    building_id: object = None
    planet_id: object = None
    dry_run: bool = False
    refresh: bool = False


@dataclass(frozen=True)
class DemolishBuildingParams:
    """Parameters for `demolish_building`."""

    building_id: object = 6
    planet_id: object = None
    dry_run: bool = False
    refresh: bool = False


@dataclass(frozen=True)
class ResearchParams:
    """Parameters for `research`."""

    research_id: object = None
    planet_id: object = None
    dry_run: bool = False
    refresh: bool = False


@dataclass(frozen=True)
class BuildShipParams:
    """Parameters for `build_ship`."""

    ship_id: object = None
    quantity: object = 1
    planet_id: object = None
    dry_run: bool = False
    refresh: bool = False


def _build_demolish_action(bridge: BrowserBridgeFacade, state: GameState, bid: int, pid: int | None) -> PlannedAction:
    planet = bridge.ctx.resolve_planet(state, pid)
    binfo = game_data.BUILDING_BY_ID.get(bid)
    bname = str(binfo.get("name") or f"Building {bid}") if isinstance(binfo, Mapping) else f"Building {bid}"
    cur_lvl = planet.building_level(bid)
    ptype_norm = normalize_planet_type(planet)
    active_p = state.active_planet()
    is_active_p = active_p is not None and planet.planet_id == active_p.planet_id
    specs_map = state.specs if isinstance(state.specs, Mapping) else {}
    default_slots = (
        1 if ptype_norm == "asteroid" else (6 if safe_api_int(specs_map.get("construction_commander", 0), 0) > 0 else 5)
    )
    max_slots = max(1, safe_api_int(specs_map.get("max_build_slots", default_slots), default_slots))
    planet_b_jobs = [q for q in (state.active_queues or ()) if q.category == "building" and is_active_p]
    q_busy = len(planet_b_jobs) >= max_slots
    b_in_queue = any(
        game_data.safe_optional_int(q.building_id, None) == bid
        or bname.lower() in str(q.name or "").lower()
        or (bid == 6 and "thermal" in str(q.name or "").lower())
        for q in planet_b_jobs
    )
    can_dem = True
    if cur_lvl < 1:
        can_dem, reason = False, f"{bname} is already at Level 0"
    elif ptype_norm in ("asteroid", "raid"):
        can_dem, reason = False, f"Building demolition unavailable on {ptype_norm}"
    elif q_busy or b_in_queue:
        can_dem, reason = False, "Building queue busy"
    else:
        reason = f"Demolish {bname} L{cur_lvl} -> L{cur_lvl - 1} to reclaim 1 field"
        raw_loss = max(0, game_data.building_energy_delta(bid, cur_lvl))
        if raw_loss > 0:
            res_map = state.researches if isinstance(state.researches, Mapping) else {}
            r2 = max(0, min(200, safe_api_int(res_map.get("r2", 0), 0)))
            safe_loss = int(math.ceil(raw_loss * (1.0 + 0.01 * r2)))
            throttled_res = 0
            econ_map = planet.economy_rates if isinstance(planet.economy_rates, Mapping) else {}
            for mbid, rkey in ((1, "resource1"), (2, "resource2"), (3, "resource3"), (4, "resource4")):
                mlvl = planet.building_level(mbid)
                if mlvl <= 0:
                    continue
                mpct = max(0, min(100, safe_api_int(econ_map.get(rkey, 100), 100)))
                if mpct < 100:
                    throttled_res += max(
                        0, int(math.ceil(game_data.mine_energy_needed(mbid, mlvl) * (100 - mpct) / 100.0))
                    )
            if safe_api_int(planet.energy.net, 0) - throttled_res - safe_loss < 0:
                can_dem = False
                reason = f"Insufficient spare energy to demolish {bname} safely (upgrade Fusion plant first)"
    return PlannedAction(
        priority="P1",
        kind="DEMOLISH_BUILDING",
        planet_id=planet.planet_id,
        planet_coords=planet.coords,
        target_id=bid,
        target_name=bname,
        target_level=max(0, cur_lvl - 1),
        reason=reason,
        executable_now=can_dem,
    )


def _cmd_build(bridge: BrowserBridgeFacade, params: BuildParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        if params.building_id is None:
            raise ApiRequestError("'build' command requires 'building_id'.")
        bid = parse_command_int("building_id", params.building_id, min_val=1, max_val=2**31 - 1)
        assert bid is not None
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        state = session.cached_or_fetch(
            include_buildings=bool(params.dry_run or session.force_refresh),
            include_research=False,
            include_shipyard=False,
            include_home=False,
        )
        planned = bridge.find_build_action(state, bid, pid, allow_live_verify=not params.dry_run)
        return bridge.execute_and_summarize_browser(
            "build", session.exec_client(planned), state, planned, dry_run=params.dry_run
        )

    return run_browser_command(bridge, "build", {"dry_run": params.dry_run, "refresh": params.refresh}, _run)


def _cmd_demolish(bridge: BrowserBridgeFacade, params: DemolishBuildingParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        bid = parse_command_int("building_id", params.building_id, min_val=1, max_val=2**31 - 1)
        assert bid is not None
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        state = session.cached_or_fetch(
            include_buildings=bool(session.force_refresh or (params.dry_run and bridge.ctx.last_state is None)),
            include_research=False,
            include_shipyard=False,
            include_home=False,
        )
        planned = _build_demolish_action(bridge, state, bid, pid)
        return bridge.execute_and_summarize_browser(
            "demolish_building", session.exec_client(planned), state, planned, dry_run=params.dry_run
        )

    return run_browser_command(
        bridge, "demolish_building", {"dry_run": params.dry_run, "refresh": params.refresh}, _run
    )


def _cmd_research(bridge: BrowserBridgeFacade, params: ResearchParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        if params.research_id is None:
            raise ApiRequestError("'research' command requires 'research_id'.")
        rid = parse_command_int("research_id", params.research_id, min_val=1, max_val=2**31 - 1)
        assert rid is not None
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        state = session.cached_or_fetch(
            include_buildings=False,
            include_research=bool(params.dry_run or session.force_refresh),
            include_shipyard=False,
            include_home=False,
        )
        planned = bridge.find_research_action_by_id(state, rid, pid, allow_live_verify=not params.dry_run)
        return bridge.execute_and_summarize_browser(
            "research", session.exec_client(planned), state, planned, dry_run=params.dry_run
        )

    return run_browser_command(bridge, "research", {"dry_run": params.dry_run, "refresh": params.refresh}, _run)


def _cmd_build_ship(bridge: BrowserBridgeFacade, params: BuildShipParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        if params.ship_id is None:
            raise ApiRequestError("'build_ship' command requires 'ship_id'.")
        sid_int = parse_command_int("ship_id", params.ship_id, min_val=1, max_val=2**31 - 1)
        assert sid_int is not None
        qty = parse_command_int("quantity", params.quantity, min_val=1, max_val=1_000_000_000)
        assert qty is not None
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        state = session.cached_or_fetch(
            include_buildings=False,
            include_research=False,
            include_shipyard=bool(params.dry_run or session.force_refresh),
            include_home=False,
        )
        planned = bridge.find_ship_action_by_id(state, sid_int, qty, pid, allow_live_verify=not params.dry_run)
        return bridge.execute_and_summarize_browser(
            "build_ship", session.exec_client(planned), state, planned, dry_run=params.dry_run
        )

    return run_browser_command(bridge, "build_ship", {"dry_run": params.dry_run, "refresh": params.refresh}, _run)


def register_build_handlers(registry: CommandRegistry[BrowserBridgeFacade]) -> None:
    """Register `build`, `demolish_building`, `research`, and `build_ship`."""
    registry.command(
        "build",
        needs_browser=True,
        mutates=True,
        summary="Queue or start a building upgrade on the specified planet.",
        params=BuildParams,
    )(_cmd_build)
    registry.command(
        "demolish_building",
        needs_browser=True,
        mutates=True,
        summary="Safely demolish one level of a building to reclaim planet fields.",
        params=DemolishBuildingParams,
    )(_cmd_demolish)
    registry.command(
        "research",
        needs_browser=True,
        mutates=True,
        summary="Start a research upgrade on the specified planet.",
        params=ResearchParams,
    )(_cmd_research)
    registry.command(
        "build_ship",
        needs_browser=True,
        mutates=True,
        summary="Commission ships in the planet's Weapons Factory.",
        params=BuildShipParams,
    )(_cmd_build_ship)
