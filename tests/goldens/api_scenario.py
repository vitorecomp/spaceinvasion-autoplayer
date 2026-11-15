"""Scripted API session against the fake game for the golden suite.

One `E2ETestHarness` (fake BitMeUp server, temporary database, ephemeral HTTP port) is
driven through a fixed list of `POST /command` requests on a frozen clock. Every
response becomes one reference file. The session covers the commands that survive
Decision D5 of the refactor plan; it never sends `ga_nn` or one of the 12 removed
commands (see `REMOVED_COMMANDS`).
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any, Callable, Optional
from unittest import mock

import requests

from backend.db import config_store as config_store_mod
from backend.core.config import StrategyConfig
from backend.parsing import overview as overview_mod, queues as queues_mod
from tests.e2e.harness import E2ETestHarness, FakeClock, FakeSpaceInvasionServer, _ClockTimeProxy
from tests.goldens.scenarios import GOLDEN_EPOCH, NICKNAME, SID, UNIVERSE_ID, USER_ID

# Decision D5: these commands have no sender and are deleted in phase 1. Never golden them.
REMOVED_COMMANDS = frozenset(
    {
        "bot_resume",
        "db_backend_status",
        "db_backup",
        "db_migrate",
        "db_migrations",
        "game_messages",
        "menu",
        "queue_list",
        "ranking_evolution",
        "restore_colony_candidate",
        "state_snapshots",
        "sync_spy_reports",
    }
)

HOME_ID = 20001
FORGE_ID = 20002
OUTPOST_ID = 20003


class FrozenClock(FakeClock):
    """Virtual clock that moves only when code sleeps on it or a step advances it.

    `FakeClock` adds real elapsed time to every reading, which makes timestamps differ
    from run to run. Here a reading depends on nothing but the sleeps that preceded it.
    """

    def monotonic(self) -> float:
        with self._lock:
            return self._base + self._virtual_offset


def _spy_href(gal: int, sun: int, pla: int) -> str:
    return f"indexInternal.es?action=internalFleet&mission=spio&gal={gal}&sun={sun}&pla={pla}"


def _slot(
    pos: int, name: str, owner: str, color: str, *, sector: tuple[int, int], debris: bool = False
) -> dict[str, Any]:
    own = owner == NICKNAME
    return {
        "position": pos,
        "name": name,
        "owner": owner,
        "status_color": color,
        "has_debris": debris,
        "is_spacia": owner == "Spacia",
        "spy_href": None if own else _spy_href(sector[0], sector[1], pos),
    }


def build_planets() -> dict[int, dict[str, Any]]:
    full_rates = {f"resource{i}": 100 for i in range(1, 6)}
    return {
        HOME_ID: {
            "planet_id": HOME_ID,
            "name": "Golden Prime",
            "coords": "03:120:07",
            "planet_type": "normal",
            "temperature": 24,
            "fields_max": 262,
            "resources": [182_000, 96_000, 74_000, 31_000],
            "storage": [400_000, 400_000, 400_000, 400_000],
            "energy": {"produced": 2_400, "used": 1_650},
            "buildings": {"b1": 12, "b2": 9, "b3": 10, "b4": 8, "b5": 13, "b7": 8, "b8": 7, "b9": 5, "b10": 4},
            "ships": {"s1": 12, "s2": 4, "s4": 30, "s5": 6, "s10": 8, "s11": 3},
            "defense": {"d1": 12, "d2": 4},
            "economy_rates": dict(full_rates),
            "building_queue": [],
            "building_offers": None,
            "ship_offers": None,
        },
        FORGE_ID: {
            "planet_id": FORGE_ID,
            "name": "Golden Forge",
            "coords": "03:120:11",
            "planet_type": "normal",
            "temperature": -18,
            "fields_max": 301,
            "resources": [54_000, 18_500, 22_000, 6_400],
            "storage": [400_000, 400_000, 400_000, 400_000],
            "energy": {"produced": 1_300, "used": 900},
            "buildings": {"b1": 9, "b2": 6, "b3": 7, "b4": 5, "b5": 10, "b7": 5, "b9": 2, "b10": 2},
            "ships": {"s1": 3, "s10": 2},
            "defense": {},
            "economy_rates": dict(full_rates),
            "building_queue": [],
            "building_offers": None,
            "ship_offers": None,
        },
        OUTPOST_ID: {
            "planet_id": OUTPOST_ID,
            "name": "Golden Outpost",
            "coords": "03:121:04",
            "planet_type": "normal",
            "temperature": 61,
            "fields_max": 188,
            "resources": [9_800, 1_200, 5_100, 900],
            "storage": [150_000, 150_000, 150_000, 150_000],
            "energy": {"produced": 420, "used": 260},
            "buildings": {"b1": 5, "b3": 4, "b4": 2, "b5": 6, "b7": 2},
            "ships": {},
            "defense": {},
            "economy_rates": dict(full_rates),
            "building_queue": [],
            "building_offers": None,
            "ship_offers": None,
        },
    }


def build_galaxy() -> dict[tuple[int, int], list[dict[str, Any]]]:
    return {
        (3, 120): [
            _slot(3, "Quiet Rock", "DormantOne", "yellow", sector=(3, 120)),
            _slot(7, "Golden Prime", NICKNAME, "white", sector=(3, 120)),
            _slot(9, "Spacia Planet", "Spacia", "yellow", sector=(3, 120), debris=True),
            _slot(11, "Golden Forge", NICKNAME, "white", sector=(3, 120)),
            _slot(14, "Bastion", "WatchfulRival", "light_red", sector=(3, 120)),
        ],
        (3, 121): [
            _slot(4, "Golden Outpost", NICKNAME, "white", sector=(3, 121)),
            _slot(6, "Dusty Moon", "SleeperTwo", "yellow", sector=(3, 121)),
            _slot(12, "Spacia Planet", "Spacia", "yellow", sector=(3, 121), debris=True),
        ],
        (3, 122): [
            _slot(5, "Far Colony", "SleeperThree", "yellow", sector=(3, 122), debris=True),
        ],
    }


def build_spy_reports() -> dict[str, dict[str, Any]]:
    return {
        "03:120:03": {
            "owner": "DormantOne",
            "resources": {"pig_iron": 64_000, "metal": 31_000, "kryptonite": 22_000, "spice": 9_000},
            "fleet": {},
            "defense": {"d1": 2},
            "dug_in": False,
            "is_spacia": False,
        },
        "03:120:09": {
            "owner": "Spacia",
            "resources": {"pig_iron": 120_000, "metal": 80_000, "kryptonite": 40_000, "spice": 15_000},
            "fleet": {"s4": 2},
            "defense": {"d1": 25, "d2": 6},
            "dug_in": True,
            "is_spacia": True,
        },
    }


def build_server(clock: FakeClock) -> FakeSpaceInvasionServer:
    return FakeSpaceInvasionServer(
        clock=clock,
        universe_id=UNIVERSE_ID,
        user_id=USER_ID,
        nickname=NICKNAME,
        sid=SID,
        speedup_factor=4.0,
        newbie_protection=False,
        account_points=18_450,
        account_rank=412,
        planets=build_planets(),
        active_planet_id=HOME_ID,
        researches={"r1": 4, "r2": 5, "r3": 3, "r4": 4, "r5": 2, "r6": 3, "r7": 2},
        unread_messages={"total": 0, "spio": 0},
        galaxy_systems=build_galaxy(),
        spy_reports=build_spy_reports(),
    )


def build_config() -> StrategyConfig:
    # max_planets equals the colony count, so the live AI-Planner gate is open once a path exists.
    return StrategyConfig(
        planner_mode="priority",
        playstyle="hybrid",
        allow_fleetsave=True,
        allow_galaxy_scan=True,
        allow_raids=True,
        allow_transport=True,
        max_planets=3,
    )


@dataclass(frozen=True)
class ApiStep:
    """One request of the session. `case` is the reference file stem."""

    case: str
    command: Optional[dict[str, Any]] = None
    get: Optional[str] = None
    before: Optional[Callable[[FakeSpaceInvasionServer, FakeClock], None]] = None


def _tutorial_open(server: FakeSpaceInvasionServer, _clock: FakeClock) -> None:
    server.tutorial = {
        "step": 3,
        "total_steps": 11,
        "displayed": True,
        "tasks": [
            {"description": "Increase your Pig iron mine level 13 / 14"},
            {"description": "Increase your Kryptonite mine level 10 / 11"},
        ],
        "reward_text": "5232 Pig iron, 900 Metal, 2758 Kryptonite, 200 Spice",
        "can_complete": False,
    }


def _tutorial_claimable(server: FakeSpaceInvasionServer, _clock: FakeClock) -> None:
    server.tutorial = {
        "step": 3,
        "total_steps": 11,
        "displayed": True,
        "tasks": [{"description": "Increase your Pig iron mine level 14 / 14"}],
        "reward_text": "5232 Pig iron, 900 Metal, 2758 Kryptonite, 200 Spice",
        "can_complete": True,
    }


def _hostile(server: FakeSpaceInvasionServer, _clock: FakeClock) -> None:
    server.tutorial = None
    server.hostile_alerts = ["Hostile attack fleet approaching [03:120:07]"]


def _cmd(case: str, command: str, **payload: Any) -> ApiStep:
    return ApiStep(case=case, command={"command": command, **payload})


_SMALL_CONFIG_OPTIMIZER_PARAMS: dict[str, Any] = {
    "catalogue_grid_size": 64,
    "catalogue_priority_steps": 2,
    "hill_climb_step_sizes": [1, 2],
    "ga_population_size": 8,
    "ga_generations": 3,
}
_SMALL_AI_PLANNER_PARAMS: dict[str, Any] = {
    "reward_weeks": 2,
    "ga_population_size": 4,
    "ga_generations": 2,
    "ga_random_seed": 20261120,
}

API_STEPS: tuple[ApiStep, ...] = (
    # Session, configuration and read models on a cold bridge.
    _cmd("001_ensure_session", "ensure_session"),
    _cmd("002_config", "config"),
    _cmd("003_plan_priority", "plan", dry_run=True),
    _cmd("003b_plan_priority_cached", "plan", dry_run=True, refresh=False),
    _cmd("004_plan_ai_planner_fallback", "plan", dry_run=True, planner_mode="ai_planner"),
    _cmd("005_action_graph_priority", "action_graph", dry_run=True),
    _cmd(
        "006_action_graph_simulated_planet",
        "action_graph",
        dry_run=True,
        simulated=True,
        planet_id=FORGE_ID,
        config={"allow_raids": False, "playstyle": "miner"},
    ),
    _cmd("007_buildings_eta", "buildings_eta"),
    _cmd("008_buildings_eta_colony", "buildings_eta", planet_id=FORGE_ID),
    _cmd("009_combat_overview", "combat_overview"),
    _cmd("010_fleetsave_plan", "fleetsave_plan", dry_run=True),
    _cmd(
        "011_simulate_combat",
        "simulate_combat",
        attacker_ships={"s4": 30, "s5": 6},
        defender_ships={"s4": 2},
        defender_defense={"d1": 25, "d2": 6},
        target_resources={"pig_iron": 120_000, "metal": 80_000, "kryptonite": 40_000, "spice": 15_000},
        target_coords="03:120:09",
    ),
    _cmd("012_planner_jobs_status_idle", "planner_jobs_status"),
    _cmd("013_notifications_list_empty", "notifications_list"),
    # Dry runs of every executor command: nothing may reach the fake game.
    _cmd("020_tick_once_dry", "tick_once", dry_run=True),
    _cmd("021_build_dry", "build", dry_run=True, building_id=1),
    _cmd("022_build_colony_dry", "build", dry_run=True, building_id=3, planet_id=FORGE_ID),
    _cmd("023_research_dry", "research", dry_run=True, research_id=1),
    _cmd("024_build_ship_dry", "build_ship", dry_run=True, ship_id=10, quantity=2),
    _cmd("025_demolish_building_dry", "demolish_building", dry_run=True, building_id=4, planet_id=OUTPOST_ID),
    _cmd("026_throttle_building_dry", "throttle_building", dry_run=True, building_id=2, percentage=50),
    _cmd("027_sync_economy", "sync_economy", dry_run=True),
    _cmd("028_claim_tutorial_dry", "claim_tutorial", dry_run=True),
    _cmd("029_navigate_buildings", "navigate", dry_run=True, action="internalBuildings"),
    _cmd("030_scan_galaxy_dry", "scan_galaxy", dry_run=True, galaxy=3, system=120),
    _cmd("031_spy_dry", "spy", dry_run=True, target_coords="03:120:03"),
    _cmd("032_attack_dry", "attack", dry_run=True, target_coords="03:120:03"),
    _cmd("033_recycle_dry", "recycle", dry_run=True, target_coords="03:120:09"),
    _cmd("034_colonize_dry", "colonize", dry_run=True, target_coords="03:120:02"),
    _cmd(
        "035_transport_dry",
        "transport",
        dry_run=True,
        dest_planet_id=FORGE_ID,
        resources={"pig_iron": 20_000, "metal": 5_000},
    ),
    _cmd("036_station_dry", "station", dry_run=True, dest_planet_id=FORGE_ID, ships={"s4": 5}),
    _cmd("037_abandon_colony_dry", "abandon_colony", dry_run=True, planet_id=OUTPOST_ID),
    _cmd("038_escape_flight_dry", "escape_flight", dry_run=True),
    _cmd("039_activate_transmitter_dry", "activate_transmitter", dry_run=True),
    _cmd("040_sync_messages_dry", "sync_messages", dry_run=True),
    _cmd("041_sync_planet_names_dry", "sync_planet_names", dry_run=True),
    _cmd("042_galaxy_sweep_dry", "galaxy_sweep", dry_run=True, pages=6),
    # A few live commands against the fake game, so the read models below have rows to show.
    _cmd("050_scan_galaxy_live_120", "scan_galaxy", galaxy=3, system=120),
    _cmd("051_scan_galaxy_live_121", "scan_galaxy", galaxy=3, system=121),
    _cmd("052_scan_galaxy_live_122", "scan_galaxy", galaxy=3, system=122),
    _cmd("053_spy_live", "spy", target_coords="03:120:03"),
    _cmd("054_sync_messages_live", "sync_messages"),
    _cmd("055_build_live", "build", building_id=1),
    _cmd("056_rename_planet", "rename_planet", planet_id=OUTPOST_ID, name="Outpost Two"),
    _cmd("060_espionage_intel", "espionage_intel"),
    _cmd("061_scanned_sectors", "scanned_sectors", galaxy=3, include_unscanned=False),
    _cmd("062_colony_candidates", "colony_candidates"),
    _cmd("063_discard_colony_candidate", "discard_colony_candidate", target_coords="03:120:02"),
    _cmd("064_universe_research_max", "universe_research_max"),
    _cmd("065_attack_results", "attack_results"),
    _cmd("066_recycler_results", "recycler_results"),
    _cmd("067_fleet_planner_get", "fleet_planner_get"),
    _cmd("068_plan_priority_after_scans", "plan", dry_run=True),
    _cmd("069_combat_overview_after_scans", "combat_overview", refresh=True),
    # Manual queue, bot control, settings and notifications.
    _cmd("070_queue_add_build", "queue_add", kind="BUILD", planet_id=HOME_ID, target_id=3),
    _cmd(
        "071_queue_add_ship", "queue_add", kind="BUILD_SHIP", planet_id=HOME_ID, target_id=1, quantity=4, priority="P2"
    ),
    _cmd("072_queue_update", "queue_update", queue_id=2, priority="P1", note="golden"),
    _cmd("073_plan_with_manual_queue", "plan", dry_run=True),
    _cmd("074_queue_cancel", "queue_cancel", queue_id=1),
    _cmd("075_queue_clear", "queue_clear"),
    _cmd("076_bot_control_pause", "bot_control", paused=True),
    _cmd("077_tick_once_paused", "tick_once", dry_run=True),
    _cmd("078_bot_control_resume", "bot_control", paused=False),
    _cmd("079_update_config", "update_config", config={"raid_min_loot_spice_eq": 2500.0, "reserved_spy_slots": 2}),
    _cmd("080_notifications_ack_all", "notifications_ack_all"),
    _cmd("081_notification_get_missing", "notification_get", notification_id=1),
    _cmd("082_notification_ack_missing", "notification_ack", notification_id=1),
    # Config Optimizer and AI-Planner with small fixed budgets, run synchronously.
    _cmd("090_config_optimizer_get_empty", "config_optimizer_get"),
    _cmd("091_ai_planner_get_empty", "ai_planner_get"),
    _cmd(
        "092_config_optimizer_run",
        "config_optimizer_run",
        sync=True,
        auto_replan_ai=False,
        params=_SMALL_CONFIG_OPTIMIZER_PARAMS,
    ),
    _cmd("093_config_optimizer_get", "config_optimizer_get"),
    _cmd("094_config_optimizer_yearly_projection", "config_optimizer_yearly_projection", horizon_years=1),
    _cmd(
        "095_config_optimizer_set_specializations",
        "config_optimizer_set_specializations",
        planet_specializations={},
    ),
    _cmd(
        "095b_config_optimizer_update_planet",
        "config_optimizer_update_planet",
        planet_id=OUTPOST_ID,
        target_buildings={"b1": 12, "b3": 10, "b5": 12},
        auto_replan_ai=False,
    ),
    _cmd(
        "096_ai_planner_run",
        "ai_planner_run",
        sync=True,
        auto_run_config_if_missing=False,
        params=_SMALL_AI_PLANNER_PARAMS,
    ),
    _cmd("097_ai_planner_get", "ai_planner_get", page_size=25),
    _cmd("098_plan_ai_planner", "plan", dry_run=True, planner_mode="ai_planner"),
    _cmd("099_action_graph_ai_planner", "action_graph", dry_run=True, planner_mode="ai_planner"),
    _cmd("100_planner_jobs_status_done", "planner_jobs_status"),
    _cmd(
        "101_resolve_field_conflict_missing",
        "resolve_field_conflict",
        conflict_key="20001:b1",
        decision="keep_reserve",
    ),
    # Tutorial branch: first with open tasks, then with the reward ready to claim.
    ApiStep(
        case="104_plan_tutorial_open",
        command={"command": "plan", "dry_run": True, "refresh": True},
        before=_tutorial_open,
    ),
    ApiStep(
        case="105_plan_tutorial_claimable",
        command={"command": "plan", "dry_run": True, "refresh": True},
        before=_tutorial_claimable,
    ),
    _cmd("106_claim_tutorial_ready_dry", "claim_tutorial", dry_run=True),
    _cmd("107_tick_once_tutorial_dry", "tick_once", dry_run=True),
    # Hostile fleet inbound: the P0 branch.
    ApiStep(
        case="110_plan_hostile",
        command={"command": "plan", "dry_run": True, "refresh": True},
        before=_hostile,
    ),
    _cmd("111_tick_once_hostile_dry", "tick_once", dry_run=True),
    _cmd("112_escape_flight_hostile_dry", "escape_flight", dry_run=True),
    # Error contract and diagnostics.
    _cmd("120_unknown_command", "no_such_command"),
    _cmd("121_build_missing_argument", "build", dry_run=True),
    _cmd("130_db_summary", "db_summary"),
    _cmd("131_db_verify", "db_verify"),
    ApiStep(case="140_get_health", get="/health"),
    ApiStep(case="141_get_dashboard", get="/dashboard?refresh=0"),
)


def _fake_game_side_effects(server: FakeSpaceInvasionServer) -> dict[str, Any]:
    """What the session did to the fake game: the proof that dry runs stay dry."""
    views: dict[str, int] = {}
    for view in server.page_view_log:
        key = f"{view.get('frame')}:{view.get('action')}"
        views[key] = views.get(key, 0) + 1
    return {
        "action_log": list(server.action_log),
        "launched_fleets": list(server.launched_fleets),
        "page_views_total": len(server.page_view_log),
        "page_views_by_frame_and_action": views,
        "concurrent_render_collisions": server.concurrent_render_collisions,
    }


def _send(
    http: requests.Session,
    base_url: str,
    step: ApiStep,
    server: FakeSpaceInvasionServer,
    clock: FakeClock,
    timeout: float,
) -> dict[str, Any]:
    if step.before is not None:
        step.before(server, clock)
    if step.get is not None:
        resp = http.get(f"{base_url}{step.get}", timeout=timeout)
        request: dict[str, Any] = {"method": "GET", "path": step.get}
    else:
        resp = http.post(f"{base_url}/command", json=step.command, timeout=timeout)
        request = {"method": "POST", "path": "/command", "json": step.command}
    try:
        body: Any = resp.json()
    except ValueError:
        body = {"__non_json_body__": resp.text[:2000]}
    return {"request": request, "http_status": resp.status_code, "response": body}


def run_api_session(*, timeout: float = 600.0) -> dict[str, Any]:
    """Run `API_STEPS` once and return `{case: {"request", "http_status", "response"}}` in step order."""
    for step in API_STEPS:
        if step.command is not None and step.command.get("command") in REMOVED_COMMANDS:
            raise AssertionError(f"{step.case} uses a command removed by Decision D5")

    clock = FrozenClock(GOLDEN_EPOCH)
    server = build_server(clock)
    out: dict[str, Any] = {}
    http = requests.Session()
    http.trust_env = False
    harness = E2ETestHarness(server=server, clock=clock, config=build_config(), http_timeout=timeout)
    proxy = _ClockTimeProxy(clock)
    with ExitStack() as stack:
        stack.callback(http.close)
        # The harness patches `time` in the runtime/db/session modules; these parsing modules also read the clock.
        for module in (overview_mod, queues_mod):
            if "time" in getattr(module, "__dict__", {}):
                stack.enter_context(mock.patch.object(module, "time", proxy))
        h = stack.enter_context(harness)
        for step in API_STEPS:
            out[step.case] = _send(http, h.base_url, step, server, clock, timeout)
        out["150_fake_game_side_effects"] = _fake_game_side_effects(server)
    return out
