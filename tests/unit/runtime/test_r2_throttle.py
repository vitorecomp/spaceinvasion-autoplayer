"""Integration and regression tests for Milestone M2 (R2 Galaxy/Throttle/Exploration/Hysteresis)."""

from __future__ import annotations

from dataclasses import asdict, replace
import itertools
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import requests
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

import commander
from backend.core import catalog as game_data
from backend.runtime.api.http_server import create_api_server
from backend.runtime.bridge import GameApiBridge
from backend.session.client import (
    GameBrowserClient,
    PageViewLimiter,
    RateLimitError,
)
from backend.runtime.commander_client import (
    GameApiClient,
    GameApiError,
)
from backend.core.config import StrategyConfig
from backend.parsing.models import (
    GalaxySlot,
    SpyReport,
)
from backend.runtime.reloader import snapshot_mtimes
from backend.core.model import (
    BuildingOffer,
    EnergyBalance,
    GameState,
    PlanetState,
    QueueJob,
    Resources,
    TutorialStatus,
    TutorialTask,
    game_state_from_dict,
)
from tests.pg_harness import open_test_db as GameDatabase, run_pg_query
from backend.core.model import PlannedAction
from backend.planner.heuristics import (
    BLAST_FURNACE,
    FUSION_PLANT,
    PIG_IRON_MINE,
    plan_actions,
)


def _sample_state(
    *,
    ships: dict[str, int] | None = None,
    buildings: dict[str, int] | None = None,
    economy_rates: dict[str, int] | None = None,
    energy: EnergyBalance = EnergyBalance(300, 120),
    resources: Resources = Resources(5500, 900, 3100, 200),
    speedup: float = 20.0,
) -> GameState:
    offers = (
        BuildingOffer(
            building_id=FUSION_PLANT,
            name="Fusion plant",
            current_level=3,
            target_level=4,
            cost=Resources(276, 0, 121, 0),
            can_build=True,
            status_text="Build level 4",
            build_href="indexInternal.es?action=internalBuildItem&buildingID=5#5",
        ),
        BuildingOffer(
            building_id=PIG_IRON_MINE,
            name="Pig iron mine",
            current_level=3,
            target_level=4,
            cost=Resources(269, 0, 87, 0),
            can_build=True,
            status_text="Build level 4",
            build_href="indexInternal.es?action=internalBuildItem&buildingID=1#1",
        ),
    )
    planet = PlanetState(
        planet_id=12476,
        name="Colony",
        coords="02:380:12",
        planet_type="normal",
        temperature=31,
        fields_used=20,
        fields_max=230,
        resources=resources,
        storage=Resources(100000, 100000, 100000, 100000),
        energy=energy,
        buildings=buildings or {"b1": 8, "b2": 6, "b3": 6, "b4": 4, "b5": 9, "b9": 2},
        ships=ships if ships is not None else {"s1": 2, "s4": 4, "s10": 3},
        building_offers=offers,
        economy_rates=economy_rates or {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
    )
    return GameState(
        universe_id=31,
        user_id=103603,
        nickname="ventkx86",
        timestamp=1234.5,
        planets=(planet,),
        active_planet_id=12476,
        researches={"r1": 0, "r2": 2},
        speedup_factor=speedup,
    )


class TestR2ThrottleAndGalaxy(unittest.TestCase):
    def test_rate_limit_cooldown_preserves_in_game_and_returns_structured_429(self) -> None:
        now = [1000.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        state = _sample_state()
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=abc")
        fake_client.read_full_snapshot.return_value = state
        fake_client.open_galaxy.side_effect = RateLimitError(
            "You've made more than 30 page views at last minute",
            retry_after=45.0,
        )

        bridge = GameApiBridge(
            debug_port=9222,
            log_dir=None,
            client_factory=lambda _port: fake_client,
            ensure_session_fn=lambda: None,
            page_limiter=limiter,
        )
        # Prime cached state before rate limit
        bridge.get_state(include_buildings=True)
        fake_client.ensure_in_game.reset_mock()
        fake_client.read_full_snapshot.reset_mock()
        fake_client.open_galaxy.reset_mock()

        server = create_api_server(bridge, host="127.0.0.1", port=0)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with patch("backend.session.browser.is_debug_port_open", return_value=True):
                resp = requests.post(
                    f"http://127.0.0.1:{port}/command",
                    json={"command": "scan_galaxy", "dry_run": False},
                    timeout=5,
                )
                self.assertEqual(resp.status_code, 429)
                self.assertIn("Retry-After", resp.headers)
                body = resp.json()
                self.assertFalse(body["ok"])
                self.assertTrue(body["rate_limited"])
                self.assertGreater(body["retry_after"], 0)
                self.assertIn("45.0", body["error"])

                # /health keeps in_game=True during cooldown and skips ensure_in_game
                fake_client.ensure_in_game.reset_mock()
                h_resp = requests.get(f"http://127.0.0.1:{port}/health", timeout=5)
                self.assertEqual(h_resp.status_code, 200)
                h = h_resp.json()
                self.assertTrue(h["ok"])
                self.assertTrue(h["in_game"])
                self.assertTrue(h["rate_limited"])
                self.assertGreater(h["retry_after"], 0)
                fake_client.ensure_in_game.assert_not_called()

                # /dashboard?refresh=1 returns HTTP 200 with cached state & rate_limited=True, 0 Chrome reads
                d_resp = requests.get(f"http://127.0.0.1:{port}/dashboard?refresh=1", timeout=5)
                self.assertEqual(d_resp.status_code, 200)
                dash = d_resp.json()
                self.assertTrue(dash["ok"])
                self.assertTrue(dash["health"]["in_game"])
                self.assertTrue(dash["health"]["rate_limited"])
                self.assertIsNotNone(dash["state"])
                fake_client.read_full_snapshot.assert_not_called()

                # Subsequent /command scan_galaxy during cooldown fails fast without touching Chrome
                fake_client.open_galaxy.reset_mock()
                resp2 = requests.post(
                    f"http://127.0.0.1:{port}/command",
                    json={"command": "scan_galaxy", "dry_run": False},
                    timeout=5,
                )
                self.assertEqual(resp2.status_code, 429)
                fake_client.open_galaxy.assert_not_called()

                # GET /state also fails fast with 429
                st_resp = requests.get(f"http://127.0.0.1:{port}/state", timeout=5)
                self.assertEqual(st_resp.status_code, 429)
        finally:
            server.shutdown()
            server.server_close()

    def test_commander_backs_off_at_least_retry_after_on_rate_limit(self) -> None:
        watched_resolved = {Path(p).resolve() for p in commander.WATCH_PATHS}
        self.assertIn(Path("backend/combat/scanner.py").resolve(), watched_resolved)
        self.assertIn(Path("backend/planner/heuristics/assembly.py").resolve(), watched_resolved)
        self.assertIn(Path("backend/combat/points.py").resolve(), watched_resolved)
        snaps = snapshot_mtimes(commander.WATCH_PATHS)
        self.assertIn(str(Path("bin/commander.py").resolve()), snaps)
        # Missing watch paths are skipped safely without crashing reloader
        safe_snaps = snapshot_mtimes((Path("bin/commander.py"), Path("backend/nonexistent_placeholder.py")))
        self.assertIn(str(Path("bin/commander.py").resolve()), safe_snaps)

        self.assertIsNone(commander.compute_rate_limit_backoff("random socket timeout"))
        self.assertGreaterEqual(
            commander.compute_rate_limit_backoff("SpaceInvasion per-minute page-view throttle hit; retry after 48s.")
            or 0.0,
            48.0,
        )

        # Simulate main() loop hitting rate limit on tick 1 and recovering on tick 2
        fake_api = MagicMock(spec=GameApiClient)
        fake_api.get_bot_control.return_value = {"ok": True, "paused": False, "mode_override": None}
        fake_api.health.return_value = {"ok": True, "in_game": True, "rate_limited": True, "retry_after": 48.0}
        state = _sample_state()
        fake_api.get_state.side_effect = [
            GameApiError("SpaceInvasion per-minute page-view throttle hit; retry after 48s."),
            state,
        ]
        fake_api.send_command.return_value = {
            "ok": True,
            "executed": False,
            "dry_run": True,
            "message": "[DRY-RUN]",
            "action": {},
            "next_action": None,
        }

        sleeps: list[float] = []
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text('{"playstyle": "fleeter"}', encoding="utf-8")
            with (
                patch("commander.GameApiClient", return_value=fake_api),
                patch("commander.time.sleep", side_effect=lambda s: sleeps.append(s)),
            ):
                rc = commander.main(
                    [
                        "--api-url",
                        "http://127.0.0.1:8787",
                        "--loop",
                        "--max-ticks",
                        "2",
                        "--interval",
                        "20",
                        "--config",
                        str(cfg_path),
                        "--log-dir",
                        str(Path(tmp) / "logs"),
                    ]
                )
        self.assertEqual(rc, 0)
        self.assertTrue(any(s >= 48.0 for s in sleeps), f"Expected backoff >= 48s, got {sleeps}")
        self.assertNotIn(3.0, sleeps)
        # Must NOT have invoked ensure_session for a rate limit!
        for call in fake_api.send_command.call_args_list:
            self.assertNotEqual(call.args[0].get("command"), "ensure_session")

    def test_burst_cuts_on_scan_galaxy_attack_dry_run_and_snapshot_ttl(self) -> None:
        state = _sample_state()
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=abc")
        fake_client.read_full_snapshot.return_value = state
        fake_client.open_galaxy.return_value = (
            '<table><tr><td>4</td><td><span class="galaxy_yellow">Idle</span></td><td>Sleepy</td></tr></table>'
        )

        bridge = GameApiBridge(
            debug_port=9222,
            log_dir=None,
            client_factory=lambda _port: fake_client,
            ensure_session_fn=lambda: None,
        )

        # (d) Repeat GET /state & plan within snapshot TTL reuses cached state
        bridge.get_state(include_buildings=True)
        self.assertEqual(fake_client.read_full_snapshot.call_count, 1)
        bridge.get_state(include_buildings=True)
        bridge.handle_command({"command": "plan"})
        self.assertEqual(fake_client.read_full_snapshot.call_count, 1)

        # (a) scan_galaxy when _last_state cached calls read_full_snapshot 0 times and never navigates home
        fake_client.navigate_menu.reset_mock()
        scan_res = bridge.handle_command({"command": "scan_galaxy", "dry_run": False})
        self.assertTrue(scan_res["executed"])
        self.assertEqual(fake_client.read_full_snapshot.call_count, 1)
        fake_client.navigate_menu.assert_not_called()
        self.assertIn("state", scan_res)

        # (a2) scan_galaxy when uncached passes include_buildings=False, include_research=False, include_shipyard=False, return_home=False
        bridge.ctx.last_state = None
        bridge.ctx.last_snapshot_at = 0.0
        fake_client.read_full_snapshot.reset_mock()
        bridge.handle_command({"command": "scan_galaxy", "dry_run": False})
        self.assertEqual(fake_client.read_full_snapshot.call_count, 1)
        kw = fake_client.read_full_snapshot.call_args.kwargs
        self.assertFalse(kw["include_buildings"])
        self.assertFalse(kw["include_research"])
        self.assertFalse(kw["include_shipyard"])
        self.assertFalse(kw["return_home"])

        # (b) attack (refresh=True, allow_raids=True) passes include_shipyard=False
        fake_client.read_full_snapshot.reset_mock()
        bridge.handle_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False, "refresh": True})
        self.assertFalse(fake_client.read_full_snapshot.call_args.kwargs["include_shipyard"])

        # (c) build dry_run=True skips post-action read_full_snapshot and skips navigate_menu("internalHome")
        fake_client.read_full_snapshot.reset_mock()
        fake_client.navigate_menu.reset_mock()
        dry_build = bridge.handle_command({"command": "build", "building_id": FUSION_PLANT, "dry_run": True})
        self.assertFalse(dry_build["executed"])
        fake_client.read_full_snapshot.assert_not_called()
        fake_client.navigate_menu.assert_not_called()

        # (e) commander.run_commander_tick reuses res["state"] on live execution without duplicate get_state
        fake_api = MagicMock(spec=GameApiClient)
        fake_api.get_state.return_value = state
        fake_api.send_command.return_value = {
            "ok": True,
            "executed": True,
            "dry_run": False,
            "message": "Built",
            "action": {},
            "next_action": None,
            "state": asdict(state),
        }
        commander.run_commander_tick(fake_api, live=True, max_actions=1, config=StrategyConfig())
        self.assertEqual(fake_api.get_state.call_count, 1)

    def test_navigate_internal_galaxy_preserves_coords_and_no_forced_home(self) -> None:
        state = _sample_state()
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.read_full_snapshot.return_value = state
        fake_client.last_galaxy_coords = (2, 385)
        fake_client.open_galaxy.return_value = (
            '<table><tr><td>4</td><td><span class="galaxy_yellow">Idle385</span></td><td>Sleeper</td></tr></table>'
        )

        bridge = GameApiBridge(
            debug_port=9222,
            log_dir=None,
            client_factory=lambda _port: fake_client,
            ensure_session_fn=lambda: None,
        )
        res = bridge.handle_command({"command": "navigate", "action": "internalGalaxy", "galaxy": 2, "system": 385})
        self.assertTrue(res["ok"])
        self.assertEqual(res["galaxy"], 2)
        self.assertEqual(res["system"], 385)
        self.assertGreaterEqual(res["scanned_targets"], 1)
        fake_client.open_galaxy.assert_called_once_with(galaxy=2, system=385, priority="interactive")
        fake_client.navigate_menu.assert_not_called()
        self.assertTrue(any(t["coords"] == "02:385:04" for t in bridge.ctx.raid_targets))

    def test_update_config_syncs_max_actions_per_hour_and_dry_executor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=None,
                client_factory=lambda _port: MagicMock(),
                ensure_session_fn=lambda: None,
            )
            upd = bridge.handle_command(
                {
                    "command": "update_config",
                    "config": {
                        "max_actions_per_hour": 14,
                        "allow_raids": True,
                        "allow_fleetsave": True,
                    },
                }
            )
            self.assertEqual(upd["config"]["max_actions_per_hour"], 14)
            self.assertEqual(bridge._live_executor.max_actions_per_hour, 14)
            self.assertEqual(bridge._dry_executor.max_actions_per_hour, 14)
            self.assertTrue(bridge._live_executor.allow_raids)
            self.assertTrue(bridge._dry_executor.allow_raids)
            self.assertTrue(bridge._live_executor.allow_fleetsave)
            self.assertTrue(bridge._dry_executor.allow_fleetsave)

    def test_attack_command_enforces_bash_tracker_and_combat_safety(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs_dir = Path(tmp) / "logs"
            db_path = Path(tmp) / "state" / "backend.db"
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text('{"allow_raids": true, "allow_pvp_in_az": true}', encoding="utf-8")

            state = _sample_state(ships={"s4": 2, "s1": 1})
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.read_full_snapshot.return_value = state
            fake_client._wait_frame_ready.return_value = "<div>Fleet sent</div>"

            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=logs_dir,
                db_path=db_path,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            assert bridge._db is not None
            try:
                now = time.time()
                # Record 3 raids against light_red target 02:380:07
                for i in range(3):
                    bridge._db.record_raid("02:380:07", attacked_ts=now - 100 + i, is_exempt=False)

                bash_blocked = bridge.handle_command(
                    {"command": "attack", "target_coords": "02:380:07", "dry_run": False}
                )
                self.assertFalse(bash_blocked["executed"])
                self.assertIn("BashTracker", bash_blocked["message"])
                fake_client.navigate_menu.assert_not_called()

                # Heavily defended target fails combat safety check (< 1.4x)
                bridge._db.record_spy_report(
                    SpyReport(
                        target_coords="02:380:08",
                        owner="Turtle",
                        resources=Resources(10000, 10000, 10000, 2000),
                        defense={"d1": 200},
                    ),
                    is_inactive=True,
                    ts=now,
                )
                bridge.ctx.raid_targets = tuple(bridge._db.get_spy_targets(limit=100))
                safety_blocked = bridge.handle_command(
                    {"command": "attack", "target_coords": "02:380:08", "dry_run": False}
                )
                self.assertFalse(safety_blocked["executed"])
                self.assertIn("combat safety", safety_blocked["message"].lower())
                fake_client.navigate_menu.assert_not_called()

                # Yellow inactive / Spacia with 3 prior raids IS exempt from BashTracker
                bridge._db.record_galaxy_slots(
                    [GalaxySlot("02:380:05", 5, "Idle", "Sleeper", "yellow")],
                    ts=now,
                )
                for i in range(3):
                    bridge._db.record_raid("02:380:05", attacked_ts=now - 100 + i, is_exempt=True)
                bridge.handle_command({"command": "update_config", "config": {"allow_raids": False}})
                allow_raids_msg = bridge.handle_command(
                    {"command": "attack", "target_coords": "02:380:05", "dry_run": False}
                )
                self.assertFalse(allow_raids_msg["executed"])
                self.assertIn("allow_raids", allow_raids_msg["message"])
            finally:
                bridge._db.close()

    def test_multi_system_exploration_sweep_and_spy_planning(self) -> None:
        now = 100_000.0
        state_no_probes = _sample_state(ships={"s1": 2, "s4": 4})
        cfg = StrategyConfig(allow_galaxy_scan=True, allow_raids=True)

        # Home 02:380 recently scanned -> sweep picks adjacent 02:381, then 02:379
        acts1 = plan_actions(
            state_no_probes,
            config=cfg,
            scanned_systems={"02:380": now - 60.0},
            now_ts=now,
        )
        scan1 = [a for a in acts1 if a.kind == "SCAN_GALAXY"]
        self.assertEqual(len(scan1), 1)
        self.assertIn("02:381", scan1[0].target_name)

        acts2 = plan_actions(
            state_no_probes,
            config=cfg,
            scanned_systems={"02:380": now - 60.0, "02:381": now - 30.0},
            now_ts=now,
        )
        scan2 = [a for a in acts2 if a.kind == "SCAN_GALAXY"]
        self.assertEqual(len(scan2), 1)
        self.assertIn("02:379", scan2[0].target_name)

        # When all systems in radius ±15 are on cooldown, stop emitting SCAN_GALAXY
        all_scanned = {f"02:{380 + off:03d}": now - 10.0 for off in range(-15, 16)}
        acts_done = plan_actions(
            state_no_probes,
            config=cfg,
            scanned_systems=all_scanned,
            now_ts=now,
        )
        self.assertFalse(any(a.kind == "SCAN_GALAXY" for a in acts_done))

        # When s10 probes exist and an unspied yellow target is in raid_targets, emit SPY
        state_with_probes = GameState(
            universe_id=31,
            user_id=103603,
            nickname="ventkx86",
            timestamp=now,
            planets=_sample_state(ships={"s1": 2, "s4": 4, "s10": 3}).planets,
            active_planet_id=12476,
            raid_targets=(
                {
                    "coords": "02:380:04",
                    "owner": "Sleeper",
                    "planet_name": "Idle",
                    "status_color": "yellow",
                    "is_inactive": True,
                    "is_spacia": False,
                    "resources": {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
                    "fleet": {},
                    "defense": {},
                    "last_scanned_ts": now - 20.0,
                    "last_spied_ts": 0.0,
                    "has_spy_intel": False,
                },
            ),
        )
        spy_acts = [a for a in plan_actions(state_with_probes, config=cfg, now_ts=now) if a.kind == "SPY"]
        self.assertEqual(len(spy_acts), 1)
        self.assertTrue(spy_acts[0].executable_now)
        self.assertEqual(spy_acts[0].target_id, 10)

    def test_galaxy_scans_table_and_spy_report_preserves_flags_and_sync_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "backend.db"
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.read_full_snapshot.return_value = _sample_state()
            fake_client.open_messages.return_value = """
            <div class="msg">
              Espionage report for 02:380:04 Player: Sleeper
              Pig iron: 45.000 Metal: 12.000 Kryptonite: 8.000 Spice: 3.000
              Mortar: 1
            </div>
            """
            bridge = GameApiBridge(
                debug_port=9222,
                log_dir=None,
                db_path=db_path,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            assert bridge._db is not None
            try:
                # Empty scan still records in galaxy_scans
                bridge._db.record_galaxy_scan(2, 381, (), ts=1000.0)
                scans = bridge._db.get_galaxy_scans(max_age_seconds=1800.0, now=1200.0)
                self.assertIn("02:381", scans)

                # record_galaxy_slots marks 02:380:04 yellow inactive
                bridge._db.record_galaxy_slots(
                    [GalaxySlot("02:380:04", 4, "Idle", "Sleeper", "yellow")],
                    ts=1100.0,
                )
                # Sync spy reports via API command (calls record_spy_report with defaults)
                sync_res = bridge.handle_command({"command": "sync_messages"})
                self.assertEqual(sync_res["synced_reports"], 1)

                targets = bridge._db.get_spy_targets()
                t4 = next(t for t in targets if t["coords"] == "02:380:04")
                self.assertEqual(t4["status_color"], "yellow")
                self.assertTrue(t4["is_inactive"])
                self.assertTrue(t4["has_spy_intel"])
                self.assertGreater(t4["last_spied_ts"], 0.0)
                self.assertEqual(t4["resources"]["pig_iron"], 45000)
                self.assertEqual(t4["defense"].get("d1"), 1)
            finally:
                bridge._db.close()

    def test_blast_furnace_hysteresis_stops_row36_oscillation_and_enforces_cooldown(self) -> None:
        # Exact DB row 36 reconstruction (`b1=12, b2=11, b3=11, b4=8, b5=13, b7=4`, 20x speedup, energy 814/681, resource2=0)
        p_row36 = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=55,
            fields_max=230,
            resources=Resources(10159, 2423, 11907, 1617),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(814, 681),
            buildings={"b1": 12, "b2": 11, "b3": 11, "b4": 8, "b5": 13, "b7": 4, "b8": 1, "b9": 2},
            economy_rates={"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100},
        )
        st_row36 = GameState(
            31,
            103603,
            "ventkx86",
            0.0,
            (p_row36,),
            active_planet_id=12476,
            researches={"r1": 0, "r2": 2},
            speedup_factor=20.0,
        )
        acts = plan_actions(st_row36, config=StrategyConfig(auto_throttle_blast_furnace=True))
        self.assertFalse(any(a.kind == "THROTTLE_BUILDING" for a in acts))

        # 20x Pig-iron deadband: 12,000 Pig iron is below bf_pig_used_full*1.5 (32,220) -> do NOT resume 100%
        p_band = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=55,
            fields_max=230,
            resources=Resources(12000, 2000, 50000, 50000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(2000, 681),
            buildings={"b1": 12, "b2": 11, "b3": 11, "b4": 8, "b5": 16},
            economy_rates={"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100},
        )
        st_band = GameState(31, 103603, "u", 0.0, (p_band,), speedup_factor=20.0)
        acts_band = plan_actions(st_band, config=StrategyConfig(auto_throttle_blast_furnace=True))
        self.assertFalse(any(a.kind == "THROTTLE_BUILDING" for a in acts_band))

        # 300s per-planet cooldown suppresses immediate flip-flop when energy is positive
        p_resume_ok = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=20,
            fields_max=230,
            resources=Resources(4000, 10, 4000, 4000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(200, 120),
            buildings={"b1": 8, "b2": 6, "b3": 6, "b4": 4, "b5": 9, "b7": 1},
            economy_rates={"resource2": 0},
        )
        st_cd = GameState(31, 103603, "u", 0.0, (p_resume_ok,), speedup_factor=1.0)
        suppressed = plan_actions(
            st_cd,
            config=StrategyConfig(auto_throttle_blast_furnace=True),
            throttle_cooldowns={12476: 1000.0},
            now_ts=1100.0,
        )
        self.assertFalse(any(a.kind == "THROTTLE_BUILDING" for a in suppressed))
        allowed_after_300 = plan_actions(
            st_cd,
            config=StrategyConfig(auto_throttle_blast_furnace=True),
            throttle_cooldowns={12476: 1000.0},
            now_ts=1305.0,
        )
        self.assertTrue(any(a.kind == "THROTTLE_BUILDING" and a.target_level == 100 for a in allowed_after_300))

    def test_normal_budget_exhaustion_preserves_interactive_headroom_and_commander_backoff(self) -> None:
        now = [200.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            max_views_per_minute=6,
            reserved_interactive_views=2,
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        limiter.record_view(4)

        state = _sample_state()
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=abc")

        def _fake_snapshot(**_kw):
            limiter.acquire(priority="normal", max_wait=0.5)
            return state

        def _fake_open_galaxy(**_kw):
            limiter.acquire(priority="interactive", max_wait=0.5)
            return '<table><tr><td>4</td><td><span class="galaxy_yellow">Idle</span></td><td>Sleeper</td></tr></table>'

        fake_client.read_full_snapshot.side_effect = _fake_snapshot
        fake_client.open_galaxy.side_effect = _fake_open_galaxy

        bridge = GameApiBridge(
            debug_port=9222,
            log_dir=None,
            client_factory=lambda _port: fake_client,
            ensure_session_fn=lambda: None,
            page_limiter=limiter,
            max_limiter_wait=0.5,
        )
        bridge.ctx.last_state = state

        server = create_api_server(bridge, host="127.0.0.1", port=0)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with patch("backend.session.browser.is_debug_port_open", return_value=True):
                # 1. Normal GET /state hits local budget -> HTTP 429, cooldown remains 0.0, hits == 0
                st_resp = requests.get(f"http://127.0.0.1:{port}/state", timeout=5)
                self.assertEqual(st_resp.status_code, 429)
                self.assertIn("Page-view budget", st_resp.json()["error"])
                self.assertEqual(limiter.rate_limit_remaining(), 0.0)
                self.assertEqual(limiter.rate_limit_hits, 0)

                # 2. /health and /dashboard?refresh=1 remain rate_limited=False
                h = requests.get(f"http://127.0.0.1:{port}/health", timeout=5).json()
                self.assertFalse(h["rate_limited"])
                dash = requests.get(f"http://127.0.0.1:{port}/dashboard?refresh=1", timeout=5)
                self.assertEqual(dash.status_code, 200)
                self.assertFalse(dash.json()["health"]["rate_limited"])

                # 3. Interactive scan_galaxy uses reserved interactive slot cleanly
                scan = requests.post(
                    f"http://127.0.0.1:{port}/command",
                    json={"command": "scan_galaxy", "dry_run": False},
                    timeout=5,
                )
                self.assertEqual(scan.status_code, 200)
                self.assertTrue(scan.json()["executed"])

                # 4. Server RateLimitError records rate_limit_hits == 1 (never double-counted by bridge+handler)
                limiter.reset()
                fake_client.open_galaxy.side_effect = RateLimitError(
                    "You've made more than 30 page views at last minute",
                    retry_after=45.0,
                )
                srv_429 = requests.post(
                    f"http://127.0.0.1:{port}/command",
                    json={"command": "scan_galaxy", "dry_run": False},
                    timeout=5,
                )
                self.assertEqual(srv_429.status_code, 429)
                self.assertEqual(limiter.rate_limit_hits, 1)

                # 5. Sub-second tail (0.4s remaining) does NOT extend timer or double-count
                now[0] += 44.6
                self.assertAlmostEqual(limiter.rate_limit_remaining(), 0.4, places=3)
                tail_429 = requests.get(f"http://127.0.0.1:{port}/state", timeout=5)
                self.assertEqual(tail_429.status_code, 429)
                self.assertAlmostEqual(limiter.rate_limit_remaining(), 0.4, places=3)
                self.assertEqual(limiter.rate_limit_hits, 1)
        finally:
            server.shutdown()
            server.server_close()

        # 6. Commander backoff handles local page-view budget messages without sleeping 3s
        self.assertGreaterEqual(
            commander.compute_rate_limit_backoff("Page-view budget reached (18/18 in 60s); retry after 60.0s.") or 0.0,
            60.0,
        )
        fake_api = MagicMock(spec=GameApiClient)
        fake_api.get_bot_control.return_value = {"ok": True, "paused": False, "mode_override": None}
        fake_api.health.return_value = {"ok": True, "in_game": True, "rate_limited": False, "retry_after": 0.0}
        fake_api.get_state.side_effect = [
            GameApiError("Page-view budget reached (18/18 in 60s); retry after 60.0s."),
            state,
        ]
        fake_api.send_command.return_value = {
            "ok": True,
            "executed": False,
            "dry_run": True,
            "message": "[DRY-RUN]",
            "action": {},
            "next_action": None,
        }
        sleeps: list[float] = []
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text('{"playstyle": "fleeter"}', encoding="utf-8")
            with (
                patch("commander.GameApiClient", return_value=fake_api),
                patch("commander.time.sleep", side_effect=lambda s: sleeps.append(s)),
            ):
                rc = commander.main(
                    [
                        "--api-url",
                        "http://127.0.0.1:8787",
                        "--loop",
                        "--max-ticks",
                        "2",
                        "--config",
                        str(cfg_path),
                        "--log-dir",
                        str(Path(tmp) / "logs"),
                    ]
                )
        self.assertEqual(rc, 0)
        self.assertTrue(any(s >= 60.0 for s in sleeps), f"Expected >= 60s sleep, got {sleeps}")
        self.assertNotIn(3.0, sleeps)
        for c in fake_api.send_command.call_args_list:
            self.assertNotEqual(c.args[0].get("command"), "ensure_session")

    def test_executor_pace_action_respects_max_limiter_wait_inside_bridge_lock(self) -> None:
        from backend.session.client import GameBrowserClient
        from backend.session.executor import ActionExecutor

        now = [500.0]
        sleeps: list[float] = []
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            max_views_per_minute=2,
            reserved_interactive_views=0,
            clock=lambda: now[0],
            sleeper=lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s))[0],
        )
        limiter.record_view(2)

        bridge = GameApiBridge(
            debug_port=9222,
            log_dir=None,
            client_factory=lambda _port: MagicMock(),
            ensure_session_fn=lambda: None,
            page_limiter=limiter,
            max_limiter_wait=8.0,
        )
        self.assertEqual(bridge._live_executor.max_limiter_wait, 8.0)
        self.assertEqual(bridge._dry_executor.max_limiter_wait, 8.0)

        fake_client = MagicMock(spec=GameBrowserClient)
        with self.assertRaises(RateLimitError) as ctx:
            bridge._live_executor._pace_action(fake_client, priority="interactive")
        self.assertFalse(ctx.exception.server_throttle)
        self.assertEqual(sleeps, [])

        # ActionExecutor without max_limiter_wait must not crash on MagicMock.max_limiter_wait
        limiter.reset()
        ex_plain = ActionExecutor(page_limiter=limiter)
        ex_plain._pace_action(fake_client, priority="interactive")
        self.assertEqual(limiter.views_last_60s(), 1)

    def test_building_offer_cache_invalidation_and_full_queue_ttl_burst_fixes(self) -> None:
        p1 = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=20,
            fields_max=230,
            resources=Resources(50000, 50000, 50000, 50000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(500, 120),
            buildings={"b1": 5, "b5": 9, "b8": 1, "b9": 1},
            building_offers=_sample_state().planets[0].building_offers,
            economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
        )
        st_with_offers = GameState(
            31,
            103603,
            "ventkx86",
            100.0,
            (p1,),
            active_planet_id=12476,
            researches={"r1": 1},
        )
        p1_updated = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=21,
            fields_max=230,
            resources=Resources(50000, 50000, 50000, 50000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(500, 120),
            buildings={"b1": 6, "b5": 9, "b8": 1, "b9": 1},
            building_offers=(),
            economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
        )
        st_changed_no_offers = GameState(
            31,
            103603,
            "ventkx86",
            101.0,
            (p1_updated,),
            active_planet_id=12476,
            researches={"r1": 1},
        )

        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.read_full_snapshot.side_effect = [st_with_offers, st_changed_no_offers, st_with_offers]
        bridge = GameApiBridge(
            debug_port=9222,
            log_dir=None,
            client_factory=lambda _port: fake_client,
            ensure_session_fn=lambda: None,
        )
        bridge.get_state(include_buildings=True)
        self.assertTrue(bridge.ctx.last_buildings_fetched)

        # Non-building refresh where b1 changed clears _last_buildings_fetched because building_offers == ()
        bridge.handle_command({"command": "attack", "target_coords": "02:380:04", "dry_run": True, "refresh": True})
        self.assertFalse(bridge.ctx.last_buildings_fetched)
        bridge.get_state(include_buildings=True)
        self.assertEqual(fake_client.read_full_snapshot.call_count, 3)

        # Bug 2 (commander.py): full build queue (5/5) must NOT trigger immediate include_buildings=True retry
        full_q = tuple(QueueJob("building", f"Job {i}", building_id=1, target_level=i + 6) for i in range(5))
        p_full_q = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=20,
            fields_max=230,
            resources=Resources(50000, 50000, 50000, 50000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(500, 120),
            buildings={"b1": 5, "b3": 5, "b4": 4, "b5": 9},
            building_offers=(),
        )
        st_full_q = GameState(
            31,
            103603,
            "ventkx86",
            100.0,
            (p_full_q,),
            active_planet_id=12476,
            active_queues=full_q,
        )
        fake_api = MagicMock(spec=GameApiClient)
        fake_api.get_state.return_value = st_full_q
        commander.run_commander_tick(fake_api, live=False, max_actions=1, include_buildings=False)
        self.assertEqual(fake_api.get_state.call_count, 1)

        # Contrast: 0 queues + stale offer.can_build=False + affordable resources DOES trigger include_buildings=True
        stale_offer = BuildingOffer(
            building_id=PIG_IRON_MINE,
            name="Pig iron mine",
            current_level=5,
            target_level=6,
            cost=Resources(500, 200, 100, 0),
            can_build=False,
            status_text="Not enough resources",
        )
        p_stale = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=20,
            fields_max=230,
            resources=Resources(50000, 50000, 50000, 50000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(500, 120),
            buildings={"b1": 5, "b3": 5, "b4": 4, "b5": 9},
            building_offers=(stale_offer,),
        )
        st_stale = GameState(31, 103603, "ventkx86", 100.0, (p_stale,), active_planet_id=12476)
        fake_api2 = MagicMock(spec=GameApiClient)
        fake_api2.get_state.return_value = st_stale
        commander.run_commander_tick(fake_api2, live=False, max_actions=1, include_buildings=False)
        self.assertEqual(fake_api2.get_state.call_count, 2)
        self.assertTrue(fake_api2.get_state.call_args_list[1].kwargs["include_buildings"])

        # Bug 2 (api_server.py TTL): unchanged empty research/shipyard syncs do NOT bypass TTL on repeat call
        fake_client2 = MagicMock()
        fake_client2.__enter__.return_value = fake_client2
        fake_client2.read_full_snapshot.return_value = st_changed_no_offers
        bridge2 = GameApiBridge(
            debug_port=9222,
            log_dir=None,
            client_factory=lambda _port: fake_client2,
            ensure_session_fn=lambda: None,
        )
        bridge2.get_state(include_buildings=True)
        self.assertTrue(fake_client2.read_full_snapshot.call_args_list[-1].kwargs["include_research"])
        self.assertTrue(fake_client2.read_full_snapshot.call_args_list[-1].kwargs["include_shipyard"])

        bridge2.ctx.last_snapshot_at = 0.0
        bridge2.get_state(include_buildings=True)
        self.assertFalse(fake_client2.read_full_snapshot.call_args_list[-1].kwargs["include_research"])
        self.assertFalse(fake_client2.read_full_snapshot.call_args_list[-1].kwargs["include_shipyard"])

        # Bumping research level invalidates sync sig and re-syncs
        bridge2.ctx.last_state = GameState(
            31,
            103603,
            "ventkx86",
            102.0,
            (p1_updated,),
            active_planet_id=12476,
            researches={"r1": 2},
        )
        bridge2.ctx.last_snapshot_at = 0.0
        bridge2.get_state(include_buildings=True)
        self.assertTrue(fake_client2.read_full_snapshot.call_args_list[-1].kwargs["include_research"])

    def test_commander_last_full_buildings_at_on_rate_limit_and_non_building_actions(self) -> None:
        now = [100.0]
        state = _sample_state()
        fake_api = MagicMock(spec=GameApiClient)
        fake_api.get_bot_control.return_value = {"ok": True, "paused": False, "mode_override": None}
        fake_api.health.return_value = {"ok": True, "in_game": True, "rate_limited": True, "retry_after": 45.0}
        fake_api.get_state.side_effect = [
            GameApiError("SpaceInvasion per-minute page-view throttle hit; retry after 45s."),
            state,
        ]
        fake_api.send_command.return_value = {
            "ok": True,
            "executed": False,
            "dry_run": True,
            "message": "[DRY-RUN]",
            "action": {},
            "next_action": None,
        }

        def _sleep_adv(secs: float) -> None:
            now[0] += secs

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text('{"playstyle": "fleeter"}', encoding="utf-8")
            with (
                patch("commander.GameApiClient", return_value=fake_api),
                patch("commander.time.monotonic", side_effect=lambda: now[0]),
                patch("commander.time.sleep", side_effect=_sleep_adv),
            ):
                rc = commander.main(
                    [
                        "--api-url",
                        "http://127.0.0.1:8787",
                        "--loop",
                        "--max-ticks",
                        "2",
                        "--config",
                        str(cfg_path),
                        "--log-dir",
                        str(Path(tmp) / "logs"),
                    ]
                )
        self.assertEqual(rc, 0)
        # Tick 1 failed with 429, so Tick 2 must STILL pass include_buildings=True
        self.assertTrue(fake_api.get_state.call_args_list[1].kwargs["include_buildings"])

        # Finding 5.3: live SCAN_GALAXY on Tick 2 does NOT reset 60s buildings timer
        now2 = [1000.0]
        state_no_offers = GameState(
            31,
            103603,
            "ventkx86",
            1000.0,
            (
                PlanetState(
                    planet_id=12476,
                    name="Colony",
                    coords="02:380:12",
                    planet_type="normal",
                    temperature=31,
                    fields_used=20,
                    fields_max=230,
                    resources=Resources(50000, 50000, 50000, 50000),
                    storage=Resources(100000, 100000, 100000, 100000),
                    energy=EnergyBalance(500, 120),
                    buildings={"b1": 5},
                    ships={"s1": 1},
                ),
            ),
            active_planet_id=12476,
        )
        fake_api2 = MagicMock(spec=GameApiClient)
        fake_api2.get_bot_control.return_value = {"ok": True, "paused": False, "mode_override": None}
        fake_api2.get_state.return_value = state_no_offers
        fake_api2.send_command.return_value = {
            "ok": True,
            "command": "scan_galaxy",
            "executed": True,
            "dry_run": False,
            "message": "Scanned",
            "action": {"kind": "SCAN_GALAXY"},
            "next_action": None,
            "state": asdict(state_no_offers),
        }

        def _sleep_fixed(secs: float) -> None:
            # Tick 1 -> +25s; Tick 2 -> +40s (total 65s since Tick 1, only 40s since Tick 2 SCAN_GALAXY)
            if now2[0] < 1020.0:
                now2[0] += 25.0
            else:
                now2[0] += 40.0

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text('{"allow_galaxy_scan": true}', encoding="utf-8")
            with (
                patch("commander.GameApiClient", return_value=fake_api2),
                patch("commander.time.monotonic", side_effect=lambda: now2[0]),
                patch("commander.time.sleep", side_effect=_sleep_fixed),
                patch("commander.plan_actions") as mock_plan,
            ):
                scan_act = PlannedAction(
                    priority="P2",
                    kind="SCAN_GALAXY",
                    planet_id=12476,
                    planet_coords="02:380:12",
                    target_name="Galaxy [02:380]",
                    reason="Scan",
                    executable_now=True,
                )
                mock_plan.side_effect = [[], [scan_act], [], []]
                rc2 = commander.main(
                    [
                        "--api-url",
                        "http://127.0.0.1:8787",
                        "--live",
                        "--loop",
                        "--max-ticks",
                        "3",
                        "--config",
                        str(cfg_path),
                        "--log-dir",
                        str(Path(tmp) / "logs"),
                    ]
                )
        self.assertEqual(rc2, 0)
        self.assertTrue(fake_api2.get_state.call_args_list[0].kwargs["include_buildings"])
        self.assertFalse(fake_api2.get_state.call_args_list[1].kwargs["include_buildings"])
        self.assertTrue(fake_api2.get_state.call_args_list[2].kwargs["include_buildings"])

    def test_health_port_closed_record_raid_exempt_tick_once_and_f19_raid_targets_enrichment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "state" / "backend.db"
            cfg_path = tmp_path / "config.json"
            cfg_path.write_text('{"allow_raids": true}', encoding="utf-8")

            state = _sample_state(ships={"s4": 4})
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.read_full_snapshot.return_value = state
            fake_client._wait_frame_ready.side_effect = [
                '<form action="indexInternal.es?action=internalSendFleet"><input type="radio" name="mission" value="1"/> Attack<input type="submit"/></form>',
                "<div>Fleet dispatched</div>",
            ]
            # Step 1 form inputs
            s4_inp, gal_inp, sun_inp, pla_inp, cont_btn, atk_radio, launch_btn = (
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
            )
            atk_radio.get_attribute.side_effect = lambda a: {"value": "1", "name": "mission"}.get(a, "")
            atk_radio.is_selected.return_value = False
            fake_client.navigate_menu.return_value = '<form action="indexInternal.es?action=internalSendFleet"><input name="ship_4"/><input name="gal"/><input name="sun"/><input name="pla"/><input type="submit"/></form>'
            fake_client.driver = MagicMock()
            fake_client.driver.find_elements.side_effect = [[s4_inp], [atk_radio]]
            fake_client.driver.find_element.side_effect = [gal_inp, sun_inp, pla_inp, cont_btn, launch_btn]

            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=None,
                db_path=db_path,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            assert bridge._db is not None
            try:
                # 1. Closed port -> health and dashboard both report in_game=False even with cached state + cooldown
                bridge.ctx.last_state = state
                bridge.record_rate_limit(30.0)
                with patch("backend.session.browser.is_debug_port_open", return_value=False):
                    h = bridge.health()
                    d = bridge.dashboard(refresh=False)
                self.assertFalse(h["in_game"])
                self.assertFalse(d["health"]["in_game"])

                # 2. tick_once sets _last_research_sync_at > 0.0
                bridge._page_limiter.reset()
                bridge.handle_command({"command": "tick_once", "dry_run": True})
                self.assertGreater(bridge.ctx.last_research_sync_at, 0.0)

                # 3. Record yellow target 02:380:05 and execute attack -> raid_history.is_exempt == 1
                bridge._db.record_galaxy_slots(
                    [
                        GalaxySlot("02:380:04", 4, "Farm", "IdleA", "yellow"),
                        GalaxySlot("02:380:05", 5, "IdleB", "SleeperB", "yellow"),
                    ],
                    ts=1000.0,
                )
                bridge.ctx.raid_targets = bridge.state_cache.enrich_raid_targets(bridge._db.get_spy_targets(limit=100))
                atk_res = bridge.handle_command({"command": "attack", "target_coords": "02:380:05", "dry_run": False})
                self.assertTrue(atk_res["executed"])
                hist_rows = bridge._db.get_raid_stats()["history"]
                self.assertTrue(any(r["target_coords"] == "02:380:05" and r["is_exempt"] for r in hist_rows))

                # 4. F19 enrichment: 02:380:04 (1 win, 1 loss) vs 02:380:09 (0 raids)
                bridge._db.record_galaxy_slots([GalaxySlot("02:380:09", 9, "Fresh", "IdleC", "yellow")], ts=1001.0)
                bridge._db.record_raid(
                    "02:380:04",
                    attacked_ts=1100.0,
                    is_exempt=True,
                    outcome="Victory",
                    loot={"pig_iron": 6000, "metal": 0, "kryptonite": 0, "spice": 0},
                )
                bridge._db.record_raid("02:380:04", attacked_ts=1200.0, is_exempt=True, outcome="Defeat", loot={})
                bridge.ctx.last_snapshot_at = time.monotonic()
                st_dict = bridge.get_state(include_buildings=True)
                rt_map = {t["coords"]: t for t in st_dict["raid_targets"]}
                t4 = rt_map["02:380:04"]
                self.assertEqual(t4["attack_attempts"], 2)
                self.assertEqual(t4["attack_wins"], 1)
                self.assertAlmostEqual(t4["attack_success_rate"], 0.5)
                self.assertIsNotNone(t4["avg_attack_loot"])
                self.assertGreater(t4["avg_attack_loot_spice_eq"], 0.0)
                self.assertGreater(t4["total_attack_loot_spice_eq"], 0.0)
                self.assertGreater(t4["recency_weighted_loot_spice_eq"], 0.0)
                self.assertEqual(t4["last_attack_outcome"], "Defeat")
                self.assertEqual(t4["last_attack_loot_spice_eq"], 0.0)
                self.assertEqual(t4["last_attack_ts"], 1200.0)

                t9 = rt_map["02:380:09"]
                self.assertEqual(t9["attack_attempts"], 0)
                self.assertIsNone(t9["attack_success_rate"])
                self.assertIsNone(t9["avg_attack_loot"])
                self.assertIsNone(t9["last_attack_loot"])

                rebuilt = game_state_from_dict(st_dict)
                self.assertEqual(len(rebuilt.raid_targets), len(st_dict["raid_targets"]))
            finally:
                bridge._db.close()

    def test_blast_furnace_hysteresis_no_flip_flop_with_three_affordable_builds_and_tight_energy(self) -> None:
        # Scenario 1: 3 affordable BUILD offers + Blast furnace WAIT_RESOURCES (missing.metal=49990)
        offers3 = (
            BuildingOffer(1, "Pig iron mine", 10, 11, Resources(100, 0, 50, 0), True, "Build"),
            BuildingOffer(3, "Kryptonite mine", 10, 11, Resources(100, 0, 50, 0), True, "Build"),
            BuildingOffer(4, "Spice mine", 8, 9, Resources(100, 0, 50, 0), True, "Build"),
            BuildingOffer(2, "Blast furnace", 12, 13, Resources(1000, 50000, 100, 0), False, "Wait"),
        )
        cur_b2 = 0
        seq1: list[int] = []
        for tick in range(4):
            p = PlanetState(
                planet_id=12476,
                name="Colony",
                coords="02:380:12",
                planet_type="normal",
                temperature=31,
                fields_used=55,
                fields_max=230,
                resources=Resources(20000, 10, 5000, 5000),
                storage=Resources(200000, 200000, 200000, 200000),
                energy=EnergyBalance(2000, 681),
                buildings={"b1": 10, "b2": 12, "b3": 10, "b4": 8, "b5": 14},
                building_offers=offers3,
                economy_rates={"resource1": 100, "resource2": cur_b2, "resource3": 100, "resource4": 100},
            )
            st = GameState(31, 103603, "u", 1000.0 + tick * 400.0, (p,), active_planet_id=12476, speedup_factor=20.0)
            acts = plan_actions(st, config=StrategyConfig(auto_throttle_blast_furnace=True))
            thr = [a for a in acts if a.kind == "THROTTLE_BUILDING"]
            if thr:
                cur_b2 = int(thr[0].target_level or 0)
                seq1.append(cur_b2)
        self.assertEqual(seq1, [100])

        # Scenario 2: Tight energy where Fusion plant L14 needs Kryptonite (not Pig iron, 0 Metal)
        cur_b2_2 = 0
        seq2: list[int] = []
        for tick in range(4):
            e_used = 681 if cur_b2_2 == 0 else 809
            p = PlanetState(
                planet_id=12476,
                name="Colony",
                coords="02:380:12",
                planet_type="normal",
                temperature=31,
                fields_used=55,
                fields_max=230,
                resources=Resources(20000, 1000, 7000, 5000),
                storage=Resources(200000, 200000, 200000, 200000),
                energy=EnergyBalance(814, e_used),
                buildings={"b1": 10, "b2": 12, "b3": 10, "b4": 8, "b5": 13},
                economy_rates={"resource1": 100, "resource2": cur_b2_2, "resource3": 100, "resource4": 100},
            )
            st = GameState(31, 103603, "u", 1000.0 + tick * 400.0, (p,), active_planet_id=12476, speedup_factor=20.0)
            acts = plan_actions(st, config=StrategyConfig(auto_throttle_blast_furnace=True))
            thr = [a for a in acts if a.kind == "THROTTLE_BUILDING"]
            if thr:
                cur_b2_2 = int(thr[0].target_level or 0)
                seq2.append(cur_b2_2)
        self.assertEqual(seq2, [])

    def test_blast_furnace_hysteresis_tight_energy_fusion_absent_unbuildable_and_queue_full(self) -> None:
        cfg = StrategyConfig(auto_throttle_blast_furnace=True)
        base_mines = (
            BuildingOffer(1, "Pig iron mine", 10, 11, Resources(100, 0, 100, 0), True, "Build"),
            BuildingOffer(3, "Kryptonite mine", 11, 12, Resources(100, 0, 100, 0), True, "Build"),
            BuildingOffer(4, "Spice mine", 8, 9, Resources(100, 0, 100, 0), True, "Build"),
            BuildingOffer(2, "Blast furnace", 12, 13, Resources(100, 50000, 100, 0), False, "Need Metal"),
        )
        b2_energy = abs(int(game_data.building_level_stats(2, 12)["energy"]))

        def _run_seq(
            cur0: int, extra_offers: tuple[BuildingOffer, ...], queues: tuple[QueueJob, ...] = ()
        ) -> list[int]:
            cur = cur0
            seq: list[int] = []
            for _ in range(6):
                e_used = 681 if cur == 0 else (681 + b2_energy)
                p = PlanetState(
                    planet_id=12476,
                    name="Colony",
                    coords="02:380:12",
                    planet_type="normal",
                    temperature=31,
                    fields_used=55,
                    fields_max=230,
                    resources=Resources(18000, 10, 50000, 50000),
                    storage=Resources(100000, 100000, 100000, 100000),
                    energy=EnergyBalance(814, e_used),
                    buildings={"b1": 10, "b2": 12, "b3": 11, "b4": 8, "b5": 13, "b7": 10, "b8": 1, "b9": 2},
                    building_offers=base_mines + extra_offers,
                    economy_rates={"resource1": 100, "resource2": cur, "resource3": 100, "resource4": 100},
                )
                st = GameState(
                    31,
                    103603,
                    "u",
                    0.0,
                    (p,),
                    active_planet_id=12476,
                    active_queues=queues,
                    speedup_factor=20.0,
                )
                th = next((a for a in plan_actions(st, config=cfg) if a.kind == "THROTTLE_BUILDING"), None)
                if th is None:
                    break
                cur = int(th.target_level or 0)
                seq.append(cur)
            return seq

        for cur0 in (0, 100):
            # 1. Fusion plant card absent (e.g., include_buildings=False polling)
            self.assertEqual(_run_seq(cur0, ()), [])
            # 2. Fusion plant card present with can_build=False
            fusion_blocked = (BuildingOffer(5, "Fusion plant", 13, 14, Resources(16975, 0, 7427, 0), False, "Wait"),)
            self.assertEqual(_run_seq(cur0, fusion_blocked), [])
            # 3. Fusion plant buildable, but 5/5 construction queue full
            fusion_ok = (BuildingOffer(5, "Fusion plant", 13, 14, Resources(16975, 0, 7427, 0), True, "Build"),)
            full_q = tuple(QueueJob("building", "Pig iron mine", building_id=1, target_level=10) for _ in range(5))
            self.assertEqual(_run_seq(cur0, fusion_ok, queues=full_q), [])

        # 4. Fusion plant buildable & queue free -> cur0=0 resumes [100], cur0=100 stays stable []
        fusion_ok = (BuildingOffer(5, "Fusion plant", 13, 14, Resources(16975, 0, 7427, 0), True, "Build"),)
        self.assertEqual(_run_seq(0, fusion_ok), [100])
        self.assertEqual(_run_seq(100, fusion_ok), [])

    def test_blast_furnace_hysteresis_queued_mine_and_tutorial_zero_energy_stability(self) -> None:
        cfg = StrategyConfig(auto_throttle_blast_furnace=True)
        b2_energy = abs(int(game_data.building_level_stats(2, 12)["energy"]))

        # Case A: Queued Kryptonite mine (b3 -> L12) drains projected_net on tight energy
        for cur0 in (0, 100):
            cur = cur0
            seq: list[int] = []
            for _ in range(6):
                p = PlanetState(
                    planet_id=12476,
                    name="Colony",
                    coords="02:380:12",
                    planet_type="normal",
                    temperature=31,
                    fields_used=55,
                    fields_max=230,
                    resources=Resources(20000, 10, 50000, 50000),
                    storage=Resources(100000, 100000, 100000, 100000),
                    energy=EnergyBalance(814, 681 if cur == 0 else (681 + b2_energy)),
                    buildings={"b1": 10, "b2": 12, "b3": 11, "b4": 8, "b5": 13},
                    economy_rates={"resource1": 100, "resource2": cur, "resource3": 100, "resource4": 100},
                )
                q = (QueueJob("building", "Kryptonite mine", building_id=3, target_level=12),)
                st = GameState(31, 103603, "u", 0.0, (p,), active_planet_id=12476, active_queues=q, speedup_factor=20.0)
                th = next((a for a in plan_actions(st, config=cfg) if a.kind == "THROTTLE_BUILDING"), None)
                if th is None:
                    break
                cur = int(th.target_level or 0)
                seq.append(cur)
            self.assertLessEqual(len(seq), 1, f"Queued mine flip-flop: {seq}")

        # Case B: Tutorial projected_net == 0 with Pig-starved Fusion (pauses from 100 -> [0], stable from 0 -> [])
        tut = TutorialStatus(
            step=3,
            total_steps=11,
            can_complete=False,
            tasks=(
                TutorialTask("Build Blast furnace 4", "Blast furnace", 2, 3, 4, "building"),
                TutorialTask("Build Pig iron warehouse 1", "Pig iron warehouse", 11, 0, 1, "building"),
            ),
        )
        b2_lvl4_energy = abs(int(game_data.building_level_stats(2, 4)["energy"]))
        for cur0, expected in ((0, []), (100, [0])):
            cur = cur0
            seq_tut: list[int] = []
            for _ in range(6):
                p = PlanetState(
                    planet_id=12476,
                    name="Colony",
                    coords="02:380:12",
                    planet_type="normal",
                    temperature=31,
                    fields_used=20,
                    fields_max=230,
                    resources=Resources(50, 500, 500, 500),
                    storage=Resources(100000, 100000, 100000, 100000),
                    energy=EnergyBalance(100, (100 - b2_lvl4_energy) if cur == 0 else 100),
                    buildings={"b1": 4, "b2": 4, "b3": 3, "b4": 2, "b5": 5},
                    economy_rates={"resource1": 100, "resource2": cur, "resource3": 100, "resource4": 100},
                )
                st = GameState(31, 103603, "u", 0.0, (p,), active_planet_id=12476, tutorial=tut, speedup_factor=20.0)
                th = next((a for a in plan_actions(st, config=cfg) if a.kind == "THROTTLE_BUILDING"), None)
                if th is None:
                    break
                cur = int(th.target_level or 0)
                seq_tut.append(cur)
            self.assertEqual(seq_tut, expected)

    def test_blast_furnace_hysteresis_exact_b2_energy_parameter_grid_zero_flip_flops(self) -> None:
        cfg = StrategyConfig(auto_throttle_blast_furnace=True)
        b2_energies = {b2: abs(int(game_data.building_level_stats(2, b2)["energy"])) for b2 in (11, 12)}
        base_offers = (
            BuildingOffer(1, "Pig iron mine", 10, 11, Resources(100, 0, 100, 0), True, "Build"),
            BuildingOffer(3, "Kryptonite mine", 11, 12, Resources(100, 0, 100, 0), True, "Build"),
            BuildingOffer(4, "Spice mine", 8, 9, Resources(100, 0, 100, 0), True, "Build"),
            BuildingOffer(2, "Blast furnace", 12, 13, Resources(100, 50000, 100, 0), False, "Need Metal"),
        )
        wait_metal_offer = (
            BuildingOffer(2, "Blast furnace", 12, 13, Resources(100, 50000, 100, 0), False, "Need Metal"),
        )
        flips = []
        combos = itertools.product(
            (0, 100),
            (11, 12),
            (0, 12000, 18000, 50000),
            (7000, 50000),
            (10, 5000),
            (5.0, 20.0, 40.0),
            ("no_offers", "3_affordable_plus_bf_wait", "metal_wait_p3"),
            ((False, False), (True, False), (True, True)),
        )
        for cur0, b2, pig, kryp, met, sp, mode, (has_fus, can_fus) in combos:
            offers = (
                ()
                if mode == "no_offers"
                else (base_offers if mode == "3_affordable_plus_bf_wait" else wait_metal_offer)
            )
            if has_fus:
                offers = offers + (
                    BuildingOffer(5, "Fusion plant", 13, 14, Resources(16975, 0, 7427, 0), can_fus, "Fusion"),
                )
            cur = cur0
            seq: list[int] = []
            b2_e = b2_energies[b2]
            for _ in range(6):
                e_used = 681 if cur == 0 else (681 + b2_e)
                p = PlanetState(
                    planet_id=12476,
                    name="Colony",
                    coords="02:380:12",
                    planet_type="normal",
                    temperature=31,
                    fields_used=55,
                    fields_max=230,
                    resources=Resources(pig, met, kryp, 50000),
                    storage=Resources(100000, 100000, 100000, 100000),
                    energy=EnergyBalance(814, e_used),
                    buildings={"b1": 10, "b2": b2, "b3": 11, "b4": 8, "b5": 13, "b7": 10, "b8": 1, "b9": 2},
                    building_offers=offers,
                    economy_rates={"resource1": 100, "resource2": cur, "resource3": 100, "resource4": 100},
                )
                st = GameState(31, 103603, "u", 0.0, (p,), active_planet_id=12476, speedup_factor=sp)
                th = next((a for a in plan_actions(st, config=cfg) if a.kind == "THROTTLE_BUILDING"), None)
                if th is None:
                    break
                cur = int(th.target_level or 0)
                seq.append(cur)
            if len(seq) >= 2:
                flips.append((cur0, b2, pig, kryp, met, sp, mode, has_fus, can_fus, seq))
        self.assertEqual(flips, [])

    def test_simulate_combat_empty_state_and_commander_max_ticks_exits_on_repeated_rate_limits(self) -> None:
        bridge = GameApiBridge(
            debug_port=9222,
            log_dir=None,
            client_factory=lambda _port: MagicMock(),
            ensure_session_fn=lambda: None,
        )
        sim_res = bridge.handle_command(
            {
                "command": "simulate_combat",
                "attacker_ships": {"s4": 15, "s1": 2},
                "defender_defense": {"d1": 1},
                "target_resources": {"pig_iron": 8000, "metal": 4000},
            }
        )
        self.assertTrue(sim_res["ok"])
        self.assertTrue(sim_res["simulation"]["safe_to_attack"])

        fake_api = MagicMock(spec=GameApiClient)
        fake_api.get_bot_control.return_value = {"ok": True, "paused": False, "mode_override": None}
        fake_api.health.return_value = {"ok": True, "in_game": True, "rate_limited": True, "retry_after": 45.0}
        fake_api.get_state.side_effect = GameApiError(
            "SpaceInvasion per-minute page-view throttle hit; retry after 45s."
        )
        sleeps: list[float] = []
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text('{"playstyle": "fleeter"}', encoding="utf-8")
            with (
                patch("commander.GameApiClient", return_value=fake_api),
                patch("commander.time.sleep", side_effect=lambda s: sleeps.append(s)),
            ):
                rc = commander.main(
                    [
                        "--api-url",
                        "http://127.0.0.1:8787",
                        "--loop",
                        "--max-ticks",
                        "2",
                        "--config",
                        str(cfg_path),
                        "--log-dir",
                        str(Path(tmp) / "logs"),
                    ]
                )
        self.assertEqual(rc, 0)
        self.assertEqual(fake_api.get_state.call_count, 2)
        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(sleeps[0], 45.0)

    def test_interactive_commands_at_18_and_17_normal_views_post_cooldown_and_b8_empty_research(self) -> None:
        now = [200.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            max_views_per_minute=24,
            reserved_interactive_views=6,
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )

        planets_json = json.dumps(
            {
                "info": {
                    "ts": 1700000000,
                    "user": {"userID": 103603, "nickname": "ventkx86"},
                    "universe": {"id": 31},
                },
                "planets": [
                    {
                        "id": 12476,
                        "name": "Colony",
                        "coords": "02:380:12",
                        "type": "normal",
                        "temperature": 31,
                        "fields": 230,
                        "resources": [10000, 8000, 8000, 4000],
                        "storage": [100000, 100000, 100000, 100000],
                        "energy": {"produced": 400, "used": 120},
                        "buildings": {
                            "b1": {"level": 8, "name": "Pig iron mine"},
                            "b2": {"level": 6, "name": "Blast furnace"},
                            "b3": {"level": 6, "name": "Kryptonite mine"},
                            "b4": {"level": 4, "name": "Spice mine"},
                            "b5": {"level": 9, "name": "Fusion plant"},
                            "b9": {"level": 2, "name": "Weapons factory"},
                        },
                        "ships": {
                            "s1": {"level": 2},
                            "s4": {"level": 6},
                            "s10": {"level": 5},
                        },
                        "defense": {},
                    }
                ],
                "researches": {"r1": {"level": 1}},
                "specs": {},
            }
        )
        buildings_html = """
        <table><tr>
          <td><a href="indexInternal.es?action=internalDetail&type=building&id=1">Pig iron mine</a></td>
          <td>Required: 200 Pig iron 50 Kryptonite</td>
          <td><a class="bau" href="indexInternal.es?action=internalBuildItem&buildingID=1#1">Build level 9</a></td>
        </tr></table>
        """
        fleet_step1_html = """
        <form action="indexInternal.es?action=internalSendFleet" method="post">
          <input type="text" name="ship_4" value="0"/>
          <input type="text" name="ship_10" value="0"/>
          <input type="text" name="gal"/><input type="text" name="sun"/><input type="text" name="pla"/>
          <input type="submit" value="Continue"/>
        </form>
        """
        fleet_step2_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <input type="radio" name="mission" value="1" id="m1"/> Attack
          <input type="radio" name="mission" value="6" id="m6"/> Spying
          <input type="submit" value="Send"/>
        </form>
        """
        galaxy_html = """
        <div>System [02:380]</div>
        <table>
          <tr><td>4</td><td><span class="galaxy_yellow">IdleFarm</span></td><td>Sleepy (i)</td></tr>
        </table>
        """

        class _El:
            def __init__(self, attrs=None, text="", on_click=None):
                self._attrs = dict(attrs or {})
                self._text = text
                self._on_click = on_click
                self.value = self._attrs.get("value", "")
                self.clicks = 0

            def get_attribute(self, name: str) -> str:
                if name == "textContent":
                    return self._text
                if name == "value":
                    return self.value
                return self._attrs.get(name, "")

            def get_dom_attribute(self, name: str):
                return self._attrs.get(name)

            def clear(self) -> None:
                self.value = ""

            def send_keys(self, txt: str) -> None:
                self.value += str(txt)

            def is_selected(self) -> bool:
                return False

            def click(self) -> None:
                self.clicks += 1
                if self._on_click is not None:
                    self._on_click(self)

        class _StatefulBridgeDriver:
            def __init__(self) -> None:
                self.current_url = "https://spaceinvasion.bitmeup.com/indexGame.es?sid=r3http"
                self.active_frame = None
                self.has_json = True
                self.planets_html = "<html><body><script id='json-container'></script></body></html>"
                self.frames = {
                    "F_Navi": {
                        "href": "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalMenu",
                        "html": "<html><body>menu</body></html>",
                    },
                    "F_planets": {
                        "href": "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalPlanetsOverview",
                        "html": self.planets_html,
                    },
                    "F_Hauptframe": {
                        "href": "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome",
                        "html": "<html><body><div>active factor: x20.0</div></body></html>",
                    },
                }
                self.nav_token = None
                self.build_clicks = 0
                self.build_btn = _El(on_click=self._on_build)
                self.s4_inp = _El(attrs={"name": "ship_4", "value": "0"})
                self.s10_inp = _El(attrs={"name": "ship_10", "value": "0"})
                self.gal_inp = _El(attrs={"name": "gal"})
                self.sun_inp = _El(attrs={"name": "sun"})
                self.pla_inp = _El(attrs={"name": "pla"})
                self.step1_btn = _El(on_click=self._on_step1)
                self.step2_btn = _El(on_click=self._on_step2)
                self.r_atk = _El(attrs={"value": "1", "id": "m1", "name": "mission"})
                self.r_spy = _El(attrs={"value": "6", "id": "m6", "name": "mission"})
                self.switch_to = self

            def default_content(self) -> None:
                self.active_frame = None

            def frame(self, frame_el) -> None:
                self.active_frame = frame_el.get_attribute("id")

            def _on_build(self, _el) -> None:
                self.build_clicks += 1
                self.frames["F_Hauptframe"]["html"] = "<div>Construction queue</div>"
                self.nav_token = None

            def _on_step1(self, _el) -> None:
                self.frames["F_Hauptframe"]["href"] = (
                    "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalSendFleet"
                )
                self.frames["F_Hauptframe"]["html"] = fleet_step2_html
                self.nav_token = None

            def _on_step2(self, _el) -> None:
                self.frames["F_Hauptframe"]["html"] = "<div>Fleet dispatched</div>"
                self.nav_token = None

            @property
            def page_source(self) -> str:
                if self.active_frame is None:
                    return "<html></html>"
                return self.frames[self.active_frame]["html"]

            def find_element(self, by: str, value: str):
                if self.active_frame is None:
                    if by == By.ID and value in self.frames:
                        return _El(attrs={"id": value, "src": self.frames[value]["href"]})
                    raise NoSuchElementException(value)
                if self.active_frame == "F_planets" and by == By.ID and value == "json-container":
                    if self.has_json:
                        return _El(text=planets_json)
                    raise NoSuchElementException("missing #json-container")
                if self.active_frame == "F_Navi" and by == By.CSS_SELECTOR and "action=" in value:
                    m_act = re.search(r"action=([a-zA-Z0-9_]+)", value)
                    act = m_act.group(1) if m_act else ""

                    def _on_menu(_el, action_name=act) -> None:
                        self.frames["F_Hauptframe"]["href"] = (
                            f"https://spaceinvasion.bitmeup.com/indexInternal.es?action={action_name}&sid=r3http"
                        )
                        if action_name == "internalBuildings":
                            self.frames["F_Hauptframe"]["html"] = buildings_html
                        elif action_name == "internalFleet":
                            self.frames["F_Hauptframe"]["html"] = fleet_step1_html
                        elif action_name == "internalGalaxy":
                            self.frames["F_Hauptframe"]["html"] = galaxy_html
                        elif action_name == "internalHome":
                            self.frames["F_Hauptframe"]["html"] = (
                                "<html><body><div>active factor: x20.0</div></body></html>"
                            )
                        self.nav_token = None

                    return _El(
                        attrs={"href": f"indexInternal.es?action={act}&sid=r3http"},
                        on_click=_on_menu,
                    )
                if self.active_frame == "F_Hauptframe":
                    if by == By.NAME:
                        return {"gal": self.gal_inp, "sun": self.sun_inp, "pla": self.pla_inp}[value]
                    if by == By.CSS_SELECTOR:
                        if "buildingID=" in value:
                            return self.build_btn
                        if "internalSendFleet" in value and "submit" in value:
                            if "action=internalSendFleet" in self.frames["F_Hauptframe"]["href"]:
                                return self.step2_btn
                            return self.step1_btn
                raise NoSuchElementException(f"{self.active_frame}:{by}={value}")

            def find_elements(self, by: str, value: str):
                if self.active_frame == "F_Hauptframe":
                    if by == By.NAME and value == "ship_4":
                        return [self.s4_inp]
                    if by == By.NAME and value == "ship_10":
                        return [self.s10_inp]
                    if by == By.CSS_SELECTOR and 'input[type="radio"]' in value:
                        return [self.r_atk, self.r_spy]
                return []

            def execute_script(self, script: str, *args):
                if "SI_TPL.menu.sid" in script:
                    return "sid=r3http"
                if "window.__siNavToken = arguments[0]" in script:
                    self.nav_token = args[0]
                    return None
                if "window.__siReloadPending = 1" in script and self.active_frame == "F_planets":
                    self.has_json = True
                    self.frames["F_planets"]["html"] = "<html><body><script id='json-container'></script></body></html>"
                    return None
                if "location.href = arguments[0]" in script:
                    url = str(args[0])
                    self.frames["F_Hauptframe"]["href"] = url
                    if "internalBuildings" in url:
                        self.frames["F_Hauptframe"]["html"] = buildings_html
                    elif "internalFleet" in url:
                        self.frames["F_Hauptframe"]["html"] = fleet_step1_html
                    elif "internalGalaxy" in url:
                        self.frames["F_Hauptframe"]["html"] = galaxy_html
                    elif "internalHome" in url:
                        self.frames["F_Hauptframe"]["html"] = (
                            "<html><body><div>active factor: x20.0</div></body></html>"
                        )
                    self.nav_token = None
                    return None
                if "typeof window.__siNavToken" in script:
                    return {
                        "ready": "complete",
                        "href": self.frames["F_Hauptframe"]["href"],
                        "token": self.nav_token,
                    }
                if "return Boolean(window.__siReloadPending)" in script:
                    return False
                if "return Boolean(document.getElementById('json-container'))" in script:
                    return self.has_json
                if "return document.readyState" in script:
                    return "complete"
                if "location.href" in script:
                    fr = self.active_frame or "F_Hauptframe"
                    return self.frames[fr]["href"]
                return None

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "state" / "backend.db"
            cfg_path = tmp_path / "config.json"
            cfg_path.write_text('{"allow_raids": true}', encoding="utf-8")

            drv = _StatefulBridgeDriver()
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=None,
                db_path=db_path,
                page_limiter=limiter,
                max_limiter_wait=0.5,
                client_factory=lambda _port: GameBrowserClient(
                    drv,
                    page_limiter=limiter,
                    min_nav_delay=(0.0, 0.0),
                    timeout=2.0,
                    max_limiter_wait=0.5,
                ),
                ensure_session_fn=lambda: None,
            )
            bridge._live_executor.action_delay_range = (0.0, 0.0)
            assert bridge._db is not None
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with patch("backend.session.browser.is_debug_port_open", return_value=True):
                    # Prime _last_state once, then reset limiter to test interactive headroom cleanly
                    bridge.get_state(include_buildings=True)

                    # 1. At 18/18 normal views: navigate, build, attack, and spy all succeed inside interactive slots
                    for payload, expect_executed in (
                        ({"command": "navigate", "action": "internalBuildings"}, None),
                        ({"command": "build", "building_id": 1, "dry_run": False}, True),
                        ({"command": "attack", "target_coords": "02:380:04", "dry_run": False}, True),
                        ({"command": "spy", "target_coords": "02:380:04", "dry_run": False}, True),
                    ):
                        now[0] += 100.0
                        limiter.reset()
                        limiter.record_view(18)
                        bridge.ctx.last_snapshot_at = time.monotonic()
                        bridge.ctx.last_buildings_fetched = True
                        drv.frames["F_Hauptframe"]["href"] = (
                            "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome"
                        )
                        resp = requests.post(f"http://127.0.0.1:{port}/command", json=payload, timeout=5)
                        self.assertEqual(resp.status_code, 200, f"{payload} -> {resp.text}")
                        body = resp.json()
                        self.assertTrue(body["ok"])
                        if expect_executed is not None:
                            self.assertEqual(body["executed"], expect_executed, f"{payload} -> {body}")
                        self.assertLessEqual(limiter.views_last_60s(), 24)

                    act_rows = run_pg_query(bridge._db, "SELECT command, executed FROM action_history")
                    raid_rows = run_pg_query(bridge._db, "SELECT target_coords FROM raid_history")
                    self.assertTrue(any(r["command"] == "build" and r["executed"] == 1 for r in act_rows))
                    self.assertTrue(any(r["command"] == "attack" and r["executed"] == 1 for r in act_rows))
                    self.assertTrue(any(r["command"] == "spy" and r["executed"] == 1 for r in act_rows))
                    self.assertTrue(any(r["target_coords"] == "02:380:04" for r in raid_rows))

                    # 2. At 17/18 normal views: live build finishes post-click overview and writes SQLite action_history
                    now[0] += 100.0
                    limiter.reset()
                    limiter.record_view(17)
                    drv.build_clicks = 0
                    bridge.ctx.last_snapshot_at = time.monotonic()
                    bridge.ctx.last_buildings_fetched = True
                    drv.frames["F_Hauptframe"]["href"] = (
                        "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome"
                    )
                    resp_b17 = requests.post(
                        f"http://127.0.0.1:{port}/command",
                        json={"command": "build", "building_id": 1, "dry_run": False},
                        timeout=5,
                    )
                    self.assertEqual(resp_b17.status_code, 200, resp_b17.text)
                    self.assertTrue(resp_b17.json()["executed"])
                    self.assertEqual(drv.build_clicks, 1)
                    b_rows = int(
                        run_pg_query(
                            bridge._db,
                            "SELECT COUNT(*) AS cnt FROM action_history WHERE command='build' AND executed=1",
                        )[0]["cnt"]
                    )
                    self.assertGreaterEqual(b_rows, 2)

                    # 3. Post-cooldown recovery at t=248.0 under 18/18 normal views:
                    # GET /health preserves _throttle_recovery_pending; scan_galaxy recovers cleanly with 0 spurious hits.
                    now[0] = 200.0
                    limiter.reset()
                    limiter.record_view(18)
                    drv.has_json = False
                    drv.frames["F_planets"]["html"] = "<div>You've made more than 30 page views at last minute!</div>"
                    bridge.ctx.last_snapshot_at = 0.0
                    now[0] = 202.0
                    resp_429 = requests.get(f"http://127.0.0.1:{port}/state", timeout=5)
                    self.assertEqual(resp_429.status_code, 429)
                    self.assertEqual(limiter.rate_limit_hits, 1)
                    self.assertTrue(limiter._throttle_recovery_pending)

                    now[0] = 248.0
                    h_resp = requests.get(f"http://127.0.0.1:{port}/health", timeout=5)
                    self.assertEqual(h_resp.status_code, 200)
                    h_body = h_resp.json()
                    self.assertFalse(h_body["rate_limited"])
                    self.assertEqual(h_body["rate_limit_hits"], 1)
                    self.assertTrue(limiter._throttle_recovery_pending)

                    bridge.ctx.last_snapshot_at = time.monotonic()
                    scan_resp = requests.post(
                        f"http://127.0.0.1:{port}/command",
                        json={"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False},
                        timeout=5,
                    )
                    self.assertEqual(scan_resp.status_code, 200, scan_resp.text)
                    self.assertTrue(scan_resp.json()["executed"])
                    self.assertEqual(limiter.rate_limit_hits, 1)
                    self.assertTrue(drv.has_json)
            finally:
                server.shutdown()
                server.server_close()
                bridge._db.close()

        # 4. Minor Observation 3: b8 0 -> 1 with researches={} immediately sets include_research=True within 60s TTL
        p_b8_0 = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=10,
            fields_max=230,
            resources=Resources(5000, 5000, 5000, 5000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(300, 100),
            buildings={"b8": 0},
        )
        p_b8_1 = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=11,
            fields_max=230,
            resources=Resources(5000, 5000, 5000, 5000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(300, 100),
            buildings={"b8": 1},
        )
        st_b8_0 = GameState(
            31, 103603, "u", 100.0, (p_b8_0,), active_planet_id=12476, researches={}, research_offers=()
        )
        st_b8_1 = GameState(
            31, 103603, "u", 101.0, (p_b8_1,), active_planet_id=12476, researches={}, research_offers=()
        )
        fake_res_client = MagicMock()
        fake_res_client.__enter__.return_value = fake_res_client
        fake_res_client.read_full_snapshot.side_effect = [st_b8_0, st_b8_0, st_b8_1]
        bridge_res = GameApiBridge(
            debug_port=9222,
            log_dir=None,
            client_factory=lambda _port: fake_res_client,
            ensure_session_fn=lambda: None,
        )
        bridge_res.get_state(include_buildings=True)
        self.assertTrue(fake_res_client.read_full_snapshot.call_args_list[-1].kwargs["include_research"])
        bridge_res.ctx.last_snapshot_at = 0.0
        bridge_res.get_state(include_buildings=True)
        self.assertFalse(fake_res_client.read_full_snapshot.call_args_list[-1].kwargs["include_research"])
        bridge_res.ctx.last_state = st_b8_1
        bridge_res.ctx.last_snapshot_at = 0.0
        bridge_res.get_state(include_buildings=True)
        self.assertTrue(fake_res_client.read_full_snapshot.call_args_list[-1].kwargs["include_research"])

    def test_all_interactive_commands_within_6_views_and_spio_sync_normal_priority(self) -> None:
        from backend.core.model import (
            ResearchOffer,
            UnitOffer,
        )

        # 1. Verify GameApiBridge executor_max_limiter_wait parameter behavior
        b_def = GameApiBridge(
            debug_port=9222, log_dir=None, client_factory=lambda _p: MagicMock(), ensure_session_fn=lambda: None
        )
        self.assertEqual(b_def._live_executor.max_limiter_wait, 8.0)
        self.assertEqual(b_def._dry_executor.max_limiter_wait, 8.0)
        b_half = GameApiBridge(
            debug_port=9222,
            log_dir=None,
            client_factory=lambda _p: MagicMock(),
            ensure_session_fn=lambda: None,
            max_limiter_wait=0.5,
        )
        self.assertEqual(b_half._live_executor.max_limiter_wait, 0.5)
        b_none = GameApiBridge(
            debug_port=9222,
            log_dir=None,
            client_factory=lambda _p: MagicMock(),
            ensure_session_fn=lambda: None,
            executor_max_limiter_wait=None,
        )
        self.assertIsNone(b_none._live_executor.max_limiter_wait)
        self.assertIsNone(b_none._dry_executor.max_limiter_wait)

        now = [200.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            max_views_per_minute=24,
            reserved_interactive_views=6,
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )

        class _El:
            def __init__(
                self,
                *,
                tag: str = "div",
                attrs: dict[str, str] | None = None,
                text: str = "",
                selected: bool = False,
                displayed: bool = True,
                on_click=None,
                children: list[_El] | None = None,
            ) -> None:
                self.tag_name = tag
                self._attrs = dict(attrs or {})
                self._text = text
                self._selected = selected
                self._displayed = displayed
                self._on_click = on_click
                self._children = list(children or [])
                self.value = self._attrs.get("value", "")

            def get_attribute(self, name: str) -> str:
                if name == "textContent":
                    return self._text
                if name == "value":
                    return self.value
                return self._attrs.get(name, "")

            def get_dom_attribute(self, name: str):
                return self._attrs.get(name)

            def is_selected(self) -> bool:
                return self._selected

            def is_displayed(self) -> bool:
                return self._displayed

            def is_enabled(self) -> bool:
                return True

            def clear(self) -> None:
                self.value = ""

            def send_keys(self, txt: str) -> None:
                self.value += str(txt)

            def click(self) -> None:
                if self._on_click is not None:
                    self._on_click(self)

            def find_element(self, by: str, value: str):
                els = self.find_elements(by, value)
                if els:
                    return els[0]
                raise NoSuchElementException(f"child {by}={value}")

            def find_elements(self, by: str, value: str):
                if by == By.TAG_NAME:
                    return [c for c in self._children if c.tag_name == value]
                if "option" in value:
                    m_val = re.search(r"value\s*=\s*['\"]([^'\"]*)['\"]", value)
                    if m_val:
                        want = m_val.group(1)
                        return [c for c in self._children if c.tag_name == "option" and c.value == want]
                    return [c for c in self._children if c.tag_name == "option"]
                if by == By.CSS_SELECTOR and "submit" in value:
                    return [c for c in self._children if c._attrs.get("type") == "submit"]
                return []

        class _TwoPlanetDriver:
            def __init__(self) -> None:
                self.current_url = "https://spaceinvasion.bitmeup.com/indexGame.es?sid=m2r4"
                self.active_frame: str | None = None
                self.switch_to = self
                self.nav_token: str | None = None
                self.active_planet_id = 12476
                self.instant_builds = True
                self.tutorial_step = 1
                self.tutorial_can_complete = True
                self.unread_spio = 0
                self.page_view_log: list[str] = []
                self.launched_fleets: list[dict[str, object]] = []
                self.researches = {"r1": 1, "r2": 4, "r4": 4}
                self.planets = {
                    12476: {
                        "id": 12476,
                        "name": "Home",
                        "coords": "02:380:12",
                        "type": "normal",
                        "temperature": 31,
                        "fields": 230,
                        "resources": [100000, 100000, 100000, 50000],
                        "storage": [500000, 500000, 500000, 500000],
                        "energy": {"produced": 600, "used": 120},
                        "buildings": {
                            "b1": {"level": 5, "name": "Pig iron mine"},
                            "b2": {"level": 3, "name": "Blast furnace"},
                            "b5": {"level": 9, "name": "Fusion plant"},
                            "b8": {"level": 2, "name": "Research lab"},
                            "b9": {"level": 2, "name": "Weapons factory"},
                        },
                        "ships": {"s1": 2, "s4": 10, "s10": 6},
                        "defense": {},
                        "economy_rates": {"resource2": 100},
                        "building_queue": [],
                    },
                    12477: {
                        "id": 12477,
                        "name": "Colony",
                        "coords": "02:380:14",
                        "type": "normal",
                        "temperature": 24,
                        "fields": 190,
                        "resources": [100000, 100000, 100000, 50000],
                        "storage": [500000, 500000, 500000, 500000],
                        "energy": {"produced": 400, "used": 80},
                        "buildings": {
                            "b1": {"level": 3, "name": "Pig iron mine"},
                            "b2": {"level": 2, "name": "Blast furnace"},
                            "b5": {"level": 7, "name": "Fusion plant"},
                            "b8": {"level": 2, "name": "Research lab"},
                            "b9": {"level": 2, "name": "Weapons factory"},
                        },
                        "ships": {"s1": 1, "s4": 8, "s10": 4},
                        "defense": {},
                        "economy_rates": {"resource2": 100},
                        "building_queue": [],
                    },
                }
                self.frames = {
                    "F_Navi": {
                        "href": "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalMenu",
                        "html": "",
                    },
                    "F_planets": {
                        "href": "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalPlanetsOverview",
                        "html": "<html><body><script id='json-container'></script></body></html>",
                    },
                    "F_Hauptframe": {
                        "href": "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome",
                        "html": "",
                    },
                }
                self._step1_inputs: dict[str, _El] = {}
                self._ship_qty_inp: _El | None = None
                self._econ_pct_sel: _El | None = None
                self._mission_radios: list[_El] = []
                self._render_home()
                self._render_menu()

            def _planets_json(self) -> str:
                return json.dumps(
                    {
                        "info": {
                            "ts": 1700000000,
                            "user": {"userID": 103603, "nickname": "ventkx86"},
                            "universe": {"id": 31},
                        },
                        "planets": [
                            {
                                k: v
                                for k, v in p.items()
                                if k
                                in (
                                    "id",
                                    "name",
                                    "coords",
                                    "type",
                                    "temperature",
                                    "fields",
                                    "resources",
                                    "storage",
                                    "energy",
                                    "buildings",
                                    "defense",
                                )
                            }
                            | {"ships": {sk: {"level": sv} for sk, sv in p["ships"].items()}}
                            for p in self.planets.values()
                        ],
                        "researches": {rk: {"level": rv} for rk, rv in self.researches.items()},
                        "specs": {},
                    }
                )

            def _switcher_html(self, action: str) -> str:
                opts = "".join(
                    f'<option {"selected " if pid == self.active_planet_id else ""}value="indexInternal.es?action={action}&newPlanet={pid}">{p["name"]}</option>'
                    for pid, p in self.planets.items()
                )
                return f'<select name="planet">{opts}</select>'

            def _render_menu(self) -> None:
                spio_lbl = f"Spying ({self.unread_spio})" if self.unread_spio else "Spying"
                self.frames["F_Navi"]["html"] = (
                    f'<html><body><a href="indexInternal.es?action=internalMessages&type=spio">{spio_lbl}</a></body></html>'
                )

            def _render_home(self) -> None:
                self.frames["F_Hauptframe"]["href"] = (
                    "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome&sid=m2r4"
                )
                claim_btn = (
                    '<form action="indexInternal.es?action=internalHome"><input type="hidden" name="tutorialComplete" value="1"/><input type="submit" value="Claim"/></form>'
                    if self.tutorial_can_complete
                    else ""
                )
                self.frames["F_Hauptframe"]["html"] = (
                    f"<script>SI_TPL={{planetID:{self.active_planet_id}}};</script>"
                    f"<div>active factor: x20.0</div><div>Tutorial {self.tutorial_step} / 11</div>"
                    f'<div id="tutorial.text">{claim_btn}</div>'
                )

            def _render_buildings(self) -> None:
                p = self.planets[self.active_planet_id]
                cur_b1 = int(p["buildings"]["b1"]["level"])
                q_items = p["building_queue"]
                q_rows = "".join(f"<tr><td>{q['name']} (Level {q['level']})</td></tr>" for q in q_items)
                q_tbl = f"<table><tr><td>Construction queue ( {len(q_items)} Elements )</td></tr>{q_rows}</table>"
                act_html = (
                    "<span>In construction</span>"
                    if q_items
                    else f'<a class="bau" href="indexInternal.es?action=internalBuildItem&buildingID=1">Build level {cur_b1 + 1}</a>'
                )
                self.frames["F_Hauptframe"]["href"] = (
                    "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalBuildings&sid=m2r4"
                )
                self.frames["F_Hauptframe"]["html"] = (
                    f"<html><body>{self._switcher_html('internalBuildings')}{q_tbl}"
                    f'<table><tr><td><a href="indexInternal.es?action=internalDetail&type=building&id=1">Pig iron mine</a> '
                    f"Required: 200 Pig iron 50 Kryptonite {act_html}</td></tr></table></body></html>"
                )

            def _render_research(self) -> None:
                cur_r1 = int(self.researches["r1"])
                self.frames["F_Hauptframe"]["href"] = (
                    "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalResearch&sid=m2r4"
                )
                self.frames["F_Hauptframe"]["html"] = (
                    f"<html><body>{self._switcher_html('internalResearch')}"
                    f'<table><tr><td><a href="indexInternal.es?action=internalDetail&type=research&id=1">Mining technology</a> '
                    f"Required: 1.000 Pig iron 500 Kryptonite "
                    f'<a class="bau" href="indexInternal.es?action=internalBuildItem&researchID=1">Research level {cur_r1 + 1}</a></td></tr></table></body></html>'
                )

            def _render_shipyard(self) -> None:
                p = self.planets[self.active_planet_id]
                owned = int(p["ships"].get("s1", 0))
                self.frames["F_Hauptframe"]["href"] = (
                    "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalWeaponFactory&sid=m2r4"
                )
                self.frames["F_Hauptframe"]["html"] = (
                    f"<html><body>{self._switcher_html('internalWeaponFactory')}"
                    f'<form action="indexInternal.es?action=internalBuildItem&buildShip=1" method="POST">'
                    f'<table><tr><td><a href="indexInternal.es?action=internalDetail&type=ship&id=1">Small transport</a> '
                    f"Available: {owned} Required: 3.000 Metal 3.000 Kryptonite "
                    f'<input type="text" name="ship[1]" value="0"/></td></tr></table>'
                    f'<input type="submit" value="Build"/></form></body></html>'
                )

            def _render_economy(self) -> None:
                p = self.planets[self.active_planet_id]
                cur = int(p["economy_rates"].get("resource2", 100))
                opts = "".join(
                    f'<option {"selected " if pct == cur else ""}value="{pct}">{pct}%</option>' for pct in (100, 0)
                )
                self.frames["F_Hauptframe"]["href"] = (
                    "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalEconomy&sid=m2r4"
                )
                self.frames["F_Hauptframe"]["html"] = (
                    f"<html><body>{self._switcher_html('internalEconomy')}"
                    f'<form id="ress"><select name="resource2">{opts}</select><input type="submit" value="Save"/></form></body></html>'
                )

            def _render_fleet_step1(self) -> None:
                self._step1_inputs = {
                    "ship_4": _El(tag="input", attrs={"name": "ship_4", "value": "0"}),
                    "ship_10": _El(tag="input", attrs={"name": "ship_10", "value": "0"}),
                    "gal": _El(tag="input", attrs={"name": "gal", "value": ""}),
                    "sun": _El(tag="input", attrs={"name": "sun", "value": ""}),
                    "pla": _El(tag="input", attrs={"name": "pla", "value": ""}),
                }
                self.frames["F_Hauptframe"]["href"] = (
                    "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalFleet&sid=m2r4"
                )
                self.frames["F_Hauptframe"]["html"] = (
                    '<form action="indexInternal.es?action=internalSendFleet">'
                    '<input name="ship_4" value="0"/><input name="ship_10" value="0"/>'
                    '<input name="gal"/><input name="sun"/><input name="pla"/><input type="submit" value="Continue"/></form>'
                )

            def _render_fleet_step2(self) -> None:
                self._mission_radios = [
                    _El(tag="input", attrs={"type": "radio", "name": "mission", "value": "1", "id": "m1"}),
                    _El(tag="input", attrs={"type": "radio", "name": "mission", "value": "6", "id": "m6"}),
                ]
                self.frames["F_Hauptframe"]["href"] = (
                    "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalSendFleet&sid=m2r4"
                )
                self.frames["F_Hauptframe"]["html"] = (
                    '<form action="indexInternal.es?action=internalSendFleet">'
                    '<input type="radio" name="mission" value="1" id="m1"/> Attack '
                    '<input type="radio" name="mission" value="6" id="m6"/> Spying '
                    '<input type="submit" value="Launch"/></form>'
                )

            def default_content(self) -> None:
                self.active_frame = None

            def frame(self, frame_el) -> None:
                self.active_frame = frame_el.get_attribute("id")

            @property
            def page_source(self) -> str:
                if self.active_frame is None:
                    return "<html></html>"
                return self.frames[self.active_frame]["html"]

            def _open_action(self, action_name: str) -> None:
                self.page_view_log.append(action_name)
                self.nav_token = None
                if action_name == "internalHome":
                    self._render_home()
                elif action_name == "internalBuildings":
                    self._render_buildings()
                elif action_name == "internalResearch":
                    self._render_research()
                elif action_name == "internalWeaponFactory":
                    self._render_shipyard()
                elif action_name == "internalEconomy":
                    self._render_economy()
                elif action_name == "internalFleet":
                    self._render_fleet_step1()
                elif action_name == "internalGalaxy":
                    self.frames["F_Hauptframe"]["href"] = (
                        "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&sid=m2r4"
                    )
                    self.frames["F_Hauptframe"]["html"] = (
                        "<div>System [02:380]</div>"
                        '<table><tr><td>4</td><td><span class="galaxy_yellow">Idle</span></td><td>Sleepy (i)</td></tr></table>'
                    )
                elif action_name == "internalMessages":
                    self.unread_spio = 0
                    self._render_menu()
                    self.frames["F_Hauptframe"]["href"] = (
                        "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalMessages&type=spio&sid=m2r4"
                    )
                    self.frames["F_Hauptframe"]["html"] = (
                        "<table><tr><td>02:380:04 Player: Sleepy Pig iron: 9.000 Metal: 4.000 Kryptonite: 2.000 Spice: 500</td></tr></table>"
                    )

            def _switch_planet_option(self, target_pid: int) -> None:
                self.page_view_log.append("switchPlanet")
                self.active_planet_id = target_pid
                self.nav_token = None
                href = self.frames["F_Hauptframe"]["href"]
                if "internalBuildings" in href:
                    self._render_buildings()
                elif "internalResearch" in href:
                    self._render_research()
                elif "internalWeaponFactory" in href:
                    self._render_shipyard()
                elif "internalEconomy" in href:
                    self._render_economy()

            def find_element(self, by: str, value: str):
                if self.active_frame is None:
                    if by == By.ID and value in self.frames:
                        return _El(tag="iframe", attrs={"id": value, "src": self.frames[value]["href"]})
                    raise NoSuchElementException(value)
                if self.active_frame == "F_planets" and by == By.ID and value == "json-container":
                    return _El(tag="script", text=self._planets_json())
                if self.active_frame == "F_Navi" and by == By.CSS_SELECTOR and "action=" in value:
                    m_act = re.search(r"action=([a-zA-Z0-9_]+)", value)
                    act = m_act.group(1) if m_act else ""
                    return _El(
                        tag="a",
                        attrs={"href": f"indexInternal.es?action={act}&sid=m2r4"},
                        on_click=lambda _el, a=act: self._open_action(a),
                    )
                if self.active_frame == "F_Hauptframe":
                    if by == By.ID and value in ("tutorial.text", "tutorial.show"):
                        return _El(tag="div", displayed=True)
                    if by == By.CSS_SELECTOR and value == 'select[name="planet"]':
                        opts = [
                            _El(
                                tag="option",
                                attrs={"value": f"indexInternal.es?action=internalBuildings&newPlanet={pid}"},
                                selected=(pid == self.active_planet_id),
                                on_click=lambda _el, p_id=pid: self._switch_planet_option(p_id),
                            )
                            for pid in self.planets
                        ]
                        return _El(tag="select", attrs={"name": "planet"}, children=opts)
                    if by == By.CSS_SELECTOR and "tutorialComplete" in value:

                        def _on_claim(_el) -> None:
                            self.page_view_log.append("internalHome[submit]")
                            self.tutorial_step += 1
                            self.tutorial_can_complete = False
                            self._render_home()
                            self.nav_token = None

                        return _El(tag="input", attrs={"type": "submit"}, on_click=_on_claim)
                    if by == By.CSS_SELECTOR and "buildingID=" in value:

                        def _on_build(_el) -> None:
                            self.page_view_log.append("internalBuildItem")
                            p = self.planets[self.active_planet_id]
                            nxt = int(p["buildings"]["b1"]["level"]) + 1
                            if self.instant_builds:
                                p["buildings"]["b1"]["level"] = nxt
                            else:
                                p["building_queue"].append({"name": "Pig iron mine", "level": nxt})
                            self._render_buildings()
                            self.nav_token = None

                        return _El(tag="a", on_click=_on_build)
                    if by == By.CSS_SELECTOR and "researchID=" in value:

                        def _on_research(_el) -> None:
                            self.page_view_log.append("internalBuildItem")
                            self.researches["r1"] = int(self.researches["r1"]) + 1
                            self._render_research()
                            self.nav_token = None

                        return _El(tag="a", on_click=_on_research)
                    if by == By.NAME and value == "ship[1]":
                        self._ship_qty_inp = _El(tag="input", attrs={"name": "ship[1]", "value": "0"})
                        return self._ship_qty_inp
                    if by == By.CSS_SELECTOR and 'form[action*="buildShip=1"]' in value:

                        def _on_ship(_el) -> None:
                            self.page_view_log.append("internalBuildItem")
                            qty = int((self._ship_qty_inp.value if self._ship_qty_inp else "1") or 1)
                            p = self.planets[self.active_planet_id]
                            p["ships"]["s1"] = int(p["ships"].get("s1", 0)) + qty
                            self._render_shipyard()
                            self.nav_token = None

                        return _El(
                            tag="form",
                            children=[_El(tag="input", attrs={"type": "submit"}, on_click=_on_ship)],
                        )
                    if by == By.NAME and value == "resource2":
                        p = self.planets[self.active_planet_id]
                        cur = int(p["economy_rates"].get("resource2", 100))
                        opts = [_El(tag="option", attrs={"value": str(pct)}, selected=(pct == cur)) for pct in (100, 0)]
                        for opt in opts:

                            def _sel_opt(_o, target=opt, all_opts=opts):
                                for x in all_opts:
                                    x._selected = x is target

                            opt._on_click = _sel_opt
                        self._econ_pct_sel = _El(tag="select", attrs={"name": "resource2"}, children=opts)
                        return self._econ_pct_sel
                    if by == By.CSS_SELECTOR and "internalEconomy" in value:

                        def _on_econ(_el) -> None:
                            self.page_view_log.append("internalEconomy")
                            chosen = next(
                                (
                                    int(o.value)
                                    for o in (self._econ_pct_sel._children if self._econ_pct_sel else [])
                                    if o._selected
                                ),
                                0,
                            )
                            self.planets[self.active_planet_id]["economy_rates"]["resource2"] = chosen
                            self._render_economy()
                            self.nav_token = None

                        return _El(tag="input", attrs={"type": "submit"}, on_click=_on_econ)
                    if by == By.NAME and value in self._step1_inputs:
                        return self._step1_inputs[value]
                    if by == By.CSS_SELECTOR and "internalSendFleet" in value and "submit" in value:
                        if "action=internalSendFleet" in self.frames["F_Hauptframe"]["href"]:

                            def _on_step2(_el) -> None:
                                self.page_view_log.append("internalSendFleet")
                                mission = next((r.value for r in self._mission_radios if r._selected), "1")
                                gal = int(self._step1_inputs["gal"].value or 1)
                                sun = int(self._step1_inputs["sun"].value or 1)
                                pla = int(self._step1_inputs["pla"].value or 1)
                                coords = f"{gal:02d}:{sun:03d}:{pla:02d}"
                                p = self.planets[self.active_planet_id]
                                sent_ships: dict[str, int] = {}
                                if mission == "6":
                                    cnt = int(self._step1_inputs["ship_10"].value or 1)
                                    p["ships"]["s10"] = max(0, int(p["ships"].get("s10", 0)) - cnt)
                                    sent_ships["s10"] = cnt
                                else:
                                    cnt = int(self._step1_inputs["ship_4"].value or 1)
                                    sent_ships["s4"] = cnt
                                self.launched_fleets.append(
                                    {
                                        "origin_planet_id": self.active_planet_id,
                                        "mission": mission,
                                        "target_coords": coords,
                                        "ships": sent_ships,
                                    }
                                )
                                self.frames["F_Hauptframe"]["html"] = "<div>Fleet dispatched</div>"
                                self.nav_token = None

                            return _El(tag="input", attrs={"type": "submit"}, on_click=_on_step2)

                        def _on_step1(_el) -> None:
                            self.page_view_log.append("internalSendFleet")
                            self._render_fleet_step2()
                            self.nav_token = None

                        return _El(tag="input", attrs={"type": "submit"}, on_click=_on_step1)
                raise NoSuchElementException(f"{self.active_frame}:{by}={value}")

            def find_elements(self, by: str, value: str):
                if self.active_frame == "F_Hauptframe":
                    if by == By.NAME and value in self._step1_inputs:
                        return [self._step1_inputs[value]]
                    if by == By.CSS_SELECTOR and 'input[type="radio"]' in value:
                        for r in self._mission_radios:
                            r._on_click = lambda _el, target=r: (
                                [setattr(x, "_selected", x is target) for x in self._mission_radios]
                            )
                        return list(self._mission_radios)
                return []

            def execute_script(self, script: str, *args):
                if "SI_TPL.menu.sid" in script:
                    return "sid=m2r4"
                if "window.__siNavToken = arguments[0]" in script:
                    self.nav_token = args[0]
                    return None
                if "window.__siReloadPending = 1" in script and self.active_frame == "F_planets":
                    self.page_view_log.append("internalPlanetsOverview")
                    return None
                if "location.href = arguments[0]" in script:
                    url = str(args[0])
                    m_act = re.search(r"action=([a-zA-Z0-9_]+)", url)
                    self._open_action(m_act.group(1) if m_act else "internalHome")
                    return None
                if "typeof window.__siNavToken" in script:
                    return {"ready": "complete", "href": self.frames["F_Hauptframe"]["href"], "token": self.nav_token}
                if "return Boolean(window.__siReloadPending)" in script:
                    return False
                if "return Boolean(document.getElementById('json-container'))" in script:
                    return True
                if "return document.readyState" in script:
                    return "complete"
                if "location.href" in script:
                    fr = self.active_frame or "F_Hauptframe"
                    return self.frames[fr]["href"]
                return None

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "state" / "backend.db"
            cfg_path = tmp_path / "config.json"
            cfg_path.write_text('{"allow_raids": true}', encoding="utf-8")

            drv = _TwoPlanetDriver()
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=None,
                db_path=db_path,
                page_limiter=limiter,
                max_limiter_wait=0.5,
                client_factory=lambda _port: GameBrowserClient(
                    drv,
                    page_limiter=limiter,
                    min_nav_delay=(0.0, 0.0),
                    timeout=2.0,
                    max_limiter_wait=0.5,
                ),
                ensure_session_fn=lambda: None,
            )
            bridge._live_executor.action_delay_range = (0.0, 0.0)
            assert bridge._db is not None
            try:

                def _reset_budget_off_home(*, stale: bool = False, drop_cache: bool = False) -> None:
                    now[0] += 100.0
                    limiter.reset()
                    limiter.record_view(18)
                    drv.page_view_log.clear()
                    drv.frames["F_Hauptframe"]["href"] = (
                        "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&sid=m2r4"
                    )
                    drv.frames["F_Hauptframe"]["html"] = "<div>System [02:380]</div>"
                    if drop_cache:
                        bridge.ctx.last_state = None
                        bridge.ctx.last_snapshot_at = 0.0
                    elif stale:
                        bridge.ctx.last_snapshot_at = time.monotonic() - 25.0
                    else:
                        bridge.ctx.last_snapshot_at = time.monotonic()

                # Prime full cache and record yellow inactive target so BashTracker exempts multi-attack matrix
                bridge._db.record_galaxy_slots([GalaxySlot("02:380:04", 4, "Farm", "Idle", "yellow")], ts=100.0)
                bridge.get_state(include_buildings=True)

                # Matrix of ALL interactive commands at 18/18 normal views (F_Hauptframe != internalHome)
                cases = [
                    ("research_warm", {"command": "research", "research_id": 1, "dry_run": False}, False, False),
                    ("research_stale", {"command": "research", "research_id": 1, "dry_run": False}, True, False),
                    ("research_missing", {"command": "research", "research_id": 1, "dry_run": False}, False, True),
                    (
                        "research_cross",
                        {"command": "research", "research_id": 1, "planet_id": 12477, "dry_run": False},
                        False,
                        False,
                    ),
                    (
                        "build_ship_warm",
                        {"command": "build_ship", "ship_id": 1, "quantity": 2, "dry_run": False},
                        False,
                        False,
                    ),
                    (
                        "build_ship_stale",
                        {"command": "build_ship", "ship_id": 1, "quantity": 2, "dry_run": False},
                        True,
                        False,
                    ),
                    (
                        "build_ship_missing",
                        {"command": "build_ship", "ship_id": 1, "quantity": 2, "dry_run": False},
                        False,
                        True,
                    ),
                    (
                        "build_ship_cross",
                        {"command": "build_ship", "ship_id": 1, "quantity": 2, "planet_id": 12477, "dry_run": False},
                        False,
                        False,
                    ),
                    ("build_warm", {"command": "build", "building_id": 1, "dry_run": False}, False, False),
                    ("build_stale", {"command": "build", "building_id": 1, "dry_run": False}, True, False),
                    ("build_missing", {"command": "build", "building_id": 1, "dry_run": False}, False, True),
                    (
                        "build_cross",
                        {"command": "build", "building_id": 1, "planet_id": 12477, "dry_run": False},
                        False,
                        False,
                    ),
                    (
                        "attack_warm",
                        {"command": "attack", "target_coords": "02:380:04", "dry_run": False},
                        False,
                        False,
                    ),
                    (
                        "attack_stale",
                        {"command": "attack", "target_coords": "02:380:04", "dry_run": False},
                        True,
                        False,
                    ),
                    (
                        "attack_missing",
                        {"command": "attack", "target_coords": "02:380:04", "dry_run": False},
                        False,
                        True,
                    ),
                    (
                        "attack_cross",
                        {"command": "attack", "target_coords": "02:380:04", "planet_id": 12477, "dry_run": False},
                        False,
                        False,
                    ),
                    ("spy_warm", {"command": "spy", "target_coords": "02:380:04", "dry_run": False}, False, False),
                    ("spy_stale_auto", {"command": "spy", "target_coords": "02:380:04", "dry_run": False}, True, False),
                    ("spy_missing", {"command": "spy", "target_coords": "02:380:04", "dry_run": False}, False, True),
                    (
                        "spy_cross",
                        {"command": "spy", "target_coords": "02:380:04", "planet_id": 12477, "dry_run": False},
                        False,
                        False,
                    ),
                    (
                        "throttle_stale_cross",
                        {
                            "command": "throttle_building",
                            "building_id": 2,
                            "percentage": 0,
                            "planet_id": 12477,
                            "dry_run": False,
                        },
                        True,
                        False,
                    ),
                    (
                        "scan_uncached",
                        {"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False},
                        False,
                        True,
                    ),
                ]
                for label, payload, stale, drop_cache in cases:
                    drv.active_planet_id = 12476
                    if bridge.ctx.last_state is not None:
                        bridge.ctx.last_state = replace(bridge.ctx.last_state, active_planet_id=12476)
                    _reset_budget_off_home(stale=stale, drop_cache=drop_cache)
                    res = bridge.handle_command(payload)
                    used = limiter.views_last_60s() - 18
                    self.assertTrue(res["ok"], f"{label} -> {res}")
                    self.assertTrue(res["executed"], f"{label} not executed: {res}")
                    self.assertLessEqual(used, 6, f"{label} used {used} > 6 views: {drv.page_view_log}")
                    self.assertLessEqual(limiter.views_last_60s(), 24, label)
                    if label == "scan_uncached":
                        self.assertNotIn("internalHome", drv.page_view_log)
                    if label.startswith("spy_"):
                        st_p = next(p for p in res["state"]["planets"] if p["planet_id"] == drv.active_planet_id)
                        self.assertEqual(st_p["ships"]["s10"], drv.planets[drv.active_planet_id]["ships"]["s10"])

                # Stale "Not enough resources" / "Locked / insufficient" cached offers still live-verify cleanly
                assert bridge.ctx.last_state is not None
                p0 = bridge.ctx.last_state.planets[0]
                stale_res_offer = ResearchOffer(
                    1, "r1", "Mining technology", 1, 2, Resources(1000, 0, 500, 0), False, "Not enough resources"
                )
                stale_ship_offer = UnitOffer(
                    "ship", 1, "Small transport", 2, Resources(0, 3000, 3000, 0), False, None, "Locked / insufficient"
                )
                bridge.ctx.last_state = replace(
                    bridge.ctx.last_state,
                    planets=(replace(p0, ship_offers=(stale_ship_offer,)), bridge.ctx.last_state.planets[1]),
                    research_offers=(stale_res_offer,),
                )
                for label, payload in (
                    ("research_stale_offer", {"command": "research", "research_id": 1, "dry_run": False}),
                    ("ship_stale_offer", {"command": "build_ship", "ship_id": 1, "quantity": 1, "dry_run": False}),
                ):
                    _reset_budget_off_home(stale=False, drop_cache=False)
                    res = bridge.handle_command(payload)
                    self.assertTrue(res["executed"], label)
                    self.assertLessEqual(limiter.views_last_60s(), 24, label)

                # Queued (non-instant) build updates active_queues cleanly at 18/18
                drv.instant_builds = False
                _reset_budget_off_home(stale=True, drop_cache=False)
                q_res = bridge.handle_command({"command": "build", "building_id": 1, "dry_run": False})
                self.assertTrue(q_res["executed"])
                self.assertTrue(any(q["category"] == "building" for q in q_res["state"]["active_queues"]))
                self.assertLessEqual(limiter.views_last_60s(), 24)
                drv.instant_builds = True

                # Claim tutorial (warm and missing cache) updates step -> 2 without double internalHome
                for drop_c in (False, True):
                    drv.tutorial_step = 1
                    drv.tutorial_can_complete = True
                    _reset_budget_off_home(stale=False, drop_cache=drop_c)
                    if not drop_c:
                        bridge.ctx.last_state = replace(
                            bridge.ctx.last_state,
                            tutorial=TutorialStatus(step=1, total_steps=11, can_complete=True),
                        )
                    t_res = bridge.handle_command({"command": "claim_tutorial", "dry_run": False})
                    self.assertTrue(t_res["executed"], f"drop_c={drop_c}: {t_res}")
                    self.assertEqual(t_res["state"]["tutorial"]["step"], 2)
                    self.assertLessEqual(limiter.views_last_60s(), 24)
                    self.assertLessEqual(drv.page_view_log.count("internalHome"), 2)

                # Reverse cross-planet switch sequence: 12477 -> uncached scan_galaxy -> stale -> 12476
                drv.active_planet_id = 12476
                _reset_budget_off_home(stale=False, drop_cache=False)
                bridge.ctx.last_state = replace(bridge.ctx.last_state, active_planet_id=12476)
                r1 = bridge.handle_command(
                    {"command": "attack", "target_coords": "02:380:04", "planet_id": 12477, "dry_run": False}
                )
                self.assertTrue(r1["executed"])
                self.assertEqual(drv.launched_fleets[-1]["origin_planet_id"], 12477)

                _reset_budget_off_home(stale=True, drop_cache=False)
                r_scan = bridge.handle_command(
                    {"command": "scan_galaxy", "galaxy": 2, "system": 380, "refresh": True, "dry_run": False}
                )
                self.assertTrue(r_scan["executed"])
                self.assertEqual(r_scan["state"]["active_planet_id"], 12477)

                _reset_budget_off_home(stale=True, drop_cache=False)
                r2 = bridge.handle_command(
                    {"command": "attack", "target_coords": "02:380:04", "planet_id": 12476, "dry_run": False}
                )
                self.assertTrue(r2["executed"])
                self.assertEqual(drv.launched_fleets[-1]["origin_planet_id"], 12476)
                self.assertLessEqual(limiter.views_last_60s(), 24)

                # _sync_spio_if_unread priority isolation: cached plan at 18/18 with unread_spio > 0
                # MUST use priority="normal" and NEVER touch the 6 reserved interactive slots.
                _reset_budget_off_home(stale=False, drop_cache=False)
                drv.unread_spio = 3
                drv._render_menu()
                bridge.ctx.last_spio_sync_at = 0.0
                bridge.ctx.last_buildings_fetched = True
                bridge.ctx.last_state = replace(bridge.ctx.last_state, unread_messages={"spio": 3})
                with self.assertRaises(RateLimitError) as ctx_plan:
                    bridge.handle_command({"command": "plan"})
                self.assertFalse(ctx_plan.exception.server_throttle)
                self.assertEqual(limiter.views_last_60s(), 18)

                sync_res = bridge.handle_command({"command": "sync_messages"})
                self.assertTrue(sync_res["ok"])
                self.assertEqual(sync_res["synced_reports"], 1)
                self.assertEqual(limiter.views_last_60s(), 19)
            finally:
                bridge._db.close()

    def test_cross_planet_noop_abort_exception_and_cold_offhome_fleet_stay_synced_within_6_views(self) -> None:
        from tests.e2e.harness import FakeClock, FakeDriver, FakeSpaceInvasionServer

        def _make_two_planet_env(tmp_dir: Path, active_pid: int):
            clock = FakeClock()
            srv = FakeSpaceInvasionServer(
                planets={
                    12476: {
                        "planet_id": 12476,
                        "name": "Home",
                        "coords": "02:380:12",
                        "planet_type": "normal",
                        "temperature": 31,
                        "fields_max": 230,
                        "resources": [100000, 100000, 100000, 50000],
                        "storage": [500000, 500000, 500000, 500000],
                        "energy": {"produced": 600, "used": 120},
                        "buildings": {"b1": 5, "b2": 3, "b5": 9, "b8": 2, "b9": 2},
                        "ships": {"s1": 2, "s4": 10, "s10": 6},
                        "defense": {},
                        "economy_rates": {
                            "resource1": 100,
                            "resource2": 100,
                            "resource3": 100,
                            "resource4": 100,
                            "resource5": 100,
                        },
                        "building_queue": [],
                    },
                    12477: {
                        "planet_id": 12477,
                        "name": "Colony",
                        "coords": "02:380:14",
                        "planet_type": "normal",
                        "temperature": 24,
                        "fields_max": 190,
                        "resources": [100000, 100000, 100000, 50000],
                        "storage": [500000, 500000, 500000, 500000],
                        "energy": {"produced": 400, "used": 80},
                        "buildings": {"b1": 3, "b2": 2, "b5": 7, "b8": 5, "b9": 1},
                        "ships": {"s1": 1, "s4": 8, "s10": 4},
                        "defense": {},
                        "economy_rates": {
                            "resource1": 100,
                            "resource2": 100,
                            "resource3": 100,
                            "resource4": 100,
                            "resource5": 100,
                        },
                        "building_queue": [],
                    },
                },
                active_planet_id=active_pid,
                clock=clock,
            )
            tmp_dir.mkdir(parents=True, exist_ok=True)
            cfg = tmp_dir / "config.json"
            cfg.write_text('{"allow_raids": true, "allow_galaxy_scan": true}', encoding="utf-8")
            drv = FakeDriver(srv)
            lim = PageViewLimiter(
                min_delay_range=(0.0, 0.0),
                max_views_per_minute=24,
                reserved_interactive_views=6,
                clock=clock.monotonic,
                sleeper=clock.sleep,
            )
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg,
                log_dir=None,
                db_path=tmp_dir / "state" / "backend.db",
                page_limiter=lim,
                max_limiter_wait=0.5,
                client_factory=lambda _p: GameBrowserClient(
                    drv,
                    page_limiter=lim,
                    min_nav_delay=(0.0, 0.0),
                    timeout=2.0,
                    max_limiter_wait=0.5,
                ),
                ensure_session_fn=lambda: None,
            )
            bridge._live_executor.action_delay_range = (0.0, 0.0)
            bridge._db.record_galaxy_slots([GalaxySlot("02:380:04", 4, "Farm", "Idle", "yellow")], ts=100.0)
            return clock, srv, drv, lim, bridge

        def _at_18(lim: PageViewLimiter, clock: FakeClock, drv: FakeDriver, action: str = "internalGalaxy") -> None:
            clock.advance(100.0)
            lim.reset()
            lim.record_view(18)
            drv._navigate_frame("F_Hauptframe", f"indexInternal.es?action={action}")

        with tempfile.TemporaryDirectory() as tmp:
            # Edge 3A (no-op throttle + aborted cross-planet build) -> attack(12476)
            clock, srv, drv, lim, bridge = _make_two_planet_env(Path(tmp) / "e3a", 12476)
            try:
                bridge.get_state(include_buildings=True)
                _at_18(lim, clock, drv)
                r_noop = bridge.handle_command(
                    {
                        "command": "throttle_building",
                        "building_id": 2,
                        "percentage": 100,
                        "planet_id": 12477,
                        "dry_run": False,
                    }
                )
                self.assertFalse(r_noop["executed"])
                self.assertEqual(srv.active_planet_id, 12477)
                self.assertEqual(bridge.ctx.last_state.active_planet_id, 12477)
                self.assertLessEqual(lim.views_last_60s() - 18, 6)

                _at_18(lim, clock, drv)
                r_atk = bridge.handle_command(
                    {"command": "attack", "target_coords": "02:380:04", "planet_id": 12476, "dry_run": False}
                )
                self.assertTrue(r_atk["executed"], r_atk)
                self.assertLessEqual(lim.views_last_60s() - 18, 6)
                self.assertEqual(srv.launched_fleets[-1]["origin_planet_id"], 12476)

                # Aborted cross-planet build after switch -> spy(12476)
                orig_render_b = srv._render_buildings_html
                srv._render_buildings_html = lambda ap, sq_amp, built_msg="": (
                    "",
                    f"<html><body>{srv._planet_switcher_html('internalBuildings')}<div>No build link</div></body></html>",
                )
                _at_18(lim, clock, drv)
                r_b_abort = bridge.handle_command(
                    {"command": "build", "building_id": 1, "planet_id": 12477, "dry_run": False}
                )
                srv._render_buildings_html = orig_render_b
                self.assertFalse(r_b_abort["executed"])
                self.assertEqual(bridge.ctx.last_state.active_planet_id, 12477)
                _at_18(lim, clock, drv)
                r_spy = bridge.handle_command(
                    {"command": "spy", "target_coords": "02:380:04", "planet_id": 12476, "dry_run": False}
                )
                self.assertTrue(r_spy["executed"], r_spy)
                self.assertLessEqual(lim.views_last_60s() - 18, 6)
                self.assertEqual(srv.launched_fleets[-1]["origin_planet_id"], 12476)
            finally:
                bridge._db.close()

            # Edge 3B (aborted fleet dispatch) + RateLimitError mid-switch -> attack(12476)
            clock2, srv2, drv2, lim2, bridge2 = _make_two_planet_env(Path(tmp) / "e3b", 12476)
            try:
                orig_launch = srv2._handle_fleet_launch
                srv2._handle_fleet_launch = lambda ap, fd, sq: (
                    "indexInternal.es?action=internalSendFleet",
                    "<div>No free fleet slot.</div>",
                )
                bridge2.get_state(include_buildings=True)
                _at_18(lim2, clock2, drv2)
                r_abort = bridge2.handle_command(
                    {"command": "attack", "target_coords": "02:380:04", "planet_id": 12477, "dry_run": False}
                )
                self.assertFalse(r_abort["executed"])
                self.assertEqual(bridge2.ctx.last_state.active_planet_id, 12477)
                srv2._handle_fleet_launch = orig_launch
                _at_18(lim2, clock2, drv2)
                r_atk2 = bridge2.handle_command(
                    {"command": "attack", "target_coords": "02:380:04", "planet_id": 12476, "dry_run": False}
                )
                self.assertTrue(r_atk2["executed"], r_atk2)
                self.assertLessEqual(lim2.views_last_60s() - 18, 6)
                self.assertEqual(srv2.launched_fleets[-1]["origin_planet_id"], 12476)

                orig_render = srv2._render_frame_inner

                def _throttle_mid_switch(fid, url, form_data=None):
                    if fid == "F_Hauptframe" and "newPlanet=12477" in url:
                        srv2.active_planet_id = 12477
                        return url, "<div>You've made more than 30 page views at last minute.</div>"
                    return orig_render(fid, url, form_data=form_data)

                srv2._render_frame_inner = _throttle_mid_switch
                _at_18(lim2, clock2, drv2)
                with self.assertRaises(RateLimitError):
                    bridge2.handle_command(
                        {"command": "attack", "target_coords": "02:380:04", "planet_id": 12477, "dry_run": False}
                    )
                self.assertFalse(bridge2.ctx.active_planet_confirmed)
                srv2._render_frame_inner = orig_render
                _at_18(lim2, clock2, drv2)
                r_atk3 = bridge2.handle_command(
                    {"command": "attack", "target_coords": "02:380:04", "planet_id": 12476, "dry_run": False}
                )
                self.assertTrue(r_atk3["executed"], r_atk3)
                self.assertLessEqual(lim2.views_last_60s() - 18, 6)
                self.assertEqual(srv2.launched_fleets[-1]["origin_planet_id"], 12476)
            finally:
                bridge2._db.close()

            # Edge 3C: cold bridge on internalGalaxy with server=12477, stale scan_galaxy -> attack(12476) + navigate(internalEconomy)
            clock3, srv3, drv3, lim3, bridge3 = _make_two_planet_env(Path(tmp) / "e3c", 12477)
            try:
                drv3._navigate_frame("F_Hauptframe", "indexInternal.es?action=internalGalaxy")
                bridge3.handle_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
                self.assertFalse(bridge3.ctx.active_planet_confirmed)
                clock3.advance(25.0)
                bridge3.ctx.last_snapshot_at -= 25.0
                _at_18(lim3, clock3, drv3, action="internalGalaxy")
                r_cold = bridge3.handle_command(
                    {"command": "attack", "target_coords": "02:380:04", "planet_id": 12476, "dry_run": False}
                )
                self.assertTrue(r_cold["executed"], r_cold)
                self.assertLessEqual(lim3.views_last_60s() - 18, 6)
                self.assertEqual(srv3.launched_fleets[-1]["origin_planet_id"], 12476)
                self.assertTrue(bridge3.ctx.active_planet_confirmed)

                # navigate(internalEconomy) syncs active planet & economy rates cleanly
                srv3.active_planet_id = 12477
                srv3.planets[12477]["economy_rates"]["resource2"] = 60
                _at_18(lim3, clock3, drv3, action="internalGalaxy")
                bridge3.handle_command({"command": "navigate", "action": "internalEconomy"})
                self.assertEqual(bridge3.ctx.last_state.active_planet_id, 12477)
                self.assertEqual(bridge3.ctx.economy_rates[12477]["resource2"], 60)
                self.assertTrue(bridge3.ctx.active_planet_confirmed)
            finally:
                bridge3._db.close()

    def test_home_freshness_ttl_queue_clearing_prereq_cache_and_ship_qty_affordability(self) -> None:
        from tests.e2e.harness import FakeClock, FakeDriver, FakeSpaceInvasionServer
        from backend.core.model import (
            ResearchOffer,
            UnitOffer,
        )

        def _make_env(tmp_dir: Path, *, planets=None, researches=None, tutorial=None, hostile_alerts=None):
            clock = FakeClock(initial=1000.0)
            srv = FakeSpaceInvasionServer(
                planets=planets,
                active_planet_id=12476,
                researches=researches,
                tutorial=tutorial,
                hostile_alerts=hostile_alerts,
                clock=clock,
            )
            tmp_dir.mkdir(parents=True, exist_ok=True)
            cfg = tmp_dir / "config.json"
            cfg.write_text('{"allow_raids": true, "allow_galaxy_scan": true}', encoding="utf-8")
            drv = FakeDriver(srv)
            lim = PageViewLimiter(
                min_delay_range=(0.0, 0.0),
                max_views_per_minute=24,
                reserved_interactive_views=6,
                clock=clock.monotonic,
                sleeper=clock.sleep,
            )
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg,
                log_dir=None,
                db_path=tmp_dir / "state" / "backend.db",
                page_limiter=lim,
                max_limiter_wait=0.5,
                client_factory=lambda _p: GameBrowserClient(
                    drv,
                    page_limiter=lim,
                    min_nav_delay=(0.0, 0.0),
                    timeout=2.0,
                    max_limiter_wait=0.5,
                ),
                ensure_session_fn=lambda: None,
            )
            bridge._live_executor.action_delay_range = (0.0, 0.0)
            return clock, srv, drv, lim, bridge

        class _TimeProxy:
            def __init__(self, c: FakeClock) -> None:
                self._c = c

            def monotonic(self) -> float:
                return self._c.monotonic()

            def time(self) -> float:
                return self._c.time()

            def sleep(self, secs: float) -> None:
                self._c.sleep(secs)

            def __getattr__(self, name: str):
                return getattr(time, name)

        with tempfile.TemporaryDirectory() as tmp:
            # 1A & 1B & 1E: Cold scan_galaxy -> claim_tutorial, cold build(dry_run=True) -> plan, navigate(internalEconomy)
            clock, srv, drv, lim, bridge = _make_env(
                Path(tmp) / "home1",
                tutorial={
                    "step": 1,
                    "total_steps": 11,
                    "can_complete": True,
                    "tasks": [],
                    "reward_text": "5000 Pig iron",
                },
                hostile_alerts=["Hostile Attack from 02:380:99 arriving in 00:05:00"],
            )
            try:
                with (
                    patch("backend.runtime.bridge.state_cache.time", _TimeProxy(clock)),
                    patch("backend.runtime.bridge._exec_after_state.time", _TimeProxy(clock)),
                    patch("backend.runtime.api.handlers._browser_session.time", _TimeProxy(clock)),
                ):
                    lim.record_view(18)
                    drv._navigate_frame("F_Hauptframe", "indexInternal.es?action=internalGalaxy")
                    r_scan = bridge.handle_command(
                        {"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False}
                    )
                    self.assertTrue(r_scan["executed"])
                    self.assertFalse(bridge.ctx.last_home_fetched)
                    self.assertLessEqual(lim.views_last_60s(), 24)
                    clock.advance(1.0)
                    lim.reset()
                    lim.record_view(18)
                    r_claim = bridge.handle_command({"command": "claim_tutorial", "dry_run": False})
                    self.assertTrue(r_claim["executed"], r_claim)
                    self.assertEqual(r_claim["state"]["tutorial"]["step"], 2)
                    self.assertLessEqual(lim.views_last_60s(), 24)
            finally:
                bridge._db.close()

            clock_b, srv_b, drv_b, lim_b, bridge_b = _make_env(
                Path(tmp) / "home2",
                tutorial={
                    "step": 1,
                    "total_steps": 11,
                    "can_complete": True,
                    "tasks": [],
                    "reward_text": "5000 Pig iron",
                },
                hostile_alerts=["Hostile Attack from 02:380:99 arriving in 00:05:00"],
            )
            try:
                with (
                    patch("backend.runtime.bridge.state_cache.time", _TimeProxy(clock_b)),
                    patch("backend.runtime.bridge._exec_after_state.time", _TimeProxy(clock_b)),
                    patch("backend.runtime.api.handlers._browser_session.time", _TimeProxy(clock_b)),
                ):
                    drv_b._navigate_frame("F_Hauptframe", "indexInternal.es?action=internalGalaxy")
                    bridge_b.handle_command({"command": "build", "building_id": 1, "dry_run": True})
                    self.assertFalse(bridge_b.ctx.last_home_fetched)
                    r_plan = bridge_b.handle_command({"command": "plan"})
                    kinds = [a["kind"] for a in r_plan["actions"]]
                    self.assertIn("ALERT_HOSTILE", kinds)
                    self.assertIn("CLAIM_TUTORIAL", kinds)

                    # 1C / 1D / 1F: 15s Home TTL expires across non-home refresh & live build
                    srv_b.hostile_alerts.clear()
                    clock_b.advance(30.0)
                    lim_b.reset()
                    bridge_b.handle_command({"command": "plan"})
                    clock_b.advance(10.0)
                    r_live_b = bridge_b.handle_command({"command": "build", "building_id": 1, "dry_run": False})
                    self.assertTrue(r_live_b["executed"])
                    clock_b.advance(6.0)  # 16s since home fetch, 6s since live build
                    srv_b.hostile_alerts.append("Incoming raid at 02:380:12")
                    r_plan_after = bridge_b.handle_command({"command": "plan"})
                    self.assertIn("ALERT_HOSTILE", [a["kind"] for a in r_plan_after["actions"]])

                    # 1E: navigate(internalEconomy) preserves _last_home_fetched=False
                    clock_b.advance(20.0)
                    srv_b.hostile_alerts.clear()
                    drv_b._navigate_frame("F_Hauptframe", "indexInternal.es?action=internalGalaxy")
                    bridge_b.ctx.last_state = None
                    bridge_b.handle_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
                    bridge_b.handle_command({"command": "navigate", "action": "internalEconomy"})
                    self.assertFalse(bridge_b.ctx.last_home_fetched)
                    srv_b.hostile_alerts.append("Economy check alert")
                    r_plan_econ = bridge_b.handle_command({"command": "plan"})
                    self.assertIn("ALERT_HOSTILE", [a["kind"] for a in r_plan_econ["actions"]])

                    # 1F: Stale carried-over tutorial does not block >15s home refresh on claim_tutorial
                    clock_b.advance(20.0)
                    srv_b.tutorial["can_complete"] = True
                    bridge_b.handle_command({"command": "build", "building_id": 1, "dry_run": True})
                    clock_b.advance(1.0)
                    r_claim2 = bridge_b.handle_command({"command": "claim_tutorial", "dry_run": False})
                    self.assertTrue(r_claim2["executed"], r_claim2)
            finally:
                bridge_b._db.close()

            # 2A / 2B: active_queues clears stale completed building/research jobs while retaining fleet
            clock_q, srv_q, drv_q, lim_q, bridge_q = _make_env(Path(tmp) / "queues")
            try:
                assert bridge_q.ctx.last_state is None
                st_q = bridge_q.get_state(include_buildings=True)
                self.assertTrue(st_q)
                bridge_q.ctx.last_state = replace(
                    bridge_q.ctx.last_state,
                    active_queues=(
                        QueueJob("building", "Pig iron mine", building_id=1, target_level=6),
                        QueueJob("research", "Mining technology", target_level=2),
                        QueueJob("fleet", "Outgoing raid"),
                    ),
                )
                # Empty building queue on live build drops "building" while keeping "research" & "fleet"
                r_qb = bridge_q.handle_command({"command": "build", "building_id": 1, "dry_run": False})
                self.assertTrue(r_qb["executed"])
                cats_b = [q["category"] for q in r_qb["state"]["active_queues"]]
                self.assertNotIn("building", cats_b)
                self.assertIn("fleet", cats_b)
                # _record_state with fetched_research=True & empty active_queues drops "research" while keeping "fleet"
                rec_r = bridge_q.state_cache.record_state(
                    replace(bridge_q.ctx.last_state, active_queues=()),
                    fetched_research=True,
                    fetched_home=False,
                )
                cats_r = [q.category for q in rec_r.active_queues]
                self.assertEqual(cats_r, ["fleet"])
            finally:
                bridge_q._db.close()

            # Edge 3D, 3E, 4A, and _find_ship_action qty > 1
            planets_tech = {
                12476: {
                    "planet_id": 12476,
                    "name": "Home",
                    "coords": "02:380:12",
                    "planet_type": "normal",
                    "temperature": 31,
                    "fields_max": 230,
                    "resources": [100000, 100000, 100000, 50000],
                    "storage": [500000, 500000, 500000, 500000],
                    "energy": {"produced": 600, "used": 100},
                    "buildings": {"b1": 5, "b5": 8, "b7": 3, "b8": 1, "b9": 0},
                    "ships": {},
                    "defense": {},
                    "economy_rates": {
                        "resource1": 100,
                        "resource2": 100,
                        "resource3": 100,
                        "resource4": 100,
                        "resource5": 100,
                    },
                    "building_queue": [],
                },
                12477: {
                    "planet_id": 12477,
                    "name": "Colony",
                    "coords": "02:380:14",
                    "planet_type": "normal",
                    "temperature": 24,
                    "fields_max": 190,
                    "resources": [100000, 100000, 100000, 50000],
                    "storage": [500000, 500000, 500000, 500000],
                    "energy": {"produced": 600, "used": 100},
                    "buildings": {"b1": 4, "b5": 8, "b7": 3, "b8": 5, "b9": 0},
                    "ships": {},
                    "defense": {},
                    "economy_rates": {
                        "resource1": 100,
                        "resource2": 100,
                        "resource3": 100,
                        "resource4": 100,
                        "resource5": 100,
                    },
                    "building_queue": [],
                },
            }
            clock_t, srv_t, drv_t, lim_t, bridge_t = _make_env(
                Path(tmp) / "prereq",
                planets=planets_tech,
                researches={"r1": 1, "r2": 0, "r3": 0, "r4": 3},
            )
            try:
                # Edge 3D: 12476 has b8=1 (r1 Unavailable), 12477 has b8=5; r2=4
                srv_t.researches["r2"] = 4
                bridge_t.get_state(include_buildings=True)
                self.assertFalse(
                    next(o for o in bridge_t.ctx.last_state.research_offers if o.research_id == 1).can_research
                )
                r_3d_fail = bridge_t.handle_command(
                    {"command": "research", "research_id": 1, "planet_id": 12476, "dry_run": False}
                )
                self.assertFalse(r_3d_fail["executed"])
                clock_t.advance(100.0)
                lim_t.reset()
                lim_t.record_view(18)
                r_3d_ok = bridge_t.handle_command(
                    {"command": "research", "research_id": 1, "planet_id": 12477, "dry_run": False}
                )
                self.assertTrue(r_3d_ok["executed"], r_3d_ok)
                self.assertLessEqual(lim_t.views_last_60s() - 18, 6)

                # Edge 3E: b8 0 -> 1 aborts r1 (0 views), b8 1 -> 2 clears research_offers & unlocks r1
                clock_t.advance(100.0)
                lim_t.reset()
                srv_t.active_planet_id = 12476
                srv_t.planets[12476]["buildings"]["b8"] = 0
                srv_t.researches["r1"] = 1
                bridge_t.ctx.last_state = None
                bridge_t.get_state(include_buildings=True)
                bridge_t.handle_command({"command": "build", "building_id": 8, "planet_id": 12476, "dry_run": False})
                v_before = lim_t.views_last_60s()
                r_e3_locked = bridge_t.handle_command(
                    {"command": "research", "research_id": 1, "planet_id": 12476, "dry_run": False}
                )
                self.assertFalse(r_e3_locked["executed"])
                self.assertEqual(lim_t.views_last_60s(), v_before)

                bridge_t.handle_command({"command": "build", "building_id": 8, "planet_id": 12476, "dry_run": False})
                self.assertEqual(bridge_t.ctx.last_state.research_offers, ())
                r_e3_dry = bridge_t.handle_command(
                    {"command": "research", "research_id": 1, "planet_id": 12476, "dry_run": True}
                )
                self.assertTrue(r_e3_dry["action"]["executable_now"])
                clock_t.advance(100.0)
                lim_t.reset()
                lim_t.record_view(18)
                r_e3_live = bridge_t.handle_command(
                    {"command": "research", "research_id": 1, "planet_id": 12476, "dry_run": False}
                )
                self.assertTrue(r_e3_live["executed"], r_e3_live)
                self.assertLessEqual(lim_t.views_last_60s() - 18, 6)

                # Edge 4A: Research r2 (0 -> 1) invalidates cached building_offers so build(b9) succeeds
                clock_t.advance(100.0)
                lim_t.reset()
                srv_t.researches["r2"] = 0
                bridge_t.ctx.last_state = None
                bridge_t.get_state(include_buildings=True)
                self.assertFalse(
                    next(
                        o for o in bridge_t.ctx.last_state.active_planet().building_offers if o.building_id == 9
                    ).can_build
                )
                r_res_r2 = bridge_t.handle_command(
                    {"command": "research", "research_id": 2, "planet_id": 12476, "dry_run": False}
                )
                self.assertTrue(r_res_r2["executed"], r_res_r2)
                self.assertEqual(bridge_t.ctx.last_state.active_planet().building_offers, ())
                self.assertFalse(bridge_t.ctx.last_buildings_fetched)
                r_b9 = bridge_t.handle_command(
                    {"command": "build", "building_id": 9, "planet_id": 12476, "dry_run": False}
                )
                self.assertTrue(r_b9["executed"], r_b9)

                # _find_ship_action qty > 1 affordability checks total_offer_cost & missing
                one_ship_res = Resources(0, 3000, 3000, 0)
                ship_offer = UnitOffer("ship", 1, "Small transport", 0, one_ship_res, True, "buildShip=1", "Build")
                p_low = replace(
                    bridge_t.ctx.last_state.active_planet(),
                    resources=one_ship_res,
                    buildings={**bridge_t.ctx.last_state.active_planet().buildings, "b9": 1},
                    ship_offers=(ship_offer,),
                )
                st_ship = replace(
                    bridge_t.ctx.last_state,
                    planets=(p_low, bridge_t.ctx.last_state.planets[1]),
                )
                ship_act = bridge_t.plan_assembly.find_ship_action(st_ship, 1, 10, 12476, allow_live_verify=True)
                self.assertFalse(ship_act.executable_now)
                self.assertEqual(ship_act.missing, Resources(0, 27000, 27000, 0))
            finally:
                bridge_t._db.close()

    def test_non_fleet_confirmed_planet_mismatch_aborts_and_syncs_bridge_state(self) -> None:
        from tests.e2e.harness import FakeClock, FakeDriver, FakeSpaceInvasionServer

        clock = FakeClock(initial=1000.0)
        srv = FakeSpaceInvasionServer(
            planets={
                12476: {
                    "planet_id": 12476,
                    "name": "Home",
                    "coords": "02:380:12",
                    "planet_type": "normal",
                    "temperature": 31,
                    "fields_max": 230,
                    "resources": [50000, 50000, 50000, 20000],
                    "storage": [500000, 500000, 500000, 500000],
                    "energy": {"produced": 600, "used": 100},
                    "buildings": {"b1": 5, "b2": 3, "b5": 9, "b8": 5, "b9": 2},
                    "ships": {"s1": 2},
                    "defense": {},
                    "economy_rates": {
                        "resource1": 100,
                        "resource2": 100,
                        "resource3": 100,
                        "resource4": 100,
                        "resource5": 100,
                    },
                    "building_queue": [],
                },
                12477: {
                    "planet_id": 12477,
                    "name": "Colony",
                    "coords": "02:380:14",
                    "planet_type": "normal",
                    "temperature": 24,
                    "fields_max": 190,
                    "resources": [50000, 50000, 50000, 20000],
                    "storage": [500000, 500000, 500000, 500000],
                    "energy": {"produced": 400, "used": 80},
                    "buildings": {"b1": 3, "b2": 2, "b5": 7, "b8": 5, "b9": 2},
                    "ships": {"s1": 1},
                    "defense": {},
                    "economy_rates": {
                        "resource1": 100,
                        "resource2": 100,
                        "resource3": 100,
                        "resource4": 100,
                        "resource5": 100,
                    },
                    "building_queue": [],
                },
            },
            active_planet_id=12476,
            researches={"r1": 0, "r2": 4, "r4": 4},
            clock=clock,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg = tmp_path / "config.json"
            cfg.write_text('{"allow_raids": true}', encoding="utf-8")
            drv = FakeDriver(srv)
            lim = PageViewLimiter(
                min_delay_range=(0.0, 0.0),
                max_views_per_minute=24,
                reserved_interactive_views=6,
                clock=clock.monotonic,
                sleeper=clock.sleep,
            )
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg,
                log_dir=None,
                db_path=tmp_path / "state" / "backend.db",
                page_limiter=lim,
                max_limiter_wait=0.5,
                client_factory=lambda _p: GameBrowserClient(
                    drv,
                    page_limiter=lim,
                    min_nav_delay=(0.0, 0.0),
                    timeout=2.0,
                    max_limiter_wait=0.5,
                ),
                ensure_session_fn=lambda: None,
            )
            bridge._live_executor.action_delay_range = (0.0, 0.0)
            assert bridge._db is not None
            try:
                bridge.get_state(include_buildings=True)
                srv._planet_switcher_html = lambda action: (
                    f'<select name="planet">'
                    f'<option selected="selected" value="indexInternal.es?action={action}&newPlanet=12476">Home</option>'
                    f"</select>"
                )
                cmds = [
                    {"command": "build", "building_id": 1, "planet_id": 12477, "dry_run": False},
                    {"command": "research", "research_id": 1, "planet_id": 12477, "dry_run": False},
                    {"command": "build_ship", "ship_id": 1, "quantity": 2, "planet_id": 12477, "dry_run": False},
                    {
                        "command": "throttle_building",
                        "building_id": 2,
                        "percentage": 0,
                        "planet_id": 12477,
                        "dry_run": False,
                    },
                ]
                for payload in cmds:
                    clock.advance(100.0)
                    lim.reset()
                    lim.record_view(18)
                    bridge.ctx.last_state = replace(bridge.ctx.last_state, active_planet_id=12477)
                    res = bridge.handle_command(payload)
                    self.assertTrue(res["ok"], payload)
                    self.assertFalse(res["executed"], f"{payload} unexpectedly executed: {res}")
                    self.assertIn("active planet is 12476", res["message"])
                    self.assertEqual(bridge.ctx.last_state.active_planet_id, 12476)
                    self.assertTrue(bridge.ctx.active_planet_confirmed)
                    self.assertLessEqual(lim.views_last_60s() - 18, 6)

                self.assertEqual(srv.planets[12476]["buildings"]["b1"], 5)
                self.assertEqual(srv.planets[12477]["buildings"]["b1"], 3)
                self.assertEqual(srv.researches["r1"], 0)
                self.assertEqual(srv.planets[12476]["ships"]["s1"], 2)
                self.assertEqual(srv.planets[12477]["ships"]["s1"], 1)
                self.assertEqual(srv.planets[12476]["economy_rates"]["resource2"], 100)
                self.assertNotIn(12477, bridge._live_executor.last_economy_rates)
                self.assertNotIn(12477, bridge.ctx.economy_rates)
                self.assertNotIn(12477, bridge._db.get_all_economy_rates())
            finally:
                bridge._db.close()

    def test_research_dry_run_active_queue_preservation_dedup_finish_clear_and_b8_zero_cross_planet(self) -> None:
        from tests.e2e.harness import FakeClock, FakeDriver, FakeSpaceInvasionServer
        from backend.core.model import ResearchOffer

        def _make_research_env(
            tmp_dir: Path, *, planets, researches=None, tutorial=None, research_offers=None, active_research_banner=None
        ):
            clock = FakeClock(initial=2000.0)
            srv = FakeSpaceInvasionServer(
                planets=planets,
                active_planet_id=12476,
                researches=researches or {"r1": 1, "r2": 2},
                research_offers=research_offers,
                tutorial=tutorial,
                clock=clock,
            )
            if active_research_banner is not None:
                orig_render = srv._render_frame_inner

                def _render_with_active_queues(fid: str, url: str, form_data=None):
                    out_url, html = orig_render(fid, url, form_data=form_data)
                    if fid == "F_Hauptframe" and active_research_banner["on"] and "action=internalHome" in out_url:
                        extra = (
                            '<div id="sp-own-research">Energy technology (Level 3)</div>'
                            '<div id="sp-own-fleet">Outgoing raid</div>'
                        )
                        html = html.replace("</body>", f"{extra}</body>")
                    elif fid == "F_Hauptframe" and active_research_banner["fleet"] and "action=internalHome" in out_url:
                        html = html.replace("</body>", '<div id="sp-own-fleet">Outgoing raid</div></body>')
                    return out_url, html

                srv._render_frame_inner = _render_with_active_queues
            tmp_dir.mkdir(parents=True, exist_ok=True)
            cfg = tmp_dir / "config.json"
            cfg.write_text('{"allow_research": true}', encoding="utf-8")
            drv = FakeDriver(srv)
            lim = PageViewLimiter(
                min_delay_range=(0.0, 0.0),
                max_views_per_minute=24,
                reserved_interactive_views=6,
                clock=clock.monotonic,
                sleeper=clock.sleep,
            )
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg,
                log_dir=None,
                db_path=tmp_dir / "state" / "backend.db",
                page_limiter=lim,
                max_limiter_wait=0.5,
                client_factory=lambda _p: GameBrowserClient(
                    drv,
                    page_limiter=lim,
                    min_nav_delay=(0.0, 0.0),
                    timeout=2.0,
                    max_limiter_wait=0.5,
                ),
                ensure_session_fn=lambda: None,
            )
            bridge._live_executor.action_delay_range = (0.0, 0.0)
            return clock, srv, drv, lim, bridge

        def _make_planets(colony_b8: int) -> dict[int, dict]:
            return {
                12476: {
                    "planet_id": 12476,
                    "name": "Home",
                    "coords": "02:380:12",
                    "planet_type": "normal",
                    "temperature": 31,
                    "fields_max": 230,
                    "resources": [100000, 100000, 100000, 50000],
                    "storage": [500000, 500000, 500000, 500000],
                    "energy": {"produced": 600, "used": 100},
                    "buildings": {"b1": 5, "b5": 8, "b7": 4, "b8": 5, "b9": 1},
                    "ships": {},
                    "defense": {},
                    "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                    "building_queue": [],
                },
                12477: {
                    "planet_id": 12477,
                    "name": "Colony",
                    "coords": "02:380:14",
                    "planet_type": "normal",
                    "temperature": 24,
                    "fields_max": 190,
                    "resources": [100000, 100000, 100000, 50000],
                    "storage": [500000, 500000, 500000, 500000],
                    "energy": {"produced": 400, "used": 80},
                    "buildings": {"b1": 3, "b5": 6, "b7": 2, "b8": colony_b8, "b9": 0},
                    "ships": {},
                    "defense": {},
                    "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                    "building_queue": [],
                },
            }

        active_offers_payload = [
            {
                "research_id": 1,
                "name": "Mining technology",
                "target_level": 2,
                "cost": [1000, 0, 500, 0],
                "can_research": False,
                "status_text": "Unavailable",
            },
            {
                "research_id": 2,
                "name": "Energy technology",
                "target_level": 3,
                "cost": [0, 800, 400, 0],
                "can_research": False,
                "status_text": "In research (Cancel)",
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            # 1. read_full_snapshot(include_home=True, include_research=True) with tut_needs_research=True
            #    must NOT duplicate "research" QueueJob when home_html already had #sp-own-research.
            banner_flag = {"on": True, "fleet": True}
            clock_d, srv_d, drv_d, lim_d, bridge_d = _make_research_env(
                Path(tmp) / "dedup",
                planets=_make_planets(0),
                researches={"r1": 1, "r2": 2},
                tutorial={
                    "step": 4,
                    "total_steps": 11,
                    "can_complete": False,
                    "tasks": [{"description": "Upgrade your Energy technology 0 / 3"}],
                },
                research_offers=active_offers_payload,
                active_research_banner=banner_flag,
            )
            try:
                with GameBrowserClient(drv_d, page_limiter=lim_d, min_nav_delay=(0.0, 0.0), timeout=2.0) as c_d:
                    snap_dedup = c_d.read_full_snapshot(
                        include_buildings=False,
                        include_research=True,
                        include_shipyard=False,
                        include_home=True,
                        return_home=False,
                    )
                res_jobs = [q for q in snap_dedup.active_queues if q.category == "research"]
                self.assertEqual(len(res_jobs), 1, f"Duplicate research QueueJobs: {snap_dedup.active_queues}")
                self.assertEqual(res_jobs[0].name, "Energy technology")
                self.assertEqual(res_jobs[0].target_level, 3)
                self.assertTrue(any(q.category == "fleet" for q in snap_dedup.active_queues))
            finally:
                bridge_d._db.close()

            # 2. b8 == 0 colony: cross-planet build switch clears research_offers=(), then
            #    research(dry_run=True) at 18/18 normal views MUST retain active "research" QueueJob!
            banner_b0 = {"on": True, "fleet": True}
            clock_0, srv_0, drv_0, lim_0, bridge_0 = _make_research_env(
                Path(tmp) / "b8_zero",
                planets=_make_planets(0),
                researches={"r1": 1, "r2": 2},
                research_offers=active_offers_payload,
                active_research_banner=banner_b0,
            )
            try:
                bridge_0.get_state(include_buildings=True)
                self.assertTrue(any(q.category == "research" for q in bridge_0.ctx.last_state.active_queues))
                r_bld0 = bridge_0.handle_command(
                    {"command": "build", "building_id": 1, "planet_id": 12477, "dry_run": False}
                )
                self.assertTrue(r_bld0["executed"])
                self.assertEqual(bridge_0.ctx.last_state.active_planet_id, 12477)
                self.assertEqual(bridge_0.ctx.last_state.research_offers, ())

                clock_0.advance(100.0)
                lim_0.reset()
                lim_0.record_view(18)
                r_dry0 = bridge_0.handle_command({"command": "research", "research_id": 1, "dry_run": True})
                self.assertTrue(r_dry0["ok"])
                self.assertLessEqual(lim_0.views_last_60s(), 24)
                self.assertFalse(r_dry0["action"]["executable_now"])
                cats0 = [q.category for q in bridge_0.ctx.last_state.active_queues]
                self.assertIn("research", cats0)
                self.assertIn("fleet", cats0)
                self.assertFalse(
                    bridge_0.plan_assembly.find_research_action(
                        bridge_0.ctx.last_state, 1, 12476, allow_live_verify=True
                    ).executable_now
                )
                dash0 = bridge_0.dashboard(refresh=False)
                self.assertIn("research", [q["category"] for q in dash0["state"]["active_queues"]])

                # Also verify off-home on internalGalaxy (observed_active_planet=False) preserves active_planet_id=12477 & "research"
                drv_0._navigate_frame("F_Hauptframe", "indexInternal.es?action=internalGalaxy")
                r_dry0_gal = bridge_0.handle_command(
                    {"command": "research", "research_id": 1, "dry_run": True, "refresh": True}
                )
                self.assertTrue(r_dry0_gal["ok"])
                self.assertEqual(bridge_0.ctx.last_state.active_planet_id, 12477)
                self.assertIn("research", [q.category for q in bridge_0.ctx.last_state.active_queues])
                self.assertIn("fleet", [q.category for q in bridge_0.ctx.last_state.active_queues])
            finally:
                bridge_0._db.close()

            # 3. b8 >= 1 planet (b8=2): cross-planet build switch clears research_offers=(), then
            #    research(dry_run=True) off-home MUST extract active "research" QueueJob and keep executable_now=False;
            #    then finishing research on server properly clears "research" while retaining "fleet".
            banner_b2 = {"on": True, "fleet": True}
            active_offers_r2_l3 = [
                active_offers_payload[0],
                {**active_offers_payload[1], "target_level": 4},
            ]
            clock_2, srv_2, drv_2, lim_2, bridge_2 = _make_research_env(
                Path(tmp) / "b8_two",
                planets=_make_planets(2),
                researches={"r1": 1, "r2": 3, "r4": 4},
                research_offers=active_offers_r2_l3,
                active_research_banner=banner_b2,
            )
            try:
                bridge_2.get_state(include_buildings=True)
                r_bld2 = bridge_2.handle_command(
                    {"command": "build", "building_id": 1, "planet_id": 12477, "dry_run": False}
                )
                self.assertTrue(r_bld2["executed"])
                self.assertEqual(bridge_2.ctx.last_state.active_planet_id, 12477)
                self.assertEqual(bridge_2.ctx.last_state.research_offers, ())

                clock_2.advance(100.0)
                lim_2.reset()
                lim_2.record_view(18)
                r_dry2 = bridge_2.handle_command({"command": "research", "research_id": 1, "dry_run": True})
                self.assertTrue(r_dry2["ok"])
                self.assertLessEqual(lim_2.views_last_60s(), 24)
                self.assertFalse(r_dry2["action"]["executable_now"])
                res_q2 = [q for q in bridge_2.ctx.last_state.active_queues if q.category == "research"]
                self.assertEqual(len(res_q2), 1)
                self.assertEqual(res_q2[0].name, "Energy technology")
                self.assertEqual(res_q2[0].target_level, 4)
                self.assertIn("fleet", [q.category for q in bridge_2.ctx.last_state.active_queues])
                self.assertFalse(
                    bridge_2.plan_assembly.find_research_action(
                        bridge_2.ctx.last_state, 1, 12477, allow_live_verify=True
                    ).executable_now
                )

                # Even if a stale ResearchOffer has can_research=True, res_busy (via active_queues or o.active) keeps executable_now=False
                stale_can = (
                    ResearchOffer(
                        1,
                        "r1",
                        "Mining technology",
                        1,
                        2,
                        Resources(1000, 0, 500, 0),
                        True,
                        "Research level 2",
                        "indexInternal.es?action=internalBuildItem&researchID=1",
                        active=False,
                    ),
                    ResearchOffer(
                        2,
                        "r2",
                        "Energy technology",
                        3,
                        4,
                        Resources(0, 800, 400, 0),
                        False,
                        "In research (Cancel)",
                        None,
                        active=True,
                    ),
                )
                self.assertFalse(
                    bridge_2.plan_assembly.find_research_action(
                        replace(bridge_2.ctx.last_state, active_queues=(), research_offers=stale_can),
                        1,
                        12477,
                        allow_live_verify=True,
                    ).executable_now
                )

                # Research finishes on server -> off-home research(dry_run=True, refresh=True) clears "research", keeps "fleet"
                banner_b2["on"] = False
                srv_2.research_offers = None
                srv_2.researches["r2"] = 4
                r_fin = bridge_2.handle_command(
                    {"command": "research", "research_id": 1, "dry_run": True, "refresh": True}
                )
                self.assertTrue(r_fin["ok"])
                self.assertTrue(r_fin["action"]["executable_now"])
                cats_fin = [q.category for q in bridge_2.ctx.last_state.active_queues]
                self.assertEqual(cats_fin, ["fleet"])
            finally:
                bridge_2._db.close()

            # 4. challenger_m2_r5_1 Low Observation 3: 12476 (b8=5) caches offer.can_research=True,
            #    target 12477 has b8=0 -> dry_run=True reports executable_now=False and dry_run=False aborts in 0 views.
            clock_c, srv_c, drv_c, lim_c, bridge_c = _make_research_env(
                Path(tmp) / "b8_zero_cross",
                planets=_make_planets(0),
                researches={"r1": 1, "r2": 4},
            )
            try:
                bridge_c.get_state(include_buildings=True)
                self.assertTrue(
                    next(o for o in bridge_c.ctx.last_state.research_offers if o.research_id == 1).can_research
                )
                r_cross_dry = bridge_c.handle_command(
                    {"command": "research", "research_id": 1, "planet_id": 12477, "dry_run": True}
                )
                self.assertTrue(r_cross_dry["ok"])
                self.assertFalse(r_cross_dry["action"]["executable_now"])

                views_before = lim_c.views_last_60s()
                r_cross_live = bridge_c.handle_command(
                    {"command": "research", "research_id": 1, "planet_id": 12477, "dry_run": False}
                )
                self.assertTrue(r_cross_live["ok"])
                self.assertFalse(r_cross_live["executed"])
                self.assertEqual(lim_c.views_last_60s(), views_before)
            finally:
                bridge_c._db.close()

    def test_adv_bridge_internalbuildings_banner_external_cancel_ship_prereq_and_cross_planet_b8(self) -> None:
        from tests.e2e.harness import FakeClock, FakeDriver, FakeSpaceInvasionServer
        from backend.core.model import (
            ResearchOffer,
            UnitOffer,
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # 1) internalBuildings embedding #tutorial.text with _last_home_fetched=False still refreshes internalHome on plan -> detects ALERT_HOSTILE
            clock_h = FakeClock(initial=3000.0)
            srv_h = FakeSpaceInvasionServer(
                active_planet_id=12476,
                tutorial={"step": 1, "total_steps": 11, "can_complete": False, "tasks": []},
                clock=clock_h,
            )
            orig_bld_fn = srv_h._render_buildings_html

            def _bld_with_tutorial(ap, sq_amp, built_msg=""):
                url, html = orig_bld_fn(ap, sq_amp, built_msg=built_msg)
                injected = html.replace(
                    "</body>",
                    '<div id="tutorial.text">Tutorial 1 / 11</div></body>',
                )
                return url, injected

            srv_h._render_buildings_html = _bld_with_tutorial
            drv_h = FakeDriver(srv_h)
            lim_h = PageViewLimiter(min_delay_range=(0.0, 0.0), clock=clock_h.monotonic, sleeper=clock_h.sleep)
            cfg_h = tmp_path / "cfg_h.json"
            cfg_h.write_text('{"allow_raids": false}', encoding="utf-8")
            bridge_h = GameApiBridge(
                debug_port=9222,
                config_path=cfg_h,
                log_dir=None,
                db_path=tmp_path / "h.db",
                page_limiter=lim_h,
                client_factory=lambda _p: GameBrowserClient(
                    drv_h, page_limiter=lim_h, min_nav_delay=(0.0, 0.0), timeout=2.0
                ),
                ensure_session_fn=lambda: None,
            )
            try:
                bridge_h.get_state(include_buildings=True)
                self.assertIsNotNone(bridge_h.ctx.last_state)
                assert bridge_h.ctx.last_state is not None
                self.assertIsNotNone(bridge_h.ctx.last_state.tutorial)
                bridge_h.ctx.last_home_fetched = False
                bridge_h.ctx.last_home_at = None
                self.assertFalse(bridge_h.state_cache.is_home_fresh(time.monotonic()))
                srv_h.hostile_alerts.append("Hostile raid incoming at 02:380:12")
                plan_res = bridge_h.handle_command({"command": "plan"})
                self.assertTrue(any(a["kind"] == "ALERT_HOSTILE" for a in plan_res["actions"]))
            finally:
                bridge_h._db.close()

            # 2) External research cancel (active_queues=()) clears carried o.active=True so next research is executable_now=True
            # 3) _find_ship_action s10 Locked/insufficient + fallback when r5=0 -> executable_now=False
            # 4) Home (b8=5) caches r8.can_research=True, Colony (b8=1) returns executable_now=False & 0 views on dry_run=False
            clock_x = FakeClock(initial=4000.0)
            planets_x = {
                12476: {
                    "planet_id": 12476,
                    "name": "Home",
                    "coords": "02:380:12",
                    "planet_type": "normal",
                    "temperature": 31,
                    "fields_max": 230,
                    "resources": [100_000, 100_000, 100_000, 50_000],
                    "storage": [500_000, 500_000, 500_000, 500_000],
                    "energy": {"produced": 600, "used": 100},
                    "buildings": {"b1": 5, "b5": 8, "b8": 5, "b9": 1},
                    "ships": {},
                    "defense": {},
                    "economy_rates": {
                        "resource1": 100,
                        "resource2": 100,
                        "resource3": 100,
                        "resource4": 100,
                        "resource5": 100,
                    },
                    "building_queue": [],
                },
                12477: {
                    "planet_id": 12477,
                    "name": "Colony",
                    "coords": "02:380:14",
                    "planet_type": "normal",
                    "temperature": 24,
                    "fields_max": 190,
                    "resources": [100_000, 100_000, 100_000, 50_000],
                    "storage": [500_000, 500_000, 500_000, 500_000],
                    "energy": {"produced": 600, "used": 100},
                    "buildings": {"b1": 4, "b5": 8, "b8": 1, "b9": 1},
                    "ships": {},
                    "defense": {},
                    "economy_rates": {
                        "resource1": 100,
                        "resource2": 100,
                        "resource3": 100,
                        "resource4": 100,
                        "resource5": 100,
                    },
                    "building_queue": [],
                },
            }
            srv_x = FakeSpaceInvasionServer(
                planets=planets_x,
                active_planet_id=12476,
                researches={"r1": 1, "r2": 4, "r4": 2, "r5": 0, "r8": 0},
                clock=clock_x,
            )
            drv_x = FakeDriver(srv_x)
            lim_x = PageViewLimiter(min_delay_range=(0.0, 0.0), clock=clock_x.monotonic, sleeper=clock_x.sleep)
            cfg_x = tmp_path / "cfg_x.json"
            bridge_x = GameApiBridge(
                debug_port=9222,
                config_path=cfg_x,
                log_dir=None,
                db_path=tmp_path / "x.db",
                page_limiter=lim_x,
                client_factory=lambda _p: GameBrowserClient(
                    drv_x, page_limiter=lim_x, min_nav_delay=(0.0, 0.0), timeout=2.0
                ),
                ensure_session_fn=lambda: None,
            )
            try:
                bridge_x.get_state(include_buildings=True)
                st_x = bridge_x.ctx.last_state
                assert st_x is not None
                self.assertTrue(next(o for o in st_x.research_offers if o.research_id == 8).can_research)

                # Colony b8=1 < 3: r8 must be blocked in 0 views even though Home cached can_research=True
                r8_dry = bridge_x.handle_command(
                    {"command": "research", "research_id": 8, "planet_id": 12477, "dry_run": True}
                )
                self.assertFalse(r8_dry["action"]["executable_now"])
                v_before = lim_x.views_last_60s()
                r8_live = bridge_x.handle_command(
                    {"command": "research", "research_id": 8, "planet_id": 12477, "dry_run": False}
                )
                self.assertFalse(r8_live["executed"])
                self.assertEqual(lim_x.views_last_60s(), v_before)

                # s10 Locked/insufficient & fallback when r5=0 -> executable_now=False
                s10_locked_offer = UnitOffer(
                    "ship", 10, "Spy probe", 0, Resources(0, 1000, 500, 0), False, None, "Locked / insufficient"
                )
                p_home = st_x.planets[0]
                st_s10_offer = replace(
                    st_x, planets=(replace(p_home, ship_offers=(s10_locked_offer,)), st_x.planets[1])
                )
                self.assertFalse(
                    bridge_x.plan_assembly.find_ship_action(
                        st_s10_offer, 10, 1, 12476, allow_live_verify=True
                    ).executable_now
                )
                st_s10_empty = replace(st_x, planets=(replace(p_home, ship_offers=()), st_x.planets[1]))
                self.assertFalse(
                    bridge_x.plan_assembly.find_ship_action(
                        st_s10_empty, 10, 1, 12476, allow_live_verify=True
                    ).executable_now
                )

                # External cancel clears carried o.active=True
                bridge_x.ctx.last_state = replace(
                    st_x,
                    research_offers=(
                        ResearchOffer(
                            2,
                            "r2",
                            "Energy technology",
                            4,
                            5,
                            Resources(0, 1600, 800, 0),
                            False,
                            "In research (Cancel)",
                            None,
                            active=True,
                        ),
                        next(o for o in st_x.research_offers if o.research_id == 8),
                    ),
                )
                merged_cancel = bridge_x.state_cache.merge_cached_offers(
                    replace(st_x, research_offers=(), active_queues=())
                )
                self.assertFalse(any(o.active for o in merged_cancel.research_offers))
                self.assertTrue(bridge_x.plan_assembly.find_research_action(merged_cancel, 8, 12476).executable_now)
            finally:
                bridge_x._db.close()

    def test_commander_fleet_slot_busy_wakes_promptly_and_continues_non_fleet_batch(self) -> None:
        from backend.core.model import UnitOffer

        p = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=95,
            fields_max=230,
            resources=Resources(270_000, 15_000, 570_000, 280_000),
            storage=Resources(1_500_000, 1_500_000, 1_500_000, 1_500_000),
            energy=EnergyBalance(5200, 4600),
            buildings={"b1": 21, "b2": 20, "b5": 22, "b8": 4, "b9": 4, "b10": 5},
            ships={"s1": 6, "s4": 12, "s10": 11, "s11": 3},
            ship_offers=(
                UnitOffer("ship", 11, "Recycler", 3, Resources(10_000, 6_000, 2_000, 0), True, "ship_11", "Build"),
            ),
        )
        st = GameState(
            universe_id=31,
            user_id=103603,
            nickname="ventkx86",
            timestamp=1700000500.0,
            planets=(p,),
            active_planet_id=12476,
            researches={"r1": 4, "r2": 6, "r5": 4, "r6": 3},
            speedup_factor=10.0,
            raid_targets=(
                {
                    "coords": "02:380:16",
                    "owner": "Spacia",
                    "is_spacia": True,
                    "is_inactive": True,
                    "status_color": "purple",
                    "has_spy_intel": True,
                    "last_spied_ts": 1700000100.0,
                    "resources": {"pig_iron": 90_000, "metal": 40_000, "kryptonite": 30_000, "spice": 10_000},
                    "fleet": {},
                    "defense": {},
                },
            ),
        )
        fake_api = MagicMock()
        fake_api.get_state.return_value = st

        def _fake_send(payload: dict) -> dict:
            if payload.get("command") in ("attack", "spy", "recycle"):
                return {
                    "ok": True,
                    "command": payload["command"],
                    "executed": False,
                    "dry_run": False,
                    "message": "Aborted: no ship input fields found on internalFleet.",
                }
            if payload.get("command") == "build_ship":
                return {
                    "ok": True,
                    "command": "build_ship",
                    "executed": True,
                    "dry_run": False,
                    "message": "Ordered 1x Recycler on [02:380:12].",
                }
            return {"ok": True, "executed": False, "dry_run": False, "message": "noop"}

        fake_api.send_command.side_effect = _fake_send
        cfg = StrategyConfig(allow_raids=True, allow_shipyard=True, shipyard_targets={"recycler": 4})
        out_st, actual, res, nxt, outcomes = commander.run_commander_tick(
            fake_api,
            live=True,
            max_actions=5,
            config=cfg,
        )
        # Even after ATTACK hit busy fleet slots, BUILD_SHIP Recycler still ran in the same batch!
        self.assertTrue(any(o.get("command") == "build_ship" and o.get("executed") for o in outcomes))
        # Even if next_action is a 900s WAIT_RESOURCES building, wakeup stays <= 120s due to fleet slot cooldown!
        wait_fusion = commander.PlannedAction(
            priority="P1",
            kind="WAIT_RESOURCES",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=5,
            target_name="Fusion plant",
            target_level=23,
            reason="Proactive power",
            executable_now=False,
            wait_seconds=902.0,
        )
        wake_s, wake_reason = commander.compute_next_wakeup_seconds(
            out_st,
            wait_fusion,
            outcomes,
            min_interval_s=120.0,
            max_idle_s=900.0,
        )
        self.assertLessEqual(wake_s, 120.0)
        self.assertIn("fleet slot", wake_reason)


if __name__ == "__main__":
    unittest.main()
