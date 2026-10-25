"""Planet-name sync, single-sector galaxy scan, transmitter activation, and nav registration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from spaceinvasion.core.model import GameState, PlanetState, PlannedAction, homeworld_planet_id
from spaceinvasion.runtime.api.handlers._browser_proto import BrowserBridgeFacade
from spaceinvasion.runtime.api.handlers._browser_session import BrowserCommandSession, run_browser_command
from spaceinvasion.runtime.api.handlers._exec_nav_pages import (
    NavigateParams,
    SyncMessagesParams,
    cmd_navigate,
    cmd_sync_messages,
)
from spaceinvasion.runtime.api.registry import CommandRegistry
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._db_gateway import RuntimeDatabaseGateway
from spaceinvasion.runtime.bridge._helpers import parse_command_int, safe_api_int, sanitize_json_dict
from spaceinvasion.runtime.bridge._helpers_sig import call_with_optional_priority


@dataclass(frozen=True)
class SyncPlanetNamesParams:
    """Parameters for `sync_planet_names`."""

    planet_names: object = None
    planet_id: object = None
    name: object = None
    force: bool = False
    dry_run: bool = False
    refresh: bool = False


@dataclass(frozen=True)
class ScanGalaxyParams:
    """Parameters for `scan_galaxy`."""

    planet_id: object = None
    galaxy: object = None
    system: object = None
    dry_run: bool = False
    refresh: bool = False


@dataclass(frozen=True)
class ActivateTransmitterParams:
    """Parameters for `activate_transmitter`."""

    planet_id: object = None
    dry_run: bool = False
    refresh: bool = False


def _resolve_planet_name_sync_targets(
    bridge: BrowserBridgeFacade,
    params: SyncPlanetNamesParams,
    planets_by_id: Mapping[int, PlanetState],
) -> dict[int, str]:
    single_pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
    targets: dict[int, str] = {}
    if params.planet_names is not None:
        if not isinstance(params.planet_names, Mapping):
            raise ApiRequestError("'sync_planet_names' field 'planet_names' must be a dictionary.")
        batch_local: dict[int, str] = {}
        for raw_pid, raw_nm in params.planet_names.items():
            pid_s = parse_command_int("planet_id", raw_pid, min_val=1, max_val=2**63 - 1)
            if pid_s is None or pid_s not in planets_by_id:
                raise ApiRequestError(f"Planet {raw_pid} does not belong to this empire.")
            if raw_nm is not None and not isinstance(raw_nm, str):
                raise ApiRequestError("Planet names must be strings.")
            clean_s = RuntimeDatabaseGateway.sanitize_custom_planet_name(raw_nm)
            batch_local[int(pid_s)] = clean_s
            if clean_s:
                targets[int(pid_s)] = clean_s
        if batch_local:
            bridge.names.set_custom_planet_names_batch_locked(batch_local)
        return targets

    if single_pid is not None:
        if single_pid not in planets_by_id:
            raise ApiRequestError(f"Planet {single_pid} does not belong to this empire.")
        if params.name is not None:
            if not isinstance(params.name, str):
                raise ApiRequestError("Field 'name' must be a string.")
            bridge.names.set_custom_planet_name_locked(int(single_pid), params.name)
        cur_custom = bridge.names.get_custom_planet_names_locked().get(str(single_pid), "")
        desired = cur_custom or str(planets_by_id[single_pid].name or "")
        if desired:
            targets[int(single_pid)] = desired
        return targets

    for pid_str, c_name in bridge.names.get_custom_planet_names_locked().items():
        pid_i = safe_api_int(pid_str, 0)
        if pid_i <= 0 or pid_i not in planets_by_id or not c_name:
            continue
        g_name = str(planets_by_id[pid_i].name or "").strip()
        if params.force or c_name != g_name:
            targets[pid_i] = str(c_name)
    return targets


def _cmd_sync_planet_names(bridge: BrowserBridgeFacade, params: SyncPlanetNamesParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        snap_state = bridge.get_planner_state_locked()
        if snap_state is None or not snap_state.planets:
            snap_state = session.cached_or_fetch(
                include_buildings=False, include_research=False, include_shipyard=False, include_home=False
            )
        planets_by_id = {int(p.planet_id): p for p in snap_state.planets if int(p.planet_id) > 0}
        targets = _resolve_planet_name_sync_targets(bridge, params, planets_by_id)
        if not targets:
            return sanitize_json_dict(
                {
                    "ok": True,
                    "command": "sync_planet_names",
                    "synced_count": 0,
                    "synced": [],
                    "skipped": [],
                    "custom_planet_names": bridge.names.get_custom_planet_names_locked(),
                    "message": "All planet names in Config Optimizer already match in-game names.",
                }
            )
        if params.dry_run:
            return sanitize_json_dict(
                {
                    "ok": True,
                    "command": "sync_planet_names",
                    "dry_run": True,
                    "synced_count": 0,
                    "pending_targets": {str(k): v for k, v in targets.items()},
                    "custom_planet_names": bridge.names.get_custom_planet_names_locked(),
                    "message": f"Dry run: would sync {len(targets)} planet name(s) via internalSettings.",
                }
            )
        client = session.get_client()
        call_with_optional_priority(client.ensure_in_game, priority="interactive")
        sync_out = bridge.ctx.live_executor.sync_planet_names_in_game(client, snap_state, targets)
        updated_st = sync_out.get("updated_state")
        if isinstance(updated_st, GameState):
            bridge.state_cache.record_state(
                updated_st,
                fetched_buildings=False,
                fetched_research=False,
                fetched_shipyard=False,
                fetched_home=False,
                observed_active_planet=False,
            )
        reconcile_batch: dict[int, str] = {}
        for s_row in sync_out.get("synced") or ():
            if isinstance(s_row, Mapping):
                s_pid = safe_api_int(s_row.get("planet_id"), 0)
                obs_gname = str(s_row.get("game_name") or "").strip()
                if s_pid > 0 and obs_gname:
                    reconcile_batch[s_pid] = obs_gname
        if reconcile_batch:
            bridge.names.set_custom_planet_names_batch_locked(reconcile_batch)
        bridge.ctx.invalidate_dashboard_cache()
        bridge.publish_snapshot()
        return sanitize_json_dict(
            {
                "ok": bool(sync_out.get("ok", True)),
                "command": "sync_planet_names",
                "synced_count": int(sync_out.get("synced_count", 0) or 0),
                "synced": list(sync_out.get("synced") or []),
                "skipped": list(sync_out.get("skipped") or []),
                "custom_planet_names": bridge.names.get_custom_planet_names_locked(),
                "message": str(sync_out.get("message") or "Synced planet names in-game."),
            }
        )

    return run_browser_command(
        bridge, "sync_planet_names", {"dry_run": params.dry_run, "refresh": params.refresh}, _run
    )


def _cmd_scan_galaxy(bridge: BrowserBridgeFacade, params: ScanGalaxyParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        ctx = bridge.ctx
        if ctx.last_state is not None and ctx.last_snapshot_at >= 0.0 and not session.force_refresh:
            state = ctx.last_state
        else:
            live_client = session.get_client()
            snap = bridge.state_cache.read_snapshot(
                live_client,
                include_buildings=False,
                include_research=False,
                include_shipyard=False,
                include_home=False,
                priority="interactive",
            )
            state = bridge.state_cache.record_state(
                snap, fetched_home=False, observed_active_planet=bool(live_client.last_snapshot_observed_active_planet)
            )
        planet = ctx.resolve_planet(state, pid)
        gal_arg = parse_command_int("galaxy", params.galaxy, allow_none=True, min_val=1, max_val=1000)
        sys_arg = parse_command_int("system", params.system, allow_none=True, min_val=1, max_val=1000)
        if gal_arg is not None and sys_arg is not None:
            target_lbl = f"Galaxy [{gal_arg:02d}:{sys_arg:03d}]"
        else:
            parts = [p.strip() for p in planet.coords.split(":")]
            p0 = (parts[0].lstrip("0") or "0") if len(parts) >= 2 else ""
            p1 = (parts[1].lstrip("0") or "0") if len(parts) >= 2 else ""
            if (
                len(parts) >= 2
                and parts[0].isascii()
                and parts[0].isdigit()
                and len(p0) <= 3
                and parts[1].isascii()
                and parts[1].isdigit()
                and len(p1) <= 3
            ):
                target_lbl = f"Galaxy [{max(1, min(999, int(p0))):02d}:{max(1, min(999, int(p1))):03d}]"
            else:
                target_lbl = "Galaxy [01:001]"
        planned = PlannedAction(
            priority="P2",
            kind="SCAN_GALAXY",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_name=target_lbl,
            reason="API scan_galaxy command",
            executable_now=True,
        )
        return bridge.execute_and_summarize_browser(
            "scan_galaxy", session.exec_client(planned), state, planned, dry_run=params.dry_run
        )

    return run_browser_command(bridge, "scan_galaxy", {"dry_run": params.dry_run, "refresh": params.refresh}, _run)


def _cmd_activate_transmitter(bridge: BrowserBridgeFacade, params: ActivateTransmitterParams) -> dict[str, object]:
    def _run(session: BrowserCommandSession) -> dict[str, object]:
        pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
        ctx = bridge.ctx
        if ctx.last_state is not None and ctx.last_snapshot_at >= 0.0 and not session.force_refresh:
            state = ctx.last_state
        else:
            live_client = session.get_client()
            snap = bridge.state_cache.read_snapshot(
                live_client,
                include_buildings=False,
                include_research=False,
                include_shipyard=False,
                include_home=False,
                priority="interactive",
            )
            state = bridge.state_cache.record_state(
                snap, fetched_home=False, observed_active_planet=bool(live_client.last_snapshot_observed_active_planet)
            )
        hw_pid = homeworld_planet_id(state)
        planet = ctx.resolve_planet(state, hw_pid if hw_pid is not None else pid)
        planned = PlannedAction(
            priority="P2",
            kind="ACTIVATE_TRANSMITTER",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_name="Interdimensional Transmitter",
            reason=f"Collect Command Central daily bonus & activate daily free Interdimensional Transmitter spin on Homeworld [{planet.coords}] (0 Urplasma)",
            executable_now=True,
        )
        res_out = bridge.execute_and_summarize_browser(
            "activate_transmitter", session.exec_client(planned), state, planned, dry_run=params.dry_run
        )
        res_out["transmitter_status"] = bridge.get_transmitter_status_locked()
        return res_out

    return run_browser_command(
        bridge, "activate_transmitter", {"dry_run": params.dry_run, "refresh": params.refresh}, _run
    )


def register_nav_handlers(registry: CommandRegistry[BrowserBridgeFacade]) -> None:
    """Register `sync_planet_names`, `navigate`, `scan_galaxy`, `activate_transmitter`, `sync_messages`."""
    registry.command(
        "sync_planet_names",
        needs_browser=True,
        mutates=True,
        summary="Sync custom planet display names to SpaceInvasion internalSettings.",
        params=SyncPlanetNamesParams,
    )(_cmd_sync_planet_names)
    registry.command(
        "navigate",
        needs_browser=True,
        mutates=True,
        summary="Navigate to an allowed in-game menu page and harvest intel side effects.",
        params=NavigateParams,
    )(cmd_navigate)
    registry.command(
        "scan_galaxy",
        needs_browser=True,
        mutates=True,
        summary="Scan a single galaxy sector and persist slots and debris fields.",
        params=ScanGalaxyParams,
    )(_cmd_scan_galaxy)
    registry.command(
        "activate_transmitter",
        needs_browser=True,
        mutates=True,
        summary="Collect daily login bonus and activate the free Interdimensional Transmitter spin.",
        params=ActivateTransmitterParams,
    )(_cmd_activate_transmitter)
    registry.command(
        "sync_messages",
        needs_browser=True,
        mutates=True,
        summary="Ingest spy, battle, fleet, and inbox messages into PostgreSQL and clean processed pages.",
        params=SyncMessagesParams,
    )(cmd_sync_messages)
