"""Economy sync, mine/plant throttling, and tutorial reward claim API handlers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import time

from backend.core import catalog as game_data
from backend.core.model import PlannedAction, normalize_planet_type
from backend.parsing import parse_economy_rates
from backend.runtime.api.handlers._browser_proto import BrowserBridgeFacade
from backend.runtime.api.handlers._browser_session import BrowserCommandSession, run_browser_command
from backend.runtime.api.registry import CommandRegistry
from backend.runtime.api.validation import ApiRequestError
from backend.runtime.bridge._helpers import (
    bounded_api_str,
    parse_command_int,
    safe_api_int,
    sanitize_json_dict,
)


@dataclass(frozen=True)
class SyncEconomyParams:
    """Parameters for `sync_economy`."""

    planet_id: object = None
    refresh: bool = False


@dataclass(frozen=True)
class ThrottleBuildingParams:
    """Parameters for `throttle_building`."""

    building_id: object = 2
    percentage: object = None
    planet_id: object = None
    reason: object = None
    dry_run: bool = False
    refresh: bool = False


@dataclass(frozen=True)
class ClaimTutorialParams:
    """Parameters for `claim_tutorial`."""

    dry_run: bool = False
    refresh: bool = False


def _cmd_sync_economy(bridge: BrowserBridgeFacade, params: SyncEconomyParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        state = session.cached_or_fetch(
            include_buildings=False,
            include_research=False,
            include_shipyard=False,
            include_home=False,
            priority="normal",
        )
        ctx = bridge.ctx
        planet = ctx.resolve_planet(state, pid)
        ptype_norm = normalize_planet_type(planet)
        if ptype_norm in ("asteroid", "raid"):
            return sanitize_json_dict(
                {
                    "ok": True,
                    "command": "sync_economy",
                    "planet_id": planet.planet_id,
                    "economy_rates": {},
                    "blast_furnace_pct": 0,
                    "metal_production_enabled": False,
                    "state": asdict(bridge.attach_raid_stats(state)),
                }
            )
        client = session.get_client()
        e_html = ctx.live_executor._navigate_and_select_planet(client, state, "internalEconomy", planet.planet_id)
        rates = parse_economy_rates(e_html)
        if rates:
            ctx.economy_rates[planet.planet_id] = dict(rates)
            ctx.economy_sync_timestamps[planet.planet_id] = time.time()
            if ctx.db is not None:
                ctx.db.save_economy_rates(planet.planet_id, planet.coords, rates)
        synced_state = bridge.state_cache.refresh_cached_economy(ctx.last_state or state)
        bf_pct = safe_api_int((ctx.economy_rates.get(planet.planet_id) or rates or {}).get("resource2", 100), 100)
        bridge.publish_snapshot()
        return sanitize_json_dict(
            {
                "ok": True,
                "command": "sync_economy",
                "planet_id": planet.planet_id,
                "economy_rates": dict(ctx.economy_rates.get(planet.planet_id) or rates or {}),
                "blast_furnace_pct": bf_pct,
                "metal_production_enabled": bf_pct > 0,
                "state": asdict(bridge.attach_raid_stats(synced_state)),
            }
        )

    return run_browser_command(bridge, "sync_economy", {"refresh": params.refresh}, _run)


def _cmd_throttle_building(bridge: BrowserBridgeFacade, params: ThrottleBuildingParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        bid = parse_command_int("building_id", params.building_id, min_val=1, max_val=2**31 - 1)
        assert bid is not None
        if params.percentage is None:
            raise ApiRequestError("'throttle_building' command requires 'percentage' (0..100).")
        pct_raw = parse_command_int("percentage", params.percentage, min_val=0, max_val=100)
        assert pct_raw is not None
        pct = max(0, min(100, (pct_raw // 10) * 10))
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        state = session.cached_or_fetch(
            include_buildings=False, include_research=False, include_shipyard=False, include_home=False
        )
        planet = bridge.ctx.resolve_planet(state, pid)
        ptype_norm = normalize_planet_type(planet)
        bid_allowed = ptype_norm not in ("asteroid", "raid") and bid in (1, 2, 3, 4, 5, 6)
        label = "Blast furnace" if bid == 2 else planet.building_names.get(f"b{bid}", f"Building b{bid}")
        if ptype_norm in ("asteroid", "raid"):
            t_reason = f"Throttle not permitted on planet_type={ptype_norm}"
        elif bid not in (1, 2, 3, 4, 5, 6):
            t_reason = f"Building b{bid} does not support internalEconomy rate control"
        elif isinstance(params.reason, str) and params.reason.strip():
            t_reason = bounded_api_str(
                params.reason.strip(), default=f"Set {label} rate to {pct}% in internalEconomy", max_len=300
            )
        else:
            t_reason = f"Set {label} rate to {pct}% in internalEconomy"
        planned = PlannedAction(
            priority="P1",
            kind="THROTTLE_BUILDING",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=bid,
            target_name=f"{label} {pct}%",
            target_level=pct,
            reason=t_reason,
            executable_now=bid_allowed,
        )
        return bridge.execute_and_summarize_browser(
            "throttle_building", session.exec_client(planned), state, planned, dry_run=params.dry_run
        )

    return run_browser_command(
        bridge, "throttle_building", {"dry_run": params.dry_run, "refresh": params.refresh}, _run
    )


def _cmd_claim_tutorial(bridge: BrowserBridgeFacade, params: ClaimTutorialParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        state = session.cached_or_fetch(
            include_buildings=False, include_research=False, include_shipyard=False, include_home=True
        )
        if not (state.tutorial and state.tutorial.can_complete):
            return sanitize_json_dict(
                {
                    "ok": True,
                    "command": "claim_tutorial",
                    "executed": False,
                    "dry_run": params.dry_run,
                    "message": "Tutorial step is not ready to claim.",
                }
            )
        planet = state.active_planet()
        planned = PlannedAction(
            priority="P2",
            kind="CLAIM_TUTORIAL",
            planet_id=planet.planet_id if planet else 0,
            planet_coords=planet.coords if planet else "",
            target_name=(
                f"Tutorial {game_data.fmt_compact_int(state.tutorial.step)}"
                f"/{game_data.fmt_compact_int(state.tutorial.total_steps)} Reward"
            ),
            reason="API claim_tutorial command",
            executable_now=True,
        )
        return bridge.execute_and_summarize_browser(
            "claim_tutorial", session.exec_client(planned), state, planned, dry_run=params.dry_run, return_home=True
        )

    return run_browser_command(bridge, "claim_tutorial", {"dry_run": params.dry_run, "refresh": params.refresh}, _run)


def register_economy_handlers(registry: CommandRegistry[BrowserBridgeFacade]) -> None:
    """Register `sync_economy`, `throttle_building`, and `claim_tutorial`."""
    registry.command(
        "sync_economy",
        needs_browser=True,
        mutates=True,
        summary="Read live internalEconomy production percentages for a planet.",
        params=SyncEconomyParams,
    )(_cmd_sync_economy)
    registry.command(
        "throttle_building",
        needs_browser=True,
        mutates=True,
        summary="Adjust a mine or power plant production percentage (0..100 in steps of 10).",
        params=ThrottleBuildingParams,
    )(_cmd_throttle_building)
    registry.command(
        "claim_tutorial",
        needs_browser=True,
        mutates=True,
        summary="Claim a completed SpaceInvasion tutorial step reward.",
        params=ClaimTutorialParams,
    )(_cmd_claim_tutorial)
