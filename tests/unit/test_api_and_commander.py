"""Tests for the local Game HTTP API, Commander client, and hot-reloader."""

from __future__ import annotations

from dataclasses import asdict
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import commander
from spaceinvasion.api_server import ApiRequestError, GameApiBridge, create_api_server
from spaceinvasion.commander_client import GameApiClient, GameApiError
from spaceinvasion.config import StrategyConfig
from spaceinvasion.logging_utils import append_tick_logs, redact_sensitive
from spaceinvasion.reloader import (
    RELOAD_CHILD_ENV,
    should_spawn_reloader,
    snapshot_mtimes,
    strip_reload_args,
)
from spaceinvasion.state import (
    BuildingOffer,
    EnergyBalance,
    FleetAlert,
    GameState,
    PlanetState,
    QueueJob,
    Resources,
    TutorialStatus,
    TutorialTask,
    game_state_from_dict,
)
from spaceinvasion.strategy import FUSION_PLANT, PIG_IRON_MINE, PlannedAction


def _sample_state() -> GameState:
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
        fields_used=9,
        fields_max=230,
        resources=Resources(5500, 900, 3100, 200),
        storage=Resources(100000, 100000, 100000, 100000),
        energy=EnergyBalance(45, 38),
        buildings={"b1": 3, "b3": 3, "b5": 3},
        building_names={"b1": "Pig iron mine", "b3": "Kryptonite mine", "b5": "Fusion plant"},
        building_offers=offers,
    )
    return GameState(
        universe_id=31,
        user_id=103603,
        nickname="ventkx86",
        timestamp=1234.5,
        planets=(planet,),
        active_planet_id=12476,
        researches={"r1": 0},
        research_names={"r1": "Mining technology"},
        specs={"miner": 0},
        unread_messages={"total": 0},
        tutorial=TutorialStatus(
            step=2,
            total_steps=11,
            tasks=(
                TutorialTask("Increase your Fusion plant level 0 / 4", "Fusion plant", 5, 0, 4),
                TutorialTask("Increase your Pig iron mine level 0 / 3", "Pig iron mine", 1, 0, 3),
            ),
            reward_text="16643 Pig iron",
            can_complete=False,
        ),
        active_queues=(QueueJob("building", "Development center", building_id=7, target_level=1),),
        fleet_alerts=(FleetAlert("ATTACK", False, "Friendly"),),
        speedup_factor=20.0,
        newbie_protection=True,
    )


class TestApiAndCommander(unittest.TestCase):
    def test_game_state_roundtrip_via_dict(self) -> None:
        original = _sample_state()
        rebuilt = game_state_from_dict(asdict(original))
        self.assertEqual(rebuilt, original)

    def test_http_api_and_commander_tick_end_to_end(self) -> None:
        state = _sample_state()
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=abc")
        fake_client.read_full_snapshot.return_value = state
        fake_client.navigate_menu.return_value = "<html>ok</html>"

        bridge = GameApiBridge(
            debug_port=9222,
            log_dir=None,
            client_factory=lambda _port: fake_client,
            ensure_session_fn=lambda: None,
        )
        server = create_api_server(bridge, host="127.0.0.1", port=0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            api = GameApiClient(base_url=f"http://127.0.0.1:{port}")
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                h = api.health()
            self.assertTrue(h["ok"])
            self.assertTrue(h["in_game"])

            got_state = api.get_state()
            self.assertEqual(got_state.planets[0].coords, "02:380:12")

            plan_res = api.send_command({"command": "plan"})
            self.assertTrue(plan_res["ok"])
            self.assertEqual(plan_res["actions"][0]["target_id"], FUSION_PLANT)

            nav_res = api.send_command({"command": "navigate", "action": "internalResearch"})
            self.assertEqual(nav_res["action"], "internalResearch")

            cfg_res = api.send_command({"command": "config"})
            self.assertTrue(cfg_res["ok"])
            self.assertIn("allow_research", cfg_res["config"])

            fs_res = api.send_command({"command": "fleetsave_plan"})
            self.assertTrue(fs_res["ok"])
            self.assertIn("escape_plan", fs_res)

            res_cmd = api.send_command({"command": "research", "research_id": 2, "dry_run": True})
            self.assertTrue(res_cmd["ok"])
            self.assertTrue(res_cmd["dry_run"])

            # Control UI static assets & /dashboard + bot_control + update_config
            import requests

            ui_html = requests.get(f"http://127.0.0.1:{port}/", timeout=5)
            self.assertEqual(ui_html.status_code, 200)
            self.assertIn("SpaceInvasion Control", ui_html.text)
            ui_css = requests.get(f"http://127.0.0.1:{port}/styles.css", timeout=5)
            self.assertEqual(ui_css.status_code, 200)
            ui_js = requests.get(f"http://127.0.0.1:{port}/app.js", timeout=5)
            self.assertEqual(ui_js.status_code, 200)
            self.assertIn("no-store", ui_html.headers.get("Cache-Control", ""))

            ver = requests.get(f"http://127.0.0.1:{port}/ui-version", timeout=5).json()
            self.assertTrue(ver["ok"])
            self.assertTrue(ver["ui_version"])
            self.assertTrue(ver["code_version"])
            self.assertTrue(ver["css_version"])
            self.assertTrue(ver["server_boot_id"])

            ctrl = api.send_command({"command": "bot_control", "paused": True, "mode_override": "dry-run"})
            self.assertTrue(ctrl["bot_control"]["paused"])
            self.assertEqual(ctrl["bot_control"]["mode_override"], "dry-run")
            self.assertTrue(api.get_bot_control()["paused"])

            tick_res = api.send_command({"command": "tick_once", "dry_run": True})
            self.assertTrue(tick_res["ok"])
            self.assertTrue(tick_res["dry_run"])

            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                dash = requests.get(f"http://127.0.0.1:{port}/dashboard?refresh=0", timeout=5).json()
            self.assertTrue(dash["ok"])
            self.assertTrue(dash["bot_control"]["paused"])
            self.assertIsNotNone(dash["state"])
            self.assertGreaterEqual(len(dash["plan"]), 1)
            self.assertIn("ranking", dash)
            self.assertIn("buildings_eta", dash)
            self.assertIn("combat", dash)
            self.assertIn("db_summary", dash)
            self.assertEqual(len(dash["buildings_eta"]["buildings"]), 21)
            self.assertEqual(len(dash["ranking"]["horizons"]), 5)

            eta_cmd = api.send_command({"command": "buildings_eta"})
            self.assertTrue(eta_cmd["ok"])
            self.assertEqual(len(eta_cmd["buildings_eta"]["researches"]), 15)

            sim_cmd = api.send_command({
                "command": "simulate_combat",
                "attacker_ships": {"s4": 20, "s1": 4},
                "defender_defense": {"d1": 2},
                "target_resources": {"pig_iron": 10000, "metal": 10000},
            })
            self.assertTrue(sim_cmd["ok"])
            self.assertTrue(sim_cmd["simulation"]["safe_to_attack"])

            rnk_cmd = api.send_command({"command": "ranking_evolution"})
            self.assertTrue(rnk_cmd["ok"])
            self.assertIn("current", rnk_cmd["ranking"])
            self.assertGreater(rnk_cmd["ranking"]["current"]["estimated_points"], 0)

            with tempfile.TemporaryDirectory() as tmp_cfg:
                bridge.config_path = Path(tmp_cfg) / "config.yaml"
                upd = api.send_command({"command": "update_config", "config": {"merchant_mode": True}})
                self.assertTrue(upd["config"]["merchant_mode"])
                self.assertTrue(bridge.config_path.exists())

            with self.assertRaises(GameApiError):
                api.send_command({"command": "navigate", "action": "invalidAction"})

            with self.assertRaises(GameApiError):
                api.send_command({"command": "unknown_cmd"})

            # Commander tick over HTTP in dry-run mode
            cmd_state, actual, actual_res, next_act, outcomes = commander.run_commander_tick(
                api, live=False, max_actions=1
            )
            self.assertEqual(cmd_state.universe_id, 31)
            self.assertIsNotNone(actual)
            self.assertEqual(actual.target_id, FUSION_PLANT)
            self.assertTrue(outcomes[0]["dry_run"])
            self.assertFalse(outcomes[0]["executed"])
            self.assertIn("[DRY-RUN]", actual_res["message"])
            self.assertIsNotNone(next_act)
        finally:
            server.shutdown()
            server.server_close()

    def test_logs_actual_and_next_action_into_logs_folder(self) -> None:
        state = _sample_state()
        with tempfile.TemporaryDirectory() as tmp:
            logs_dir = Path(tmp) / "logs"
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.read_full_snapshot.return_value = state
            bridge = GameApiBridge(
                debug_port=9222,
                log_dir=logs_dir,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                api = GameApiClient(base_url=f"http://127.0.0.1:{port}")
                st, actual, res, next_act, outcomes = commander.run_commander_tick(
                    api, live=False, max_actions=1
                )
                append_tick_logs(
                    logs_dir,
                    source="commander",
                    tick=1,
                    mode="DRY-RUN",
                    state=st,
                    actual_action=actual,
                    actual_result=res,
                    next_action=next_act,
                    outcomes=outcomes,
                )
            finally:
                server.shutdown()
                server.server_close()

            self.assertTrue((Path(tmp) / "state" / "spaceinvasion.db").exists())
            self.assertIsNotNone(bridge._db)
            summary = bridge._db.summary()
            bridge._db.close()
            self.assertGreaterEqual(summary["counts"]["rank_snapshots"], 1)
            self.assertGreaterEqual(summary["counts"]["action_history"], 1)

            cmd_jsonl = (logs_dir / "commander.jsonl").read_text(encoding="utf-8").strip().splitlines()
            cmd_log = (logs_dir / "commander.log").read_text(encoding="utf-8")
            api_jsonl = (logs_dir / "game_api.jsonl").read_text(encoding="utf-8").strip().splitlines()
            api_log = (logs_dir / "game_api.log").read_text(encoding="utf-8")

            parsed_cmd = json.loads(cmd_jsonl[-1])
            self.assertIsNotNone(parsed_cmd["actual_action"])
            self.assertIsNotNone(parsed_cmd["next_action"])
            self.assertEqual(parsed_cmd["actual_action"]["target_id"], FUSION_PLANT)
            self.assertIn("ACTUAL:", cmd_log)
            self.assertIn("NEXT:", cmd_log)

            parsed_api = json.loads(api_jsonl[-1])
            self.assertEqual(parsed_api["command"], "build")
            self.assertIsNotNone(parsed_api["actual_action"])
            self.assertIsNotNone(parsed_api["next_action"])
            self.assertIn("actual=", api_log)
            self.assertIn("next=", api_log)

    def test_reloader_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "mod.py"
            target.write_text("x = 1\n", encoding="utf-8")
            snap1 = snapshot_mtimes([target])
            target.write_text("x = 2\n", encoding="utf-8")
            snap2 = snapshot_mtimes([target])
            self.assertNotEqual(snap1, snap2)

        self.assertEqual(
            strip_reload_args(["--port", "8787", "--reload", "--live"]),
            ["--port", "8787", "--live"],
        )
        with patch.dict("os.environ", {}, clear=True):
            self.assertTrue(should_spawn_reloader(True))
        with patch.dict("os.environ", {RELOAD_CHILD_ENV: "1"}, clear=True):
            self.assertFalse(should_spawn_reloader(True))

    def test_redact_sensitive_masks_sid_tokens(self) -> None:
        raw = {"action_href": "indexInternal.es?action=internalBuildItem&buildingID=1&sid=3efba2684bd7f4dc"}
        sanitized = redact_sensitive(raw)
        self.assertEqual(
            sanitized["action_href"],
            "indexInternal.es?action=internalBuildItem&buildingID=1&sid=REDACTED",
        )

    def test_commander_maps_throttle_building_command_payload(self) -> None:
        from spaceinvasion.strategy import PlannedAction

        act = PlannedAction(
            priority="P1",
            kind="THROTTLE_BUILDING",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=2,
            target_name="Blast furnace 0%",
            target_level=0,
            reason="Pause Blast furnace to boost Pig iron",
            executable_now=True,
        )
        payload = commander._action_to_command_payload(act, dry_run=False)
        self.assertEqual(payload["command"], "throttle_building")
        self.assertEqual(payload["building_id"], 2)
        self.assertEqual(payload["percentage"], 0)
        self.assertFalse(payload["dry_run"])

        scan_act = PlannedAction(
            priority="P2",
            kind="SCAN_GALAXY",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="Galaxy [02:380]",
            reason="Scan system",
            executable_now=True,
        )
        scan_payload = commander._action_to_command_payload(scan_act, dry_run=False)
        self.assertEqual(scan_payload["command"], "scan_galaxy")
        self.assertEqual(scan_payload["galaxy"], 2)
        self.assertEqual(scan_payload["system"], 380)

        atk_act = PlannedAction(
            priority="P2",
            kind="ATTACK",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="02:380:04 (Sleepy)",
            reason="Raid inactive",
            executable_now=True,
        )
        atk_payload = commander._action_to_command_payload(atk_act, dry_run=True)
        self.assertEqual(atk_payload["command"], "attack")
        self.assertEqual(atk_payload["target_coords"], "02:380:04")
        self.assertTrue(atk_payload["dry_run"])

    def test_scan_galaxy_and_attack_api_commands(self) -> None:
        state = _sample_state()
        gal_html = '<table><tr><td>4</td><td><span class="galaxy_yellow">Idle</span></td><td>Sleeper</td></tr></table>'
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=abc")
        fake_client.read_full_snapshot.return_value = state
        fake_client.open_galaxy.return_value = gal_html
        fake_client.navigate_menu.return_value = gal_html

        bridge = GameApiBridge(
            debug_port=9222,
            log_dir=None,
            client_factory=lambda _port: fake_client,
            ensure_session_fn=lambda: None,
        )
        scan_res = bridge.handle_command({"command": "scan_galaxy", "dry_run": False})
        self.assertTrue(scan_res["executed"])
        self.assertIn("02:380", scan_res["message"])
        self.assertTrue(any(t["coords"] == "02:380:04" for t in bridge._raid_targets))

        atk_res = bridge.handle_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
        self.assertFalse(atk_res["executed"])
        self.assertIn("allow_raids", atk_res["message"])

    def test_ui_version_detects_css_and_code_changes(self) -> None:
        from spaceinvasion.api_server import compute_ui_assets_state

        with tempfile.TemporaryDirectory() as tmp:
            ui_dir = Path(tmp)
            (ui_dir / "index.html").write_text("<html>v1</html>", encoding="utf-8")
            (ui_dir / "app.js").write_text("console.log(1);", encoding="utf-8")
            (ui_dir / "styles.css").write_text("body{color:#fff;}", encoding="utf-8")

            s1 = compute_ui_assets_state(ui_dir)
            (ui_dir / "styles.css").write_text("body{color:#16ec98;background:#000;}", encoding="utf-8")
            s2 = compute_ui_assets_state(ui_dir)
            self.assertEqual(s1["code_version"], s2["code_version"])
            self.assertNotEqual(s1["css_version"], s2["css_version"])
            self.assertNotEqual(s1["ui_version"], s2["ui_version"])

            (ui_dir / "app.js").write_text("console.log('reloaded');", encoding="utf-8")
            s3 = compute_ui_assets_state(ui_dir)
            self.assertNotEqual(s2["code_version"], s3["code_version"])

    def test_bridge_limiter_wait_defaults_and_dynamic_raid_stats_isolated_from_repo_file(self) -> None:
        from dataclasses import replace as dc_replace
        import sqlite3
        from spaceinvasion.storage import GameDatabase

        repo_hist = Path("data") / "attack_history.json"
        self.assertFalse(repo_hist.exists())
        repo_db = Path("state") / "spaceinvasion.db"
        before_raids = 0
        if repo_db.is_file():
            with sqlite3.connect(f"file:{repo_db}?mode=ro", uri=True) as rconn:
                before_raids = int(rconn.execute("SELECT COUNT(*) FROM raid_history").fetchone()[0])

        state = _sample_state()
        p = state.planets[0]
        rich_planet = dc_replace(
            p,
            buildings={"b1": 4, "b2": 3, "b3": 4, "b5": 8, "b9": 2},
            ships={"s4": 25, "s1": 8},
            resources=Resources(8000, 6000, 4000, 2000),
        )
        state = dc_replace(state, planets=(rich_planet,))

        with tempfile.TemporaryDirectory() as tmp:
            logs_dir = Path(tmp) / "logs"
            db_path = Path(tmp) / "state" / "spaceinvasion.db"
            cfg_path = Path(tmp) / "config.yaml"

            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.read_full_snapshot.return_value = state

            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=logs_dir,
                db_path=db_path,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            try:
                self.assertEqual(bridge._max_limiter_wait, 8.0)
                self.assertEqual(bridge._executor_max_limiter_wait, 8.0)

                st0 = bridge.get_state(include_buildings=True)
                self.assertIn("raid_stats", st0)
                self.assertEqual(st0["raid_stats"]["overall"]["attempts"], 0)

                # Simulate out-of-band DB raid inserts (PW-02 contract)
                ext_db = GameDatabase(db_path)
                try:
                    ext_db.record_raid(
                        "02:380:04",
                        attacked_ts=1_700_000_100.0,
                        is_exempt=True,
                        outcome="Victory",
                        loot={"pig_iron": 12000, "metal": 6000},
                    )
                    ext_db.record_raid(
                        "02:380:05",
                        attacked_ts=1_700_000_200.0,
                        is_exempt=True,
                        outcome="Defeat",
                        loot={},
                    )
                finally:
                    ext_db.close()

                st1 = bridge.get_state(include_buildings=True)
                self.assertEqual(st1["raid_stats"]["overall"]["attempts"], 2)
                self.assertIn("02:380:04", st1["raid_stats"]["per_target"])

                with patch("spaceinvasion.api_server.plan_actions", return_value=[]) as mock_plan:
                    bridge._plan_with_context(state)
                    passed_state = mock_plan.call_args.args[0]
                    self.assertEqual(passed_state.raid_stats["overall"]["attempts"], 2)
                    self.assertIn("02:380:05", passed_state.raid_stats["per_target"])
            finally:
                if bridge._db is not None:
                    bridge._db.close()

        self.assertFalse(repo_hist.exists())
        if repo_db.is_file():
            with sqlite3.connect(f"file:{repo_db}?mode=ro", uri=True) as rconn:
                after_raids = int(rconn.execute("SELECT COUNT(*) FROM raid_history").fetchone()[0])
            self.assertEqual(after_raids, before_raids)

    def test_update_config_validation_and_executor_sync(self) -> None:
        import requests

        state = _sample_state()
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.read_full_snapshot.return_value = state

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.yaml"
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=None,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                base = f"http://127.0.0.1:{port}"
                invalid_patches = [
                    {"planner_mode": "bogus"},
                    {"planner_mode": ""},
                    {"planner_mode": "   "},
                    {"planner_mode": None},
                    {"planner_mode": False},
                    {"planner_mode": 123},
                    {"ga_population_size": 3},
                    {"ga_population_size": 201},
                    {"ga_population_size": True},
                    {"ga_population_size": 12.5},
                    {"ga_population_size": "abc"},
                    {"ga_generations": 0},
                    {"ga_generations": 201},
                    {"ga_generations": False},
                    {"ga_generations": 2.5},
                    {"ga_horizon_steps": 0},
                    {"ga_horizon_steps": 25},
                    {"ga_mutation_rate": -0.01},
                    {"ga_mutation_rate": 1.01},
                    {"ga_mutation_rate": True},
                    {"ga_mutation_rate": "bad"},
                    {"ga_nn_weight": -0.1},
                    {"ga_nn_weight": 1.5},
                    {"ga_nn_weight": False},
                    {"ga_seed": True},
                    {"ga_seed": 42.5},
                    {"ga_seed": ""},
                    {"ga_seed": "not_int"},
                    {"max_actions_per_hour": 0},
                    {"max_actions_per_hour": -5},
                    {"max_actions_per_hour": True},
                    {"max_actions_per_hour": 3.5},
                ]
                for bad in invalid_patches:
                    resp = requests.post(
                        f"{base}/command",
                        json={"command": "update_config", "config": bad},
                        timeout=5,
                    )
                    self.assertEqual(resp.status_code, 400, msg=f"Expected 400 for {bad!r}, got {resp.status_code}")
                    self.assertFalse(resp.json()["ok"])
                    self.assertEqual(bridge.strategy_config.planner_mode, "priority")
                    self.assertEqual(bridge._live_executor.max_actions_per_hour, 60)
                    self.assertEqual(bridge._dry_executor.max_actions_per_hour, 60)

                # NaN rejected when sent as string or float
                resp_nan = requests.post(
                    f"{base}/command",
                    json={"command": "update_config", "config": {"ga_mutation_rate": "NaN"}},
                    timeout=5,
                )
                self.assertEqual(resp_nan.status_code, 400)

                # Invalid per-request planner_mode on plan rejected with 400
                for bad_mode in ("bogus", "", "   ", 123, False):
                    r_bad_plan = requests.post(
                        f"{base}/command",
                        json={"command": "plan", "planner_mode": bad_mode},
                        timeout=5,
                    )
                    self.assertEqual(r_bad_plan.status_code, 400, msg=f"bad_mode={bad_mode!r}")

                # Valid update_config persists & syncs both live and dry executors
                good_resp = requests.post(
                    f"{base}/command",
                    json={
                        "command": "update_config",
                        "config": {
                            "planner_mode": "ga_nn",
                            "ga_population_size": 16,
                            "ga_generations": 4,
                            "ga_horizon_steps": 3,
                            "ga_mutation_rate": 0.2,
                            "ga_nn_weight": 0.4,
                            "ga_seed": 77,
                            "max_actions_per_hour": 12,
                            "allow_raids": True,
                            "allow_fleetsave": True,
                        },
                    },
                    timeout=5,
                )
                self.assertEqual(good_resp.status_code, 200, good_resp.text)
                cfg_out = good_resp.json()["config"]
                self.assertEqual(cfg_out["planner_mode"], "ga_nn")
                self.assertEqual(cfg_out["ga_population_size"], 16)
                self.assertEqual(cfg_out["ga_generations"], 4)
                self.assertEqual(cfg_out["ga_horizon_steps"], 3)
                self.assertEqual(cfg_out["ga_seed"], 77)
                self.assertEqual(cfg_out["max_actions_per_hour"], 12)
                self.assertEqual(bridge._live_executor.max_actions_per_hour, 12)
                self.assertEqual(bridge._dry_executor.max_actions_per_hour, 12)
                self.assertTrue(bridge._live_executor.allow_raids)
                self.assertTrue(bridge._dry_executor.allow_raids)
                self.assertTrue(bridge._live_executor.allow_fleetsave)
                self.assertTrue(bridge._dry_executor.allow_fleetsave)

                # ga_seed=None clears seed cleanly
                clr = requests.post(
                    f"{base}/command",
                    json={"command": "update_config", "config": {"ga_seed": None}},
                    timeout=5,
                )
                self.assertEqual(clr.status_code, 200)
                self.assertIsNone(clr.json()["config"]["ga_seed"])
            finally:
                server.shutdown()
                server.server_close()

    def test_plan_override_and_sanitized_optimizer_summary_isolation(self) -> None:
        from spaceinvasion.api_server import _sanitize_optimizer_summary

        dirty = {
            "generations": 4,
            "population_size": 16,
            "nn_evaluations": 64,
            "baseline_fitness": float("nan"),
            "best_fitness": float("inf"),
            "improvement_pct": 1.25e8,
            "note": "https://example.invalid/indexInternal.es?sid=SECRET999TOKEN",
        }
        cleaned = _sanitize_optimizer_summary(dirty)
        assert cleaned is not None
        self.assertEqual(cleaned["baseline_fitness"], 0.0)
        self.assertEqual(cleaned["best_fitness"], 0.0)
        self.assertLessEqual(cleaned["improvement_pct"], 1000.0)
        self.assertGreaterEqual(cleaned["improvement_pct"], -1000.0)
        self.assertNotIn("SECRET999TOKEN", json.dumps(cleaned))
        self.assertIn("sid=REDACTED", cleaned["note"])

        state = _sample_state()
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.read_full_snapshot.return_value = state

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.yaml"
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=None,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            bridge.handle_command(
                {"command": "update_config", "config": {"ga_population_size": 8, "ga_generations": 2, "ga_seed": 42}}
            )

            res_pri = bridge.handle_command({"command": "plan"})
            self.assertEqual(res_pri["planner_mode"], "priority")
            self.assertIsNone(res_pri["optimizer_summary"])

            # Per-request ga_nn override exposes summary without mutating config or leaking into priority dashboard
            res_ga = bridge.handle_command({"command": "plan", "planner_mode": "ga_nn"})
            self.assertEqual(res_ga["planner_mode"], "ga_nn")
            self.assertIsInstance(res_ga["optimizer_summary"], dict)
            self.assertEqual(res_ga["optimizer_summary"]["generations"], 2)
            self.assertEqual(bridge.strategy_config.planner_mode, "priority")

            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                dash_pri = bridge.dashboard(refresh=False)
            self.assertEqual(dash_pri["planner_mode"], "priority")
            self.assertIsNone(dash_pri["optimizer_summary"])

            # Switching config to ga_nn exposes optimizer_summary on dashboard; switching back clears it
            bridge.handle_command({"command": "update_config", "config": {"planner_mode": "ga_nn"}})
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                dash_ga = bridge.dashboard(refresh=False)
            self.assertEqual(dash_ga["planner_mode"], "ga_nn")
            self.assertIsInstance(dash_ga["optimizer_summary"], dict)
            self.assertGreaterEqual(dash_ga["optimizer_summary"]["nn_evaluations"], 1)

            bridge.handle_command({"command": "update_config", "config": {"planner_mode": "priority"}})
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                dash_after = bridge.dashboard(refresh=False)
            self.assertEqual(dash_after["planner_mode"], "priority")
            self.assertIsNone(dash_after["optimizer_summary"])

    def test_all_action_and_early_abort_responses_include_all_nine_eta_fields(self) -> None:
        from dataclasses import replace as dc_replace
        from spaceinvasion.strategy import PlannedAction

        eta_keys = (
            "wait_hours",
            "wait_seconds",
            "wait_eta",
            "duration_seconds",
            "duration_text",
            "total_hours",
            "total_seconds",
            "total_eta",
            "eta_bottleneck",
        )
        state = _sample_state()
        p = state.planets[0]
        offers_with_sid = tuple(
            dc_replace(
                o,
                build_href=f"indexInternal.es?action=internalBuildItem&buildingID={o.building_id}&sid=leaksid999",
            )
            for o in p.building_offers
        )
        state = dc_replace(state, planets=(dc_replace(p, building_offers=offers_with_sid),))

        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.read_full_snapshot.return_value = state

        bridge = GameApiBridge(
            debug_port=9222,
            log_dir=None,
            client_factory=lambda _port: fake_client,
            ensure_session_fn=lambda: None,
        )

        bld_res = bridge.handle_command({"command": "build", "building_id": PIG_IRON_MINE, "dry_run": True})
        for k in eta_keys:
            self.assertIn(k, bld_res["action"])
        self.assertGreater(bld_res["action"]["duration_seconds"], 0.0)
        self.assertNotEqual(bld_res["action"]["duration_text"], "Instant")
        self.assertNotIn("leaksid999", json.dumps(bld_res))

        ship_res = bridge.handle_command({"command": "build_ship", "ship_id": 1, "quantity": 2, "dry_run": True})
        for k in eta_keys:
            self.assertIn(k, ship_res["action"])

        # Own-planet spy & attack early-abort paths include all 9 ETA keys
        spy_abort = bridge.handle_command({"command": "spy", "target_coords": "02:380:12", "dry_run": True})
        self.assertFalse(spy_abort["executed"])
        for k in eta_keys:
            self.assertIn(k, spy_abort["action"])

        atk_abort = bridge.handle_command({"command": "attack", "target_coords": "02:380:12", "dry_run": True})
        self.assertFalse(atk_abort["executed"])
        for k in eta_keys:
            self.assertIn(k, atk_abort["action"])

        # tick_once when no actions are executable_now
        non_exec = PlannedAction(
            priority="P2",
            kind="WAIT_RESOURCES",
            planet_id=p.planet_id,
            planet_coords=p.coords,
            target_id=1,
            target_name="Pig iron mine",
            target_level=4,
            cost=Resources(1000, 0, 0, 0),
            missing=Resources(500, 0, 0, 0),
            reason="Waiting",
            executable_now=False,
        )
        with patch("spaceinvasion.api_server.plan_actions", return_value=[non_exec]):
            tick_wait = bridge.handle_command({"command": "tick_once", "dry_run": True})
            self.assertFalse(tick_wait["executed"])
            for k in eta_keys:
                self.assertIn(k, tick_wait["action"])
            self.assertGreater(tick_wait["action"]["wait_hours"], 0.0)
            self.assertNotEqual(tick_wait["action"]["wait_eta"], "Ready")
            self.assertEqual(tick_wait["action"]["eta_bottleneck"], "pig_iron")

    def test_sid_and_uppercase_sid_redaction_across_api_endpoints(self) -> None:
        from dataclasses import replace as dc_replace
        from spaceinvasion.fleet import GalaxySlot

        secret = "secretToken_XYZ999"
        base = _sample_state()
        p0 = base.planets[0]
        leaky_offer = dc_replace(
            p0.building_offers[1],
            name=f"Pig iron mine sid={secret}",
            can_build=False,
            build_href=f"indexInternal.es?action=internalBuildItem&buildingID=1&SID={secret}",
        )
        leaky_planet = dc_replace(
            p0,
            name=f"Homeworld sid={secret}",
            resources=Resources(0, 0, 0, 0),
            buildings={"b1": 0, "b3": 0, "b5": 3},
            building_names={"b1": f"Pig iron mine sid={secret}", "b3": "Kryptonite mine", "b5": "Fusion plant"},
            building_offers=(leaky_offer,),
        )
        leaky_tut = TutorialStatus(
            step=2,
            total_steps=11,
            tasks=(
                TutorialTask(
                    f"Increase your Pig iron mine level 0 / 5 sid={secret}",
                    f"Pig iron mine sid={secret}",
                    1,
                    0,
                    5,
                ),
            ),
            reward_text=f"1000 Pig iron sid={secret}",
            can_complete=False,
        )
        leaky_state = dc_replace(
            base,
            planets=(leaky_planet,),
            research_names={"r1": f"Mining technology SID={secret}"},
            tutorial=leaky_tut,
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "state" / "spaceinvasion.db"
            cfg_path = tmp_path / "config.yaml"
            nav_html = (
                f'<a href="indexInternal.es?action=internalHome&SID={secret}" '
                f'onclick="go(\'indexInternal.es?sid={secret}\')">Home</a>'
                f'<a href="indexInternal.es?SID={secret}&action=internalBuildings">Buildings</a>'
            )
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.driver = MagicMock(
                current_url=f"https://spaceinvasion.bitmeup.com/indexGame.es?SID={secret}",
                page_source=nav_html,
            )
            fake_client.read_full_snapshot.return_value = leaky_state

            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=None,
                db_path=db_path,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            try:
                assert bridge._db is not None
                bridge._db.record_galaxy_slots(
                    [
                        GalaxySlot(
                            coords="02:380:04",
                            position=4,
                            name=f"BlueP SID={secret}",
                            owner=f"Ally sid={secret}",
                            status_color="blue",
                        )
                    ]
                )

                bridge.get_state(include_buildings=True)
                tick_wait = bridge.handle_command({"command": "tick_once", "dry_run": True})
                self.assertFalse(tick_wait["executed"])
                self.assertNotIn(secret, json.dumps(tick_wait))
                self.assertIn("Waiting:", tick_wait["message"])

                with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                    dash = bridge.dashboard(refresh=False)
                self.assertNotIn(secret, json.dumps(dash))

                eta_res = bridge.handle_command({"command": "buildings_eta"})
                self.assertNotIn(secret, json.dumps(eta_res))

                db_res = bridge.handle_command({"command": "db_summary"})
                self.assertNotIn(secret, json.dumps(db_res))

                menu_res = bridge.handle_command({"command": "menu"})
                self.assertNotIn(secret, json.dumps(menu_res))
                self.assertEqual(len(menu_res["items"]), 2)
                self.assertIn("action=internalBuildings", menu_res["items"][1]["href"])
                self.assertIn("sid=REDACTED", menu_res["items"][0]["onclick"])
            finally:
                if bridge._db is not None:
                    bridge._db.close()

    def test_cross_bridge_concurrent_ga_nn_plan_isolation_and_config_cache_clear(self) -> None:
        from dataclasses import replace as dc_replace
        import sys

        base_a = _sample_state()
        p_a = dc_replace(
            base_a.planets[0],
            planet_id=101,
            coords="02:111:12",
            buildings={"b1": 4, "b2": 3, "b3": 4, "b5": 8, "b9": 2},
            ships={"s4": 25, "s1": 8},
            resources=Resources(8000, 6000, 4000, 2000),
            tutorial=None,
        ) if hasattr(base_a.planets[0], "tutorial") else dc_replace(
            base_a.planets[0],
            planet_id=101,
            coords="02:111:12",
            buildings={"b1": 4, "b2": 3, "b3": 4, "b5": 8, "b9": 2},
            ships={"s4": 25, "s1": 8},
            resources=Resources(8000, 6000, 4000, 2000),
        )
        state_a = dc_replace(base_a, planets=(p_a,), active_planet_id=101, tutorial=None)
        state_b = dc_replace(
            state_a,
            planets=(dc_replace(p_a, planet_id=202, coords="09:222:12"),),
            active_planet_id=202,
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_a = tmp_path / "cfg_a.yaml"
            cfg_b = tmp_path / "cfg_b.yaml"
            cfg_a.write_text("planner_mode: ga_nn\nga_generations: 1\nga_population_size: 4\nga_seed: 101\nallow_raids: true\n", encoding="utf-8")
            cfg_b.write_text("planner_mode: ga_nn\nga_generations: 5\nga_population_size: 4\nga_seed: 202\nallow_raids: true\n", encoding="utf-8")

            client_a = MagicMock()
            client_a.__enter__.return_value = client_a
            client_a.read_full_snapshot.return_value = state_a

            client_b = MagicMock()
            client_b.__enter__.return_value = client_b
            client_b.read_full_snapshot.return_value = state_b

            bridge_a = GameApiBridge(
                debug_port=9222,
                config_path=cfg_a,
                log_dir=None,
                db_path=tmp_path / "db_a.db",
                client_factory=lambda _p: client_a,
                ensure_session_fn=lambda: None,
            )
            bridge_b = GameApiBridge(
                debug_port=9223,
                config_path=cfg_b,
                log_dir=None,
                db_path=tmp_path / "db_b.db",
                client_factory=lambda _p: client_b,
                ensure_session_fn=lambda: None,
            )
            try:
                assert bridge_a._db is not None and bridge_b._db is not None
                bridge_a._db.record_raid("02:111:01", attacked_ts=1_700_000_100.0, is_exempt=True, outcome="Victory", loot={"pig_iron": 10000})
                bridge_b._db.record_raid("09:222:09", attacked_ts=1_700_000_200.0, is_exempt=True, outcome="Victory", loot={"pig_iron": 20000})

                errors: list[str] = []
                barrier = threading.Barrier(2)

                def _worker(tag: str, br: GameApiBridge, exp_gens: int, exp_seed: int, own_c: str, other_c: str) -> None:
                    barrier.wait()
                    for idx in range(25):
                        res = br.handle_command({"command": "plan", "planner_mode": "ga_nn"})
                        summ = res.get("optimizer_summary") or {}
                        pt = ((summ.get("attack_history_stats") or {}).get("per_target") or {})
                        if summ.get("generations") != exp_gens or summ.get("seed") != exp_seed:
                            errors.append(f"{tag}[{idx}] gens={summ.get('generations')} seed={summ.get('seed')}")
                        if own_c not in pt or other_c in pt:
                            errors.append(f"{tag}[{idx}] per_target={list(pt.keys())}")

                old_interval = sys.getswitchinterval()
                sys.setswitchinterval(1e-6)
                try:
                    ta = threading.Thread(target=_worker, args=("A", bridge_a, 1, 101, "02:111:01", "09:222:09"))
                    tb = threading.Thread(target=_worker, args=("B", bridge_b, 5, 202, "09:222:09", "02:111:01"))
                    ta.start()
                    tb.start()
                    ta.join(timeout=30)
                    tb.join(timeout=30)
                finally:
                    sys.setswitchinterval(old_interval)

                self.assertEqual(errors, [])

                # Changing GA hyperparameters while _last_state is None clears cached _last_optimizer_summary
                bridge_a._last_state = None
                bridge_a._last_optimizer_summary = {"generations": 1, "seed": 101}
                bridge_a.handle_command({"command": "update_config", "config": {"ga_generations": 3}})
                with patch("spaceinvasion.api_server.is_debug_port_open", return_value=False):
                    dash_cleared = bridge_a.dashboard(refresh=False)
                self.assertIsNone(dash_cleared["optimizer_summary"])
            finally:
                if bridge_a._db is not None:
                    bridge_a._db.close()
                if bridge_b._db is not None:
                    bridge_b._db.close()

    def test_huge_float_non_ga_validation_and_bool_command_ids_rejected(self) -> None:
        import requests

        state = _sample_state()
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.read_full_snapshot.return_value = state

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.yaml"
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=None,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                base = f"http://127.0.0.1:{port}"
                hostile_configs = [
                    {"ga_seed": 1e308},
                    {"ga_seed": 2**31},
                    {"ga_seed": -(2**31) - 1},
                    {"max_actions_per_hour": 1e308},
                    {"max_actions_per_hour": 100_001},
                    {"combat_safety_margin": 1e308},
                    {"combat_safety_margin": 0.99},
                    {"combat_safety_margin": -1},
                    {"raid_min_loot_spice_eq": 1e308},
                    {"raid_min_loot_spice_eq": -1},
                    {"attack_min_res_per_hour": 1e308},
                    {"attack_min_res_per_hour": -1},
                    {"recycle_min_res_per_hour": 1e308},
                    {"recycle_min_res_per_hour": -1},
                    {"storage_warn_ratio": 0.05},
                    {"storage_warn_ratio": 1.0},
                    {"storage_warn_ratio": -1},
                    {"blast_pig_iron_max_share": 0.05},
                    {"blast_pig_iron_max_share": 1.05},
                    {"fleet_save_fuel_reserve_spice": "NaN"},
                    {"fleet_save_fuel_reserve_spice": -1},
                    {"fleet_save_fuel_reserve_spice": 1e308},
                    {"min_free_fields_warning": "bogus"},
                    {"min_free_fields_warning": -1},
                    {"min_free_fields_warning": 1001},
                    {"min_free_fields_warning": True},
                ]
                for bad in hostile_configs:
                    resp = requests.post(
                        f"{base}/command",
                        json={"command": "update_config", "config": bad},
                        timeout=5,
                    )
                    self.assertEqual(resp.status_code, 400, msg=f"Expected 400 for {bad!r}, got {resp.status_code}")

                good = requests.post(
                    f"{base}/command",
                    json={
                        "command": "update_config",
                        "config": {
                            "storage_warn_ratio": 0.9,
                            "blast_pig_iron_max_share": 0.8,
                            "combat_safety_margin": 2.0,
                            "raid_min_loot_spice_eq": 5000.0,
                            "attack_min_res_per_hour": 15000.0,
                            "recycle_min_res_per_hour": 9500.0,
                            "fleet_save_fuel_reserve_spice": 750,
                            "min_free_fields_warning": 15,
                        },
                    },
                    timeout=5,
                )
                self.assertEqual(good.status_code, 200, good.text)

                bool_cmds = [
                    {"command": "build", "building_id": True, "dry_run": True},
                    {"command": "research", "research_id": False, "dry_run": True},
                    {"command": "build_ship", "ship_id": True, "quantity": 1, "dry_run": True},
                    {"command": "build_ship", "ship_id": 1, "quantity": True, "dry_run": True},
                    {"command": "throttle_building", "building_id": True, "percentage": 50, "dry_run": True},
                    {"command": "throttle_building", "building_id": 2, "percentage": True, "dry_run": True},
                    {"command": "build", "building_id": 1, "planet_id": True, "dry_run": True},
                ]
                for payload in bool_cmds:
                    r_bool = requests.post(f"{base}/command", json=payload, timeout=5)
                    self.assertEqual(r_bool.status_code, 400, msg=f"Expected 400 for {payload!r}, got {r_bool.status_code}")
            finally:
                server.shutdown()
                server.server_close()

    def test_iteration3_config_parity_simulate_combat_command_bounds_and_sid_redaction(self) -> None:
        import requests
        from spaceinvasion.auth import SpaceInvasionAuthError
        from spaceinvasion.client import RateLimitError

        secret_plain = "rawToken_ABC987"
        secret_enc = "encTok_999"
        state = _sample_state()
        nav_html = (
            f'<html><body>'
            f'<a href="indexInternal.es?action=internalHome&next=foo%3Fsid%3D{secret_enc}%26bar%3D1">Home</a>'
            f'<a href="indexInternal.es?sid%3D{secret_enc}&action=internalBuildings">Bld</a>'
            f'<a href="indexInternal.es?action=internalResearch&SID={secret_plain}">Res</a>'
            f'</body></html>'
        )
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.driver = MagicMock(
            current_url=f"https://spaceinvasion.bitmeup.com/indexGame.es?sid={secret_plain}",
            page_source=nav_html,
        )
        fake_client.read_full_snapshot.return_value = state
        fake_client.navigate_menu.return_value = nav_html

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.yaml"
            logs_dir = tmp_path / "logs"
            db_path = tmp_path / "state" / "spaceinvasion.db"
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=logs_dir,
                db_path=db_path,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                base = f"http://127.0.0.1:{port}"

                # 1) Config parity: fleet_save_fuel_reserve_spice int-only + min_free_fields_warning >= 1
                r_set = requests.post(
                    f"{base}/command",
                    json={"command": "update_config", "config": {"fleet_save_fuel_reserve_spice": 750}},
                    timeout=5,
                )
                self.assertEqual(r_set.status_code, 200)
                self.assertEqual(r_set.json()["config"]["fleet_save_fuel_reserve_spice"], 750)

                for bad_cfg in (
                    {"fleet_save_fuel_reserve_spice": "100.5"},
                    {"fleet_save_fuel_reserve_spice": 100.9},
                    {"min_free_fields_warning": 0},
                ):
                    r_bad = requests.post(
                        f"{base}/command",
                        json={"command": "update_config", "config": bad_cfg},
                        timeout=5,
                    )
                    self.assertEqual(r_bad.status_code, 400, msg=f"bad_cfg={bad_cfg!r}: {r_bad.text}")
                    self.assertEqual(bridge.strategy_config.fleet_save_fuel_reserve_spice, 750)

                # 2) simulate_combat malformed types & non-finite safety_margin -> 400 (no 500 or Infinity JSON)
                bad_sims = [
                    {"command": "simulate_combat", "attacker_ships": [1]},
                    {"command": "simulate_combat", "attacker_ships": []},
                    {"command": "simulate_combat", "attacker_ships": {"s4": [1]}},
                    {"command": "simulate_combat", "defender_ships": {"s4": -1}},
                    {"command": "simulate_combat", "defender_defense": {"d1": True}},
                    {"command": "simulate_combat", "defender_researches": "r8"},
                    {"command": "simulate_combat", "target_resources": {"pig_iron": 1.5}},
                    {"command": "simulate_combat", "attacker_ships": {"s4": 10}, "safety_margin": None},
                    {"command": "simulate_combat", "attacker_ships": {"s4": 10}, "safety_margin": "Inf"},
                    {"command": "simulate_combat", "attacker_ships": {"s4": 10}, "safety_margin": "NaN"},
                    {"command": "simulate_combat", "attacker_ships": {"s4": 10}, "safety_margin": 1e308},
                ]
                for bad_sim in bad_sims:
                    r_sim = requests.post(f"{base}/command", json=bad_sim, timeout=5)
                    self.assertEqual(r_sim.status_code, 400, msg=f"bad_sim={bad_sim!r}: {r_sim.text}")

                def _forbid_nonfinite(tok: str) -> None:
                    raise AssertionError(f"Non-finite JSON constant emitted: {tok}")

                r_sim_ok = requests.post(
                    f"{base}/command",
                    json={"command": "simulate_combat", "attacker_ships": {"s4": 20}},
                    timeout=5,
                )
                self.assertEqual(r_sim_ok.status_code, 200)
                parsed_ok = json.loads(r_sim_ok.text, parse_constant=_forbid_nonfinite)
                self.assertTrue(parsed_ok["ok"])

                # 3) Command ID/quantity bounds -> 400 (no 2**63 SQLite 500, no negative silent clamping)
                bad_cmds = [
                    {"command": "build_ship", "ship_id": 1, "quantity": 2**63, "dry_run": True},
                    {"command": "build_ship", "ship_id": -1, "quantity": 1, "dry_run": True},
                    {"command": "build_ship", "ship_id": 1, "quantity": 0, "dry_run": True},
                    {"command": "build_ship", "ship_id": 1, "quantity": -5, "dry_run": True},
                    {"command": "build", "building_id": -5, "dry_run": True},
                    {"command": "build", "building_id": 0, "dry_run": True},
                    {"command": "build", "building_id": 1, "planet_id": 0, "dry_run": True},
                    {"command": "build", "building_id": 1, "planet_id": 2**63, "dry_run": True},
                    {"command": "research", "research_id": -1, "dry_run": True},
                    {"command": "spy", "target_coords": "02:380:04", "quantity": -5, "dry_run": True},
                    {"command": "spy", "target_coords": "02:380:04", "quantity": 0, "dry_run": True},
                    {"command": "throttle_building", "building_id": -1, "percentage": 50, "dry_run": True},
                    {"command": "throttle_building", "building_id": 2, "percentage": -50, "dry_run": True},
                    {"command": "throttle_building", "building_id": 2, "percentage": 110, "dry_run": True},
                    {"command": "navigate", "action": "internalGalaxy", "galaxy": 1001},
                    {"command": "scan_galaxy", "system": 1001, "dry_run": True},
                ]
                for bad_cmd in bad_cmds:
                    r_cmd = requests.post(f"{base}/command", json=bad_cmd, timeout=5)
                    self.assertEqual(r_cmd.status_code, 400, msg=f"bad_cmd={bad_cmd!r}: {r_cmd.text}")

                # 4) Remaining sid leak vectors: navigate(include_html=True), URL-encoded sid%3D in menu, & HTTP error strings
                r_nav = requests.post(
                    f"{base}/command",
                    json={"command": "navigate", "action": "internalHome", "include_html": True},
                    timeout=5,
                )
                self.assertEqual(r_nav.status_code, 200)
                self.assertNotIn(secret_plain, r_nav.text)
                self.assertNotIn(secret_enc, r_nav.text)
                self.assertIn("sid%3DREDACTED", r_nav.json()["html"])

                r_menu = requests.post(f"{base}/command", json={"command": "menu"}, timeout=5)
                self.assertEqual(r_menu.status_code, 200)
                self.assertNotIn(secret_plain, r_menu.text)
                self.assertNotIn(secret_enc, r_menu.text)
                items = r_menu.json()["items"]
                self.assertIn("next=foo%3Fsid%3DREDACTED%26bar%3D1", items[0]["href"])
                self.assertEqual(items[1]["href"], "indexInternal.es?action=internalBuildings")

                # HTTP 429, 503, 500 error strings scrubbed across GET & POST + logs
                with patch.object(
                    bridge,
                    "handle_command",
                    side_effect=RateLimitError(
                        f"Throttled at https://spaceinvasion.bitmeup.com/indexGame.es?SID={secret_plain}&next=a%3Fsid%3D{secret_enc}",
                        retry_after=12.0,
                    ),
                ):
                    r_429 = requests.post(f"{base}/command", json={"command": "plan"}, timeout=5)
                self.assertEqual(r_429.status_code, 429)
                self.assertNotIn(secret_plain, r_429.text)
                self.assertNotIn(secret_enc, r_429.text)

                with patch.object(
                    bridge,
                    "handle_command",
                    side_effect=SpaceInvasionAuthError(
                        f"Session lost at https://spaceinvasion.bitmeup.com/indexGame.es?sid={secret_plain}"
                    ),
                ):
                    r_503 = requests.post(f"{base}/command", json={"command": "plan"}, timeout=5)
                self.assertEqual(r_503.status_code, 503)
                self.assertNotIn(secret_plain, r_503.text)
                self.assertIn("sid=REDACTED", r_503.json()["error"])

                with patch.object(
                    bridge,
                    "handle_command",
                    side_effect=RuntimeError(f"Unexpected err ?sid%3D{secret_enc}"),
                ):
                    r_500 = requests.post(f"{base}/command", json={"command": "plan"}, timeout=5)
                self.assertEqual(r_500.status_code, 500)
                self.assertNotIn(secret_enc, r_500.text)

                with patch.object(
                    bridge,
                    "dashboard",
                    side_effect=SpaceInvasionAuthError(f"Auth expired ?SID={secret_plain}"),
                ):
                    r_get_503 = requests.get(f"{base}/dashboard", timeout=5)
                self.assertEqual(r_get_503.status_code, 503)
                self.assertNotIn(secret_plain, r_get_503.text)

                for log_file in logs_dir.glob("game_api.*"):
                    log_txt = log_file.read_text(encoding="utf-8")
                    self.assertNotIn(secret_plain, log_txt)
                    self.assertNotIn(secret_enc, log_txt)
            finally:
                server.shutdown()
                server.server_close()
                if bridge._db is not None:
                    bridge._db.close()

    def test_tier5_sid_pct3d_log_redaction_stale_research_active_prereqs_and_commander_asteroid_cap(self) -> None:
        from spaceinvasion.logging_utils import append_api_log
        from spaceinvasion.state import QueueJob, ResearchOffer, UnitOffer

        with tempfile.TemporaryDirectory(prefix="si_t5_api_") as tmp:
            log_dir = Path(tmp) / "logs"
            jsonl_p, txt_p = append_api_log(
                log_dir,
                method="POST",
                path="/command?sid=PLAIN_SECRET_11&x=1&sid%3DENC_SECRET_22",
                status=200,
                payload={"cmd": "ok", "next": "url?sid%3DENC_SECRET_22"},
            )
            for content in (jsonl_p.read_text(encoding="utf-8"), txt_p.read_text(encoding="utf-8")):
                self.assertNotIn("PLAIN_SECRET_11", content)
                self.assertNotIn("ENC_SECRET_22", content)

            # _merge_cached_offers clears stale active=True when no research queue job exists
            def _mk_p(buildings: dict[str, int], planet_type: str = "normal", **kw) -> PlanetState:
                return PlanetState(
                    planet_id=1,
                    name="Home",
                    coords="01:001:01",
                    planet_type=planet_type,
                    temperature=20,
                    fields_used=10,
                    fields_max=200,
                    resources=Resources(50_000, 50_000, 50_000, 50_000),
                    storage=Resources(100_000, 100_000, 100_000, 100_000),
                    energy=EnergyBalance(200, 50),
                    buildings=buildings,
                    **kw,
                )

            p0 = _mk_p({"b8": 2, "b9": 2})
            prev_st = GameState(
                31,
                1,
                "u",
                100.0,
                (p0,),
                active_planet_id=1,
                research_offers=(
                    ResearchOffer(2, "r2", "Energy technology", 0, 1, cost=Resources(), status_text="Active", can_research=False, active=True),
                ),
            )
            cur_st = GameState(
                31,
                1,
                "u",
                110.0,
                (p0,),
                active_planet_id=1,
                research_offers=(),
                active_queues=(),
            )
            cfg_p = Path(tmp) / "cfg.yaml"
            bridge = GameApiBridge(
                client_factory=lambda _port: MagicMock(),
                config_path=cfg_p,
                db_path=Path(tmp) / "bridge.db",
                log_dir=log_dir,
            )
            try:
                bridge._last_state = prev_st
                merged = bridge._merge_cached_offers(cur_st)
                self.assertFalse(merged.research_offers[0].active)

                # Missing prereqs rejected even when status_text mentions insufficient resources
                locked_r_st = GameState(
                    31,
                    1,
                    "u",
                    120.0,
                    (_mk_p({"b8": 2, "b9": 1}),),
                    active_planet_id=1,
                    research_offers=(
                        ResearchOffer(11, "r11", "Particle technology", 0, 1, cost=Resources(), can_research=False, status_text="not enough resources"),
                    ),
                )
                r_act = bridge._find_research_action(locked_r_st, 11, 1)
                self.assertFalse(r_act.executable_now)

                locked_s_st = GameState(
                    31,
                    1,
                    "u",
                    120.0,
                    (
                        _mk_p(
                            {"b9": 1},
                            ship_offers=(
                                UnitOffer("ship", 6, "Destroyer", 0, cost=Resources(), can_build=False, status_text="insufficient resources"),
                            ),
                        ),
                    ),
                    active_planet_id=1,
                )
                s_act = bridge._find_ship_action(locked_s_st, 6, 1, 1)
                self.assertFalse(s_act.executable_now)
            finally:
                if bridge._db is not None:
                    bridge._db.close()

            # commander NaN min_backoff + asteroid queue cap=1 skips unnecessary include_buildings=True refresh
            self.assertEqual(commander.compute_rate_limit_backoff("30 page views", min_backoff=float("nan")), 45.0)
            ast_p = _mk_p({"b18": 1}, planet_type="asteroid")
            ast_st = GameState(
                31,
                1,
                "u",
                100.0,
                (ast_p,),
                active_planet_id=1,
                active_queues=(QueueJob("building", "Sensor", building_id=18, target_level=2),),
            )
            mock_api = MagicMock()
            mock_api.get_state.return_value = ast_st
            commander.run_commander_tick(mock_api, live=False, include_buildings=False, max_actions=1)
            self.assertEqual(mock_api.get_state.call_count, 1)

    def test_unnormalized_coords_api_guards_external_research_cancel_and_deep_sid_redaction(self) -> None:
        from spaceinvasion.api_server import _redact_api_payload
        from spaceinvasion.state import ResearchOffer
        from dataclasses import replace
        from spaceinvasion.strategy import plan_actions

        # 1. BUG-08: Multi-encoded sid%253D, JSON "sid": "...", and dict/Mapping "sid"/"SID" redaction
        sec_enc = "DOUBLE_ENC_SECRET_991"
        sec_json = "JSON_KEY_SECRET_992"
        sec_dict = "DICT_SECRET_993"
        for fn in (redact_sensitive, _redact_api_payload):
            out = fn(
                {
                    "sid": sec_dict,
                    "SID": sec_dict,
                    "url": f"https://uni1.test/index.php?sid%253D{sec_enc}",
                    "json_txt": json.dumps({"sid": sec_json, "token": "ok"}),
                    "single_q": f"{{'sid': '{sec_json}'}}",
                }
            )
            dumped = json.dumps(out)
            self.assertNotIn(sec_enc, dumped)
            self.assertNotIn(sec_json, dumped)
            self.assertNotIn(sec_dict, dumped)
            self.assertEqual(out["sid"], "REDACTED")
            self.assertEqual(out["SID"], "REDACTED")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_p = tmp_path / "cfg.yaml"
            cfg_p.write_text("allow_raids: true\nallow_pvp_in_az: false\n", encoding="utf-8")
            p_own = PlanetState(
                planet_id=101,
                name="Home",
                coords="02:380:04",
                planet_type="normal",
                temperature=20,
                fields_used=20,
                fields_max=200,
                resources=Resources(200_000, 200_000, 200_000, 200_000),
                storage=Resources(500_000, 500_000, 500_000, 500_000),
                energy=EnergyBalance(500, 100),
                buildings={"b1": 4, "b8": 4, "b9": 2},
                ships={"s4": 20, "s1": 10, "s10": 5},
            )
            st0 = GameState(
                31,
                1,
                "u",
                1000.0,
                (p_own,),
                active_planet_id=101,
                researches={"r1": 1, "r2": 4, "r4": 2},
            )
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.read_full_snapshot.return_value = st0
            fake_client.read_planets_overview.return_value = st0
            fake_client.read_menu_status.return_value = ({}, None, None)

            bridge = GameApiBridge(
                client_factory=lambda _port: fake_client,
                config_path=cfg_p,
                db_path=tmp_path / "api.db",
                log_dir=tmp_path / "logs",
                ensure_session_fn=lambda: None,
            )
            try:
                assert bridge._db is not None
                # 2. BUG-01: Unnormalized "2:380:4" blocked on own planet, "2:380:9" blocked on blue, "2:380:11" exempt
                from spaceinvasion.fleet import GalaxySlot

                bridge._db.record_galaxy_slots(
                    [
                        GalaxySlot("02:380:09", 9, "BlueTarget", "VacOwner", "blue"),
                        GalaxySlot("02:380:11", 11, "IdleTarget", "Sleeper", "yellow"),
                    ],
                    ts=1000.0,
                )
                bridge._raid_targets = bridge._enrich_raid_targets(bridge._db.get_spy_targets(limit=100))
                bridge._last_state = st0
                bridge._last_state_ts = 1e12

                own_spy = bridge.handle_command({"command": "spy", "target_coords": "2:380:4", "dry_run": False})
                self.assertFalse(own_spy["executed"])
                self.assertIn("own planets", own_spy["message"])

                own_atk = bridge.handle_command({"command": "attack", "target_coords": "2:380:4", "dry_run": False})
                self.assertFalse(own_atk["executed"])
                self.assertIn("own planets", own_atk["message"])

                blue_atk = bridge.handle_command({"command": "attack", "target_coords": "2:380:9", "dry_run": False})
                self.assertFalse(blue_atk["executed"])
                self.assertIn("protected", blue_atk["message"].lower())

                # 3. BUG-04: External research cancel clears active AND restores can_research on unlocked affordable offers
                r2_active = ResearchOffer(2, "r2", "Energy technology", 4, 5, Resources(0, 6400, 12800, 6400), False, "In research (Cancel)", None, active=True)
                r1_busy = ResearchOffer(1, "r1", "Mining technology", 1, 2, Resources(400, 200, 0, 0), False, "Research queue busy", None, active=False)
                r11_locked = ResearchOffer(11, "r11", "Particle technology", 0, 1, Resources(1000, 1000, 1000, 0), False, "Unavailable", None, active=False)
                bridge._last_state = replace(st0, research_offers=(r2_active, r1_busy, r11_locked))
                merged = bridge._merge_cached_offers(replace(st0, research_offers=(), active_queues=()))
                by_rid = {o.research_id: o for o in merged.research_offers}
                self.assertFalse(by_rid[2].active)
                self.assertTrue(by_rid[2].can_research)
                self.assertTrue(by_rid[1].can_research)
                self.assertFalse(by_rid[11].can_research)
                self.assertTrue(bridge._find_research_action(merged, 2, 101, allow_live_verify=True).executable_now)
                self.assertFalse(
                    any(a.kind == "RESEARCH" and a.executable_now for a in plan_actions(bridge._last_state))
                )
                acts_after_cancel = plan_actions(merged)
                self.assertTrue(any(a.kind == "RESEARCH" and a.executable_now for a in acts_after_cancel))

                # GAP-01 & GAP-03: Attack target ranked past top-100 _raid_targets still blocked via DB lookup,
                # corrupt DB JSON fields do not crash attack, and externally canceled zero-cost ResearchOffer
                # restores real catalog cost for both affordable and unaffordable offers.
                many_yellow = [
                    GalaxySlot(f"05:{idx:03d}:01", 1, f"Farm{idx}", f"Idle{idx}", "yellow")
                    for idx in range(1, 106)
                ]
                many_yellow.append(GalaxySlot("09:009:09", 9, "DeepBlue", "VacDeep", "blue"))
                bridge._db.record_galaxy_slots(many_yellow, ts=2000.0)
                bridge._db._conn.execute(
                    "UPDATE spy_targets SET pig_iron='bad', spice=100, "
                    "fleet_json='{\"s1\": \"bad\"}', defense_json='{\"d1\": \"bad\"}' "
                    "WHERE coords='05:001:01'"
                )
                bridge._db._conn.commit()
                bridge._raid_targets = bridge._enrich_raid_targets(bridge._db.get_spy_targets(limit=100))
                self.assertFalse(any(r.get("coords") == "09:009:09" for r in bridge._raid_targets))
                deep_blue_atk = bridge.handle_command(
                    {"command": "attack", "target_coords": "9:9:9", "dry_run": False}
                )
                self.assertFalse(deep_blue_atk["executed"])
                self.assertIn("protected", deep_blue_atk["message"].lower())
                corrupt_dry_atk = bridge.handle_command(
                    {"command": "attack", "target_coords": "5:1:1", "dry_run": True}
                )
                self.assertFalse(corrupt_dry_atk["executed"])

                # GAP-03: Zero-cost active ResearchOffer after external cancel restores true catalog cost
                from spaceinvasion.ranking import compute_action_eta
                from spaceinvasion.state import ResearchOffer

                p_lab = replace(
                    st0.planets[0],
                    buildings={**st0.planets[0].buildings, "b8": 2},
                    resources=Resources(3000, 1000, 2000, 500),
                )
                st_lab = replace(st0, planets=(p_lab,), researches={"r1": 1, "r2": 4})
                r1_zero_active = ResearchOffer(
                    1, "r1", "Mining technology", 1, 2, Resources(), False, "In research (Cancel)", None, active=True
                )
                r2_zero_unaffordable = ResearchOffer(
                    2, "r2", "Energy technology", 4, 5, Resources(), False, "Research queue busy", None, active=False
                )
                bridge._last_state = replace(st_lab, research_offers=(r1_zero_active, r2_zero_unaffordable))
                merged_cost = bridge._merge_cached_offers(replace(st_lab, research_offers=(), active_queues=()))
                by_id_cost = {o.research_id: o for o in merged_cost.research_offers}
                self.assertFalse(by_id_cost[1].cost.is_zero())
                self.assertTrue(by_id_cost[1].can_research)
                self.assertFalse(by_id_cost[2].cost.is_zero())
                self.assertFalse(by_id_cost[2].can_research)
                self.assertEqual(by_id_cost[2].status_text, "Not enough resources")
                eta_unaff = compute_action_eta(
                    merged_cost,
                    {
                        "kind": "RESEARCH",
                        "planet_id": p_lab.planet_id,
                        "target_id": 2,
                        "target_name": "Energy technology",
                        "target_level": 5,
                        "cost": by_id_cost[2].cost,
                    },
                )
                self.assertNotEqual(eta_unaff["wait_eta"], "Ready")
                self.assertIn(eta_unaff["eta_bottleneck"], ("metal", "kryptonite", "spice"))
            finally:
                bridge._db.close()

    def test_attack_blocks_dark_red_and_state_dashboard_log_huge_ints_safe(self) -> None:
        import math
        import requests
        from spaceinvasion import game_data
        from spaceinvasion.fleet import GalaxySlot
        from spaceinvasion.logging_utils import append_api_log

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            cfg_path = tp / "config.yaml"
            cfg_path.write_text(
                "planner_mode: priority\nallow_raids: true\nallow_pvp_in_az: true\n",
                encoding="utf-8",
            )
            exact_pid = 10**18 + 123456789
            p_huge = PlanetState(
                planet_id=exact_pid,
                name="Colony",
                coords="02:380:12",
                planet_type="normal",
                temperature=31,
                fields_used=5,
                fields_max=10**1_000_000,
                resources=Resources(10**1_000_000, 1000, 500, 1000),
                storage=Resources(100000, 100000, 100000, 100000),
                energy=EnergyBalance(10**5000, 100),
                buildings={"b1": 10**1_000_000, "b5": 5},
                ships={"s1": 10**1_000_000, "s4": 10},
            )
            st_huge = GameState(
                universe_id=31,
                user_id=1,
                nickname="Tester",
                timestamp=1_700_000_000.0,
                planets=(p_huge,),
                active_planet_id=exact_pid,
                account_points=10**1_000_000,
                account_rank=5,
            )
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.driver = MagicMock(current_url="https://uni/indexGame.es?action=internalHome")
            fake_client.read_full_snapshot.return_value = st_huge

            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=tp / "logs",
                db_path=tp / "state.db",
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                bridge._db.record_galaxy_slots(
                    [GalaxySlot("05:010:03", 3, "DarkTarget", "Titan", "dark_red")],
                    ts=1_700_000_000.0,
                )
                with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                    r_atk = requests.post(
                        f"http://127.0.0.1:{port}/command",
                        json={"command": "attack", "target_coords": "05:010:03", "dry_run": False},
                        timeout=5,
                    )
                    self.assertEqual(r_atk.status_code, 200)
                    atk_data = r_atk.json()
                    self.assertFalse(atk_data.get("executed"))
                    self.assertIn("protected", str(atk_data.get("message", "")).lower())

                    r_state = requests.get(f"http://127.0.0.1:{port}/state", timeout=5)
                    self.assertEqual(r_state.status_code, 200)
                    st_payload = json.loads(
                        r_state.text,
                        parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)),
                    )
                    self.assertEqual(st_payload["planets"][0]["planet_id"], exact_pid)
                    self.assertEqual(st_payload["account_points"], game_data.MAX_JSON_SAFE_INT)

                    r_dash = requests.get(f"http://127.0.0.1:{port}/dashboard", timeout=5)
                    self.assertEqual(r_dash.status_code, 200)
                    dash_payload = json.loads(
                        r_dash.text,
                        parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)),
                    )
                    self.assertTrue(dash_payload["ok"])

                    r_nan = requests.post(
                        f"http://127.0.0.1:{port}/command",
                        data=b'{"command":"plan","x":NaN}',
                        headers={"Content-Type": "application/json"},
                        timeout=5,
                    )
                    self.assertEqual(r_nan.status_code, 400)

                append_tick_logs(
                    tp / "logs",
                    source="commander",
                    tick=1,
                    mode="priority",
                    state=st_huge,
                    actual_action=None,
                    actual_result={"ok": True, "nan": float("nan"), "huge": 10**1_000_000},
                    next_action=None,
                )
                append_api_log(
                    tp / "logs",
                    method="POST",
                    path="/command",
                    status=200,
                    payload={"huge": 10**1_000_000, "inf": float("inf")},
                    response={"huge": 10**400, "nan": float("nan")},
                )
                for jf in (tp / "logs").glob("*.jsonl"):
                    for line in jf.read_text(encoding="utf-8").splitlines():
                        json.loads(line, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
            finally:
                server.shutdown()
                server.server_close()
                bridge._db.close()

    def test_bug01_commander_client_non_dict_json_loop_recovery_and_selenium_free(self) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import io
        import os
        import subprocess
        import sys

        routes: dict[str, list[tuple[int, str]]] = {}

        class _MockHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args) -> None:
                return

            def _reply(self, status: int, body: str) -> None:
                raw = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                seq = routes.get(path)
                if seq:
                    st_code, payload = seq.pop(0) if len(seq) > 1 else seq[0]
                    self._reply(st_code, payload)
                    return
                self._reply(404, '{"ok":false,"error":"not found"}')

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length > 0:
                    self.rfile.read(length)
                path = self.path.split("?", 1)[0]
                seq = routes.get(path)
                if seq:
                    st_code, payload = seq.pop(0) if len(seq) > 1 else seq[0]
                    self._reply(st_code, payload)
                    return
                self._reply(404, '{"ok":false,"error":"not found"}')

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        base_url = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with GameApiClient(base_url=base_url, timeout=2.0) as client:
                for path, st_code, body in (
                    ("/health", 200, "[]"),
                    ("/health", 200, "null"),
                    ("/health", 200, '"bad"'),
                    ("/health", 200, "123"),
                    ("/bot_status", 502, '["bad"]'),
                    ("/state", 500, "null"),
                    ("/command", 400, '"bad"'),
                    ("/command", 429, '["rate"]'),
                ):
                    routes[path] = [(st_code, body)]
                    with self.assertRaises(GameApiError) as exc_ctx:
                        if path == "/health":
                            client.health()
                        elif path == "/bot_status":
                            client.get_bot_control()
                        elif path == "/state":
                            client.get_state()
                        else:
                            client.send_command({"command": "plan"})
                    if st_code == 429:
                        self.assertIn("429", str(exc_ctx.exception))

            # compute_rate_limit_backoff handles 10**400 and huge retry-after numbers safely
            backoff_val = commander.compute_rate_limit_backoff(
                f"retry after {'9' * 500}s",
                {"retry_after": 10**400},
                min_backoff=10**400,  # type: ignore[arg-type]
            )
            self.assertIsNotNone(backoff_val)
            self.assertTrue(math.isfinite(float(backoff_val)))

            # Test commander.main --loop recovery after tick 1 502 ["bad"] and 200 null
            state_dict = {
                "universe_id": 31,
                "user_id": 1,
                "nickname": "CmdTest",
                "timestamp": 1_700_000_000.0,
                "active_planet_id": 101,
                "planets": [
                    {
                        "planet_id": 101,
                        "name": "Home",
                        "coords": "01:010:01",
                        "planet_type": "normal",
                        "temperature": 20,
                        "fields_used": 10,
                        "fields_max": 200,
                        "resources": {"pig_iron": 500, "metal": 100, "kryptonite": 100, "spice": 50},
                        "storage": {"pig_iron": 100000, "metal": 100000, "kryptonite": 100000, "spice": 100000},
                        "energy": {"produced": 100, "used": 50},
                        "buildings": {"b1": 1},
                    }
                ],
                "specs": {"miner": 10**400, "construction_commander": 10**400, "fleeter": "bad"},
            }
            valid_state_json = json.dumps(state_dict)
            valid_plan_json = json.dumps({"ok": True, "actions": []})
            corrupt_bot_json = '{"paused": false, "scanned_systems": {"01:010": 1000.0}, "throttle_cooldowns": {"²": 100.0, "999999999999999999999": 100.0, "101": ' + ("9" * 500) + "}}"

            with tempfile.TemporaryDirectory() as tmp:
                tp = Path(tmp)
                cfg_p = tp / "cfg.yaml"
                cfg_p.write_text("planner_mode: priority\n", encoding="utf-8")

                for tick1_route, tick1_resp in (
                    ("/state", (502, '["bad"]')),
                    ("/command", (200, "null")),
                ):
                    routes["/bot_status"] = [(200, corrupt_bot_json), (200, corrupt_bot_json)]
                    routes["/state"] = [(200, valid_state_json), (200, valid_state_json)]
                    routes["/command"] = [(200, valid_plan_json), (200, valid_plan_json)]
                    routes[tick1_route] = [tick1_resp, (200, valid_state_json if tick1_route == "/state" else valid_plan_json)]
                    with (
                        patch("commander.time.sleep", return_value=None),
                        patch("sys.stdout", new_callable=io.StringIO) as out_buf,
                    ):
                        rc = commander.main(
                            [
                                "--api-url",
                                base_url,
                                "--config",
                                str(cfg_p),
                                "--loop",
                                "--max-ticks",
                                "2",
                                "--interval",
                                "0.01",
                            ]
                        )
                    self.assertEqual(rc, 0)
                    self.assertIn("tick 1", out_buf.getvalue())
                    self.assertIn("tick 2", out_buf.getvalue())

                # Direct run_commander_tick(..., include_buildings=False) with specs={'construction_commander': 10**400}
                routes["/state"] = [(200, valid_state_json)]
                routes["/command"] = [(200, valid_plan_json)]
                with GameApiClient(base_url=base_url, timeout=2.0) as tick_client:
                    tick_state, _, _, _, _ = commander.run_commander_tick(
                        tick_client,
                        live=False,
                        max_actions=1,
                        config=StrategyConfig(planner_mode="priority"),
                        include_buildings=False,
                    )
                self.assertEqual(tick_state.active_planet_id, 101)

            env_no_reexec = os.environ.copy()
            env_no_reexec["SPACEINVASION_NO_REEXEC"] = "1"
            sub = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.path.insert(0, 'bin'); import commander; assert not any(m == 'selenium' or m.startswith('selenium.') for m in sys.modules)",
                ],
                env=env_no_reexec,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(sub.returncode, 0, msg=sub.stderr)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_reloader_watch_and_run_restarts_propagates_exit_and_reaps_sigign_child(self) -> None:
        import os
        import signal
        import subprocess
        import sys
        import time
        from spaceinvasion.reloader import _terminate_child, watch_and_run

        before_sig = signal.getsignal(signal.SIGTERM)
        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            watched = tp / "watched.py"
            watched.write_text("V = 1\n", encoding="utf-8")
            pids_log = tp / "pids.txt"
            trigger = tp / "exit_now.flag"
            ready1 = tp / "ready1.flag"

            child_code = (
                "import os, pathlib, sys, time; "
                f"log_p = pathlib.Path({str(pids_log)!r}); "
                f"trig_p = pathlib.Path({str(trigger)!r}); "
                f"r1_p = pathlib.Path({str(ready1)!r}); "
                "open(log_p, 'a', encoding='utf-8').write(f'{os.getpid()}\\n'); "
                "r1_p.write_text('1', encoding='utf-8'); "
                "time.sleep(0.05) if trig_p.exists() else None; "
                "sys.exit(7) if trig_p.exists() else time.sleep(30)"
            )

            def _edit_after_ready() -> None:
                deadline = time.monotonic() + 5.0
                while not ready1.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                time.sleep(0.08)
                trigger.write_text("go", encoding="utf-8")
                watched.write_text("V = 2\n", encoding="utf-8")

            editor = threading.Thread(target=_edit_after_ready, daemon=True)
            editor.start()
            rc = watch_and_run(
                label="TEST_RELOAD",
                cmd=[sys.executable, "-c", child_code],
                watch_paths=[watched],
                poll_interval=0.05,
                debounce_seconds=0.05,
            )
            editor.join(timeout=5.0)
            self.assertEqual(rc, 7)
            self.assertEqual(signal.getsignal(signal.SIGTERM), before_sig)
            pids = [int(x) for x in pids_log.read_text(encoding="utf-8").splitlines() if x.strip()]
            self.assertGreaterEqual(len(pids), 2)
            with self.assertRaises(OSError):
                os.kill(pids[0], 0)

            # _terminate_child SIGKILL fallback on SIGTERM-ignoring child with 0 ResourceWarning
            ready_ign = tp / "ready_ign.flag"
            ign_code = (
                "import signal, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"open({str(ready_ign)!r}, 'w', encoding='utf-8').write('1'); "
                "time.sleep(60)"
            )
            ign_proc = subprocess.Popen(
                [sys.executable, "-c", ign_code],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 5.0
                while not ready_ign.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(ready_ign.exists())
                _terminate_child(ign_proc)
                self.assertIsNotNone(ign_proc.poll())
            finally:
                if ign_proc.poll() is None:
                    ign_proc.kill()
                ign_proc.wait(timeout=5.0)

    def test_adv_huge_content_length_413_invalid_400_favicon_204_and_commander_10e5000_payload(self) -> None:
        import json
        import os
        import socket
        import subprocess
        import sys
        import urllib.request
        from unittest.mock import MagicMock
        import commander
        from spaceinvasion import game_data
        from spaceinvasion.api_server import GameApiBridge, MAX_REQUEST_BYTES, create_api_server
        from spaceinvasion.strategy import PlannedAction

        # GAP-M5-03: direct commander._action_to_command_payload 10**5000 boundary across all kinds
        for kind in (
            "CLAIM_TUTORIAL",
            "BUILD",
            "RESEARCH",
            "BUILD_SHIP",
            "THROTTLE_BUILDING",
            "ESCAPE_FLIGHT",
            "SCAN_GALAXY",
            "SPY",
            "ATTACK",
            "PLAN",
        ):
            act = PlannedAction(
                "P2",
                kind,
                10**5000,
                "01:001:01",
                "Target 01:001:02",
                "Reason",
                target_id=10**5000,
                target_level=10**5000,
            )
            payload = commander._action_to_command_payload(act, dry_run=True)
            encoded = json.dumps(payload, allow_nan=False)
            decoded = json.loads(encoded)
            for key in ("planet_id", "building_id", "research_id", "ship_id", "quantity", "percentage"):
                if key in decoded and decoded[key] is not None:
                    self.assertLessEqual(abs(decoded[key]), game_data.MAX_JSON_SAFE_INT)

        env_clean = os.environ.copy()
        env_clean["SPACEINVASION_NO_REEXEC"] = "1"
        sub = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, 'bin'); import commander; assert not any(m == 'selenium' or m.startswith('selenium.') for m in sys.modules)",
            ],
            env=env_clean,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(sub.returncode, 0, msg=sub.stderr)

        # GAP-M5-02 + /favicon.ico 204
        bridge = MagicMock(spec=GameApiBridge)
        bridge.log_dir = None
        srv = create_api_server(bridge, host="127.0.0.1", port=0)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        port = srv.server_address[1]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/favicon.ico", timeout=3.0) as fav_resp:
                self.assertEqual(fav_resp.status, 200)
                self.assertGreater(len(fav_resp.read()), 64)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/favicon.svg", timeout=3.0) as fav_svg:
                self.assertEqual(fav_svg.status, 200)
                self.assertIn(b"<svg", fav_svg.read())

            def _raw_post(hdr_len: str) -> tuple[bytes, dict]:
                with socket.create_connection(("127.0.0.1", port), timeout=3.0) as sock:
                    sock.sendall(
                        f"POST /command HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: {hdr_len}\r\nConnection: close\r\n\r\n".encode(
                            "ascii"
                        )
                    )
                    chunks: list[bytes] = []
                    while True:
                        buf = sock.recv(4096)
                        if not buf:
                            break
                        chunks.append(buf)
                raw = b"".join(chunks)
                head, _, body_b = raw.partition(b"\r\n\r\n")
                return head.split(b"\r\n", 1)[0], json.loads(body_b.decode("utf-8"))

            status_413, body_413 = _raw_post("9999999999999")
            self.assertIn(b"413", status_413)
            self.assertFalse(body_413["ok"])
            self.assertIn(str(MAX_REQUEST_BYTES), body_413["error"])

            for bad_len in ("9" * 5000, "-5", "abc"):
                status_400, body_400 = _raw_post(bad_len)
                self.assertIn(b"400", status_400)
                self.assertFalse(body_400["ok"])
                self.assertIn("Content-Length", body_400["error"])
        finally:
            srv.shutdown()
            srv.server_close()
            t.join(timeout=5.0)

    def test_r7_api_rejects_unicode_digits_bad_fleetsave_reserve_and_closes_bridge(self) -> None:
        import math
        import requests
        import commander
        from spaceinvasion.api_server import ApiRequestError, GameApiBridge, create_api_server
        from spaceinvasion.commander_client import GameApiClient
        from spaceinvasion.strategy import PlannedAction

        # GameApiClient timeout validation
        for bad_t in (float("inf"), float("nan"), -5.0, 0.0, None, True):
            with GameApiClient("http://127.0.0.1:8787", timeout=bad_t) as c_bad:  # type: ignore[arg-type]
                self.assertTrue(math.isfinite(c_bad.timeout))
                self.assertEqual(c_bad.timeout, 30.0)

        # commander waiting message sid= redaction
        wait_act = PlannedAction(
            priority="P2",
            kind="BUILD",
            planet_id=101,
            planet_coords="01:001:01",
            target_name="Mine ?sid=CMD_WAIT_SECRET",
            reason="Wait ?sid=CMD_WAIT_REASON",
            executable_now=False,
        )
        fake_api = MagicMock(spec=GameApiClient)
        fake_api.get_state.return_value = _sample_state()
        with patch("commander.plan_actions", return_value=[wait_act]):
            _, _, actual_res, _, _ = commander.run_commander_tick(fake_api, live=False, max_actions=1)
        self.assertIsNotNone(actual_res)
        self.assertNotIn("CMD_WAIT_SECRET", actual_res["message"])
        self.assertNotIn("CMD_WAIT_REASON", actual_res["message"])

        state = _sample_state()
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.read_full_snapshot.return_value = state
        fake_client.read_planets_overview.return_value = state

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=tp / "config.yaml",
                log_dir=tp / "logs",
                db_path=tp / "test.db",
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            port = server.server_address[1]
            t_srv = threading.Thread(target=server.serve_forever, daemon=True)
            t_srv.start()
            try:
                base = f"http://127.0.0.1:{port}"
                for bad_payload in (
                    {"command": "spy", "target_coords": "\u0660\u0662:\u0663\u0668\u0660:\u0660\u0664"},
                    {"command": "attack", "target_coords": "\u0660\u0662:\u0663\u0668\u0660:\u0660\u0664"},
                    {"command": "scan_galaxy", "galaxy": "\u0662", "system": 1},
                    {"command": "fleetsave_plan", "fuel_reserve_spice": "bad"},
                    {"command": "fleetsave_plan", "fuel_reserve_spice": -1},
                ):
                    r = requests.post(f"{base}/command", json=bad_payload, timeout=5)
                    self.assertEqual(r.status_code, 400, msg=f"payload={bad_payload!r} body={r.text}")
                    self.assertFalse(r.json()["ok"])
            finally:
                server.shutdown()
                server.server_close()
                t_srv.join(timeout=5.0)
                bridge.close()
                bridge.close()  # idempotent
            self.assertTrue(bridge._db_closed)

    def test_r8_client_keepalive_commander_action_redaction_and_api_float_huge_int_guards(self) -> None:
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import json
        import requests
        import commander
        from spaceinvasion.api_server import ApiRequestError, GameApiBridge, create_api_server
        from spaceinvasion.commander_client import GameApiClient, GameApiError
        from spaceinvasion.strategy import PlannedAction

        # 1. GameApiClient held-exception Keep-Alive HTTP/1.1 shutdown check
        class _ApiErrKeepAlive(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                body = json.dumps({"ok": False, "error": "boom"}, allow_nan=False).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.wfile.write(body)

        ka_srv = HTTPServer(("127.0.0.1", 0), _ApiErrKeepAlive)
        ka_thread = threading.Thread(target=ka_srv.serve_forever, daemon=True)
        ka_thread.start()
        held_api_exc: Exception | None = None
        try:
            try:
                with GameApiClient(base_url=f"http://127.0.0.1:{ka_srv.server_address[1]}") as cli:
                    cli.health()
                self.fail("Expected GameApiError")
            except GameApiError as exc:
                held_api_exc = exc

            sd_th = threading.Thread(target=ka_srv.shutdown, daemon=True)
            sd_th.start()
            sd_th.join(timeout=1.5)
            self.assertFalse(sd_th.is_alive(), "HTTPServer.shutdown() deadlocked while GameApiError retained")
            self.assertIsNotNone(held_api_exc)
        finally:
            held_api_exc = None
            ka_srv.shutdown()
            ka_srv.server_close()
            ka_thread.join(timeout=2.0)

        # 2. commander.run_commander_tick waiting fallback must redact action dict (incl. reason/action_href/target_name)
        wait_act = PlannedAction(
            priority="P2",
            kind="BUILD",
            planet_id=101,
            planet_coords="01:001:01",
            target_name="Mine ?sid=R8_WAIT_NAME",
            reason="Wait ?sid=R8_WAIT_REASON",
            action_href="indexInternal.es?action=internalBuildings&sid=R8_WAIT_HREF",
            executable_now=False,
        )
        fake_api = MagicMock(spec=GameApiClient)
        fake_api.get_state.return_value = _sample_state()
        with patch("commander.plan_actions", return_value=[wait_act]):
            _, _, actual_res, _, outcomes = commander.run_commander_tick(fake_api, live=False, max_actions=1)
        self.assertIsNotNone(actual_res)
        assert actual_res is not None
        serialized = json.dumps(actual_res, allow_nan=False)
        for secret_tok in ("R8_WAIT_NAME", "R8_WAIT_REASON", "R8_WAIT_HREF"):
            self.assertNotIn(secret_tok, serialized)
        self.assertIn("REDACTED", actual_res["action"]["reason"])
        self.assertIn("REDACTED", actual_res["action"]["action_href"])
        self.assertEqual(outcomes[0]["action"], actual_res["action"])

        # 3. API HTTP Arabic-Indic float 400 + direct handle_command 10**5000 ApiRequestError
        state = _sample_state()
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.read_full_snapshot.return_value = state
        fake_client.read_planets_overview.return_value = state

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=tp / "config.yaml",
                log_dir=tp / "logs",
                db_path=tp / "test.db",
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            port = server.server_address[1]
            t_srv = threading.Thread(target=server.serve_forever, daemon=True)
            t_srv.start()
            try:
                base = f"http://127.0.0.1:{port}"
                for bad_float_cmd in (
                    {"command": "update_config", "config": {"storage_warn_ratio": "\u0660.\u0665"}},
                    {"command": "simulate_combat", "attacker_ships": {"s4": 1}, "safety_margin": "\u0662"},
                ):
                    resp = requests.post(f"{base}/command", json=bad_float_cmd, timeout=5)
                    self.assertEqual(resp.status_code, 400, msg=resp.text)
                    self.assertFalse(resp.json()["ok"])

                for huge_cmd in (
                    {"command": 10**5000},
                    {"command": "plan", "planner_mode": 10**5000},
                    {"command": "navigate", "action": 10**5000},
                    {"command": "spy", "target_coords": 10**5000},
                    {"command": "attack", "target_coords": 10**5000},
                    {"command": "fleetsave_plan", "target_coords": 10**5000},
                ):
                    with self.assertRaises(ApiRequestError):
                        bridge.handle_command(huge_cmd)

                fs_ok = bridge.handle_command({"command": "fleetsave_plan", "target_coords": None})
                self.assertTrue(fs_ok["ok"])
            finally:
                server.shutdown()
                server.server_close()
                t_srv.join(timeout=5.0)
                bridge.close()

    def test_r9_api_playstyle_validation_bridge_wait_bounds_scan_coords_and_def_research(self) -> None:
        import requests
        from spaceinvasion.api_server import ApiRequestError

        base_p = _sample_state().planets[0]
        huge_coords_p = PlanetState(
            planet_id=base_p.planet_id,
            name=base_p.name,
            coords=("9" * 5000) + ":" + ("9" * 5000) + ":1",
            planet_type=base_p.planet_type,
            temperature=base_p.temperature,
            fields_used=base_p.fields_used,
            fields_max=base_p.fields_max,
            resources=base_p.resources,
            storage=base_p.storage,
            energy=base_p.energy,
            buildings=base_p.buildings,
        )
        zero_padded_p = PlanetState(
            planet_id=base_p.planet_id,
            name=base_p.name,
            coords=("0" * 5000) + "2:" + ("0" * 5000) + "380:1",
            planet_type=base_p.planet_type,
            temperature=base_p.temperature,
            fields_used=base_p.fields_used,
            fields_max=base_p.fields_max,
            resources=base_p.resources,
            storage=base_p.storage,
            energy=base_p.energy,
            buildings=base_p.buildings,
        )
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.read_full_snapshot.return_value = GameState(
            31, 1, "u", 1_700_000_000.0, (huge_coords_p,), active_planet_id=huge_coords_p.planet_id
        )
        fake_client.read_planets_overview.return_value = fake_client.read_full_snapshot.return_value

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=tp / "config.yaml",
                log_dir=tp / "logs",
                db_path=tp / "test.db",
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
                max_limiter_wait=10**400,  # type: ignore[arg-type]
                executor_max_limiter_wait=10**400,  # type: ignore[arg-type]
            )
            self.assertEqual(bridge._max_limiter_wait, 3600.0)
            self.assertEqual(bridge._executor_max_limiter_wait, 3600.0)
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            port = server.server_address[1]
            t_srv = threading.Thread(target=server.serve_forever, daemon=True)
            t_srv.start()
            try:
                base = f"http://127.0.0.1:{port}"
                for bad_ps in (10**5000, 123, True, None, "", "   "):
                    with self.assertRaises(ApiRequestError):
                        bridge.handle_command({"command": "update_config", "config": {"playstyle": bad_ps}})
                for bad_http_ps in (123, True, None, "", "   "):
                    r_ps = requests.post(
                        f"{base}/command",
                        json={"command": "update_config", "config": {"playstyle": bad_http_ps}},
                        timeout=5,
                    )
                    self.assertEqual(r_ps.status_code, 400)

                # scan_galaxy with >4300-digit coords and >4300-zero-padded coords falls back cleanly
                r_scan1 = requests.post(
                    f"{base}/command", json={"command": "scan_galaxy", "dry_run": True}, timeout=5
                )
                self.assertEqual(r_scan1.status_code, 200)
                self.assertIn("01:001", r_scan1.json()["action"]["target_name"])

                bridge._last_state = GameState(
                    31, 1, "u", 1_700_000_000.0, (zero_padded_p,), active_planet_id=zero_padded_p.planet_id
                )
                r_scan2 = requests.post(
                    f"{base}/command", json={"command": "scan_galaxy", "dry_run": True}, timeout=5
                )
                self.assertEqual(r_scan2.status_code, 200)
                self.assertIn("02:380", r_scan2.json()["action"]["target_name"])

                # simulate_combat honors defender_researches (+10%/lvl)
                r_sim0 = requests.post(
                    f"{base}/command",
                    json={
                        "command": "simulate_combat",
                        "attacker_ships": {"s4": 16},
                        "defender_ships": {"s4": 10},
                        "defender_researches": {"r8": 0, "r9": 0, "r10": 0},
                    },
                    timeout=5,
                )
                r_sim15 = requests.post(
                    f"{base}/command",
                    json={
                        "command": "simulate_combat",
                        "attacker_ships": {"s4": 16},
                        "defender_ships": {"s4": 10},
                        "defender_researches": {"r8": 15, "r9": 15, "r10": 15},
                    },
                    timeout=5,
                )
                self.assertEqual(r_sim0.status_code, 200)
                self.assertEqual(r_sim15.status_code, 200)
                s0 = r_sim0.json()["simulation"]
                s15 = r_sim15.json()["simulation"]
                self.assertTrue(s0["safe_to_attack"])
                self.assertEqual(s15["defender_power"], 7375.0)
                self.assertFalse(s15["safe_to_attack"])
            finally:
                server.shutdown()
                server.server_close()
                t_srv.join(timeout=5.0)
                bridge.close()

    def test_r10_bridge_ports_budgets_health_control_redaction_and_retry_after(self) -> None:
        import ast
        import tempfile
        import threading
        import requests
        from pathlib import Path
        from spaceinvasion.api_server import ApiRequestError, GameApiBridge, create_api_server
        from spaceinvasion.client import RateLimitError
        import commander

        # Verify commander.py and spaceinvasion/runtime/commander_client.py remain 100% selenium-free
        for fname in ("bin/commander.py", "spaceinvasion/runtime/commander_client.py"):
            tree = ast.parse(Path(fname).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse(any("selenium" in a.name for a in node.names), fname)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn("selenium", node.module, fname)

        self.assertEqual(commander.compute_rate_limit_backoff("retry after \u0669\u0660s"), 45.0)
        self.assertIsNone(commander.compute_rate_limit_backoff("\u0669\u0660s"))

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            b_zero = GameApiBridge(
                debug_port=0,
                max_actions_per_hour=0,
                log_dir=None,
                config_path=tp / "cfg0.yaml",
            )
            b_huge = GameApiBridge(
                debug_port=10**5000,
                max_actions_per_hour=10**5000,
                log_dir=None,
                config_path=tp / "cfg1.yaml",
            )
            try:
                self.assertEqual(b_zero.debug_port, 0)
                h0 = b_zero.health()
                self.assertEqual(h0["debug_port"], 0)
                self.assertEqual(h0["remaining_hourly_actions"], 0)
                self.assertEqual(b_huge.debug_port, 65535)
                self.assertLessEqual(b_huge.health()["remaining_hourly_actions"], 1_000_000)

                b_zero._last_url = "https://uni/?sid=HEALTH_SECRET_1"
                b_zero._bot_control["password"] = b"HEALTH_PW_2"
                b_zero._scanned_systems["01:001"] = float("nan")
                b_zero._throttle_timestamps[10**5000] = float("inf")
                h_sec = b_zero.health()
                c_sec = b_zero.get_bot_control()
                cmd_ctrl = b_zero.handle_command({"command": "bot_control"})
                for payload in (h_sec, c_sec, cmd_ctrl):
                    dumped = json.dumps(payload, allow_nan=False)
                    self.assertNotIn("HEALTH_SECRET_1", dumped)
                    self.assertNotIn("HEALTH_PW_2", dumped)
            finally:
                b_zero.close()
                b_huge.close()

            class _RateLimitedClient:
                def __init__(self, _port: int) -> None:
                    self.driver = type("_Drv", (), {"current_url": "https://uni/?sid=RL_URL_SEC"})()

                def __enter__(self) -> "_RateLimitedClient":
                    return self

                def __exit__(self, *_a: object) -> None:
                    return None

                def ensure_in_game(self, **_kw: object) -> None:
                    err = RateLimitError("throttle ?sid=RL_ERR_SEC", retry_after=10)
                    err.retry_after = 10**5000  # type: ignore[assignment]
                    raise err

                def read_full_snapshot(self, **_kw: object) -> object:
                    err = RateLimitError("throttle ?sid=RL_ERR_SEC", retry_after=10)
                    err.retry_after = 10**5000  # type: ignore[assignment]
                    raise err

            b_rl = GameApiBridge(
                debug_port=0,
                log_dir=None,
                config_path=tp / "cfgrl.yaml",
                client_factory=_RateLimitedClient,
                ensure_session_fn=lambda: None,
            )
            srv = create_api_server(b_rl, host="127.0.0.1", port=0)
            t_srv = threading.Thread(target=srv.serve_forever, daemon=True)
            t_srv.start()
            try:
                base = f"http://127.0.0.1:{srv.server_port}"
                r429 = requests.post(f"{base}/command", json={"command": "plan"}, timeout=5)
                self.assertEqual(r429.status_code, 429)
                self.assertEqual(r429.headers.get("Retry-After"), "3600")
                body = r429.json()
                self.assertEqual(body["retry_after"], 3600.0)
                self.assertNotIn("RL_ERR_SEC", json.dumps(body, allow_nan=False))
            finally:
                srv.shutdown()
                srv.server_close()
                t_srv.join(timeout=5.0)
                b_rl.close()

    def test_r11_api_redaction_unknown_scalars_frozenset_validators_build_eligibility_and_commander_asteroid_cap(
        self,
    ) -> None:
        from types import MappingProxyType
        import requests
        from spaceinvasion.strategy import PlannedAction
        from spaceinvasion.api_server import (
            ApiRequestError,
            _parse_command_int,
            _sanitize_finite_json,
            _validate_strict_float,
            _validate_strict_int,
        )

        class _BytesPath:
            def __fspath__(self) -> bytes:
                return b"https://x/?sid=API_BPATH_SEC"

        for bad_val in (frozenset({1}), MappingProxyType({"a": 1})):
            with self.assertRaises(ApiRequestError):
                _parse_command_int("planet_id", bad_val)
            with self.assertRaises(ApiRequestError):
                _validate_strict_int("ga_generations", bad_val)
            with self.assertRaises(ApiRequestError):
                _validate_strict_float("ga_mutation_rate", bad_val)

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            fake_cli = MagicMock()
            fake_cli.__enter__.return_value = fake_cli
            fake_cli.driver = type("_Drv", (), {"current_url": "https://u/?sid=API_URL_SEC"})()
            fake_cli.read_full_snapshot.return_value = _sample_state()
            bridge = GameApiBridge(
                debug_port=0,
                log_dir=None,
                config_path=tp / "cfg.yaml",
                client_factory=lambda _port: fake_cli,
                ensure_session_fn=lambda: None,
            )
            try:
                # 1. PathLike, BytesPath, RuntimeError, complex, object() across health, bot_control, dashboard, handle_command, Handler._send_json
                bridge._last_url = Path("https://u/?sid=API_PATH_SEC")  # type: ignore[assignment]
                bridge._bot_control["secret_path"] = _BytesPath()
                bridge._bot_control["err"] = RuntimeError("boom ?sid=API_EXC_SEC")
                bridge._bot_control["cplx"] = complex(1, 2)
                bridge._bot_control["obj"] = object()
                bridge._last_state = _sample_state()

                san = _sanitize_finite_json(
                    {
                        "p": Path("https://u/?sid=DIRECT_PATH_SEC"),
                        "bp": _BytesPath(),
                        "err": RuntimeError("?sid=DIRECT_EXC_SEC"),
                        "cplx": complex(3, 4),
                        "obj": object(),
                        (Path("?sid=KEY_PATH_SEC"),): 1,
                        frozenset({Path("?sid=KEY_FSET_SEC")}): 2,
                    }
                )
                dumped_san = json.dumps(san, allow_nan=False)
                for sec in (
                    "API_BPATH_SEC",
                    "DIRECT_PATH_SEC",
                    "DIRECT_EXC_SEC",
                    "KEY_PATH_SEC",
                    "KEY_FSET_SEC",
                ):
                    self.assertNotIn(sec, dumped_san)

                for payload in (
                    bridge.health(),
                    bridge.get_bot_control(),
                    bridge.dashboard(refresh=False),
                    bridge.handle_command({"command": "claim_tutorial"}),
                    bridge.handle_command({"command": "ensure_session"}),
                    bridge.handle_command(
                        {
                            "command": "simulate_combat",
                            "attacker_ships": {"s4": 10},
                            "defender_ships": {"s4": 5},
                        }
                    ),
                ):
                    dumped = json.dumps(payload, allow_nan=False)
                    for sec in ("API_PATH_SEC", "API_BPATH_SEC", "API_EXC_SEC"):
                        self.assertNotIn(sec, dumped)

                srv = create_api_server(bridge, host="127.0.0.1", port=0)
                t_srv = threading.Thread(target=srv.serve_forever, daemon=True)
                t_srv.start()
                try:
                    base = f"http://127.0.0.1:{srv.server_port}"
                    for path in ("/health", "/bot_status", "/dashboard"):
                        resp = requests.get(f"{base}{path}", timeout=5)
                        self.assertEqual(resp.status_code, 200)
                        for sec in ("API_PATH_SEC", "API_BPATH_SEC", "API_EXC_SEC"):
                            self.assertNotIn(sec, resp.text)
                finally:
                    srv.shutdown()
                    srv.server_close()
                    t_srv.join(timeout=5.0)

                # 2. _find_build_action enforces REQ-BLD-003 across all 3 branches (offer is None, mismatch, offer is not None)
                ast_p = PlanetState(
                    planet_id=901,
                    name="Ast",
                    coords="01:001:02",
                    planet_type=" Asteroid ",
                    temperature=10,
                    fields_used=9,
                    fields_max=20,
                    resources=Resources(500_000, 500_000, 500_000, 500_000),
                    storage=Resources(500_000, 500_000, 500_000, 500_000),
                    energy=EnergyBalance(50, 10),
                    buildings={"b7": 8, "b19": 1},
                    building_offers=(
                        BuildingOffer(1, "Pig iron mine", 5, 1, Resources(100, 50, 0, 0), False, "Not enough", "?b=1"),
                        BuildingOffer(5, "Fusion plant", 0, 1, Resources(100, 50, 0, 0), True, "Build", "?b=5"),
                        BuildingOffer(19, "Galaxy scanner", 1, 2, Resources(20_000, 80_000, 40_000, 16_000), True, "Build", "?b=19"),
                    ),
                )
                raid_p = PlanetState(
                    planet_id=902,
                    name="Raid",
                    coords="01:001:03",
                    planet_type=" Raid ",
                    temperature=20,
                    fields_used=4,
                    fields_max=20,
                    resources=Resources(500_000, 500_000, 500_000, 500_000),
                    storage=Resources(500_000, 500_000, 500_000, 500_000),
                    energy=EnergyBalance(50, 10),
                    buildings={"b7": 3},
                    building_offers=(
                        BuildingOffer(1, "Pig iron mine", 0, 1, Resources(78, 0, 25, 0), True, "Build", "?b=1"),
                    ),
                )
                norm_p = PlanetState(
                    planet_id=903,
                    name="Norm",
                    coords="01:001:04",
                    planet_type="normal",
                    temperature=20,
                    fields_used=10,
                    fields_max=200,
                    resources=Resources(500_000, 500_000, 500_000, 500_000),
                    storage=Resources(500_000, 500_000, 500_000, 500_000),
                    energy=EnergyBalance(50, 10),
                    buildings={"b7": 8},
                    building_offers=(
                        BuildingOffer(19, "Galaxy scanner", 0, 1, Resources(10_000, 40_000, 20_000, 8_000), True, "Build", "?b=19"),
                    ),
                )
                norm_p2 = PlanetState(
                    planet_id=906,
                    name="NormCross",
                    coords="01:001:07",
                    planet_type="normal",
                    temperature=20,
                    fields_used=10,
                    fields_max=200,
                    resources=Resources(500_000, 500_000, 500_000, 500_000),
                    storage=Resources(500_000, 500_000, 500_000, 500_000),
                    energy=EnergyBalance(50, 10),
                    buildings={"b7": 8},
                )
                st_bld = GameState(31, 1, "u", 1000.0, (ast_p, raid_p, norm_p, norm_p2), active_planet_id=903)

                # Forbidden: offer is None with can_fallback=True (b2 on ast, b8 on ast without offer, b2 on raid, b19 on norm cross-planet)
                for bid_chk, pid_chk in ((2, 901), (8, 901), (2, 902), (19, 906)):
                    act_f = bridge._find_build_action(st_bld, bid_chk, pid_chk, allow_live_verify=True)
                    self.assertFalse(act_f.executable_now, f"Expected forbidden b{bid_chk} on pid {pid_chk}")
                    self.assertIsNone(act_f.action_href)
                    self.assertIn("not allowed", act_f.reason)
                # Forbidden: target_level != nxt_lvl mismatch + "not enough" override (b1 on ast)
                act_mismatch = bridge._find_build_action(st_bld, 1, 901, allow_live_verify=True)
                self.assertFalse(act_mismatch.executable_now)
                self.assertIsNone(act_mismatch.action_href)
                # Forbidden: live offer present (b5 on ast, b1 on raid, b19 on norm)
                for bid_chk, pid_chk in ((5, 901), (1, 902), (19, 903)):
                    act_f2 = bridge._find_build_action(st_bld, bid_chk, pid_chk, allow_live_verify=True)
                    self.assertFalse(act_f2.executable_now, f"Expected forbidden live offer b{bid_chk} on pid {pid_chk}")
                    self.assertIsNone(act_f2.action_href)
                    self.assertIn("not allowed", act_f2.reason)
                # Allowed: b7 (offer is None) & b19 (offer present) on ast, and b8 on ast when b8 offer present
                self.assertTrue(bridge._find_build_action(st_bld, 7, 901, allow_live_verify=True).executable_now)
                act_b19_ok = bridge._find_build_action(st_bld, 19, 901, allow_live_verify=True)
                self.assertTrue(act_b19_ok.executable_now)
                self.assertEqual(act_b19_ok.action_href, "?b=19")
                ast_with_b8 = PlanetState(
                    planet_id=904,
                    name="AstB8",
                    coords="01:001:05",
                    planet_type=" Asteroid ",
                    temperature=10,
                    fields_used=2,
                    fields_max=20,
                    resources=Resources(500_000, 500_000, 500_000, 500_000),
                    storage=Resources(500_000, 500_000, 500_000, 500_000),
                    energy=EnergyBalance(50, 10),
                    buildings={"b7": 1},
                    building_offers=(
                        BuildingOffer(8, "Research lab", 0, 1, Resources(200, 400, 200, 0), True, "Build", "?b=8"),
                    ),
                )
                st_b8 = GameState(31, 1, "u", 1000.0, (ast_with_b8,), active_planet_id=904)
                self.assertTrue(bridge._find_build_action(st_b8, 8, 904, allow_live_verify=True).executable_now)

                # 3. Commander treats " Asteroid " as queue_cap == 1 (does not re-fetch buildings when 1/1 queued)
                ast_cmd_p = PlanetState(
                    planet_id=905,
                    name="AstCmd",
                    coords="01:001:06",
                    planet_type=" Asteroid ",
                    temperature=10,
                    fields_used=9,
                    fields_max=20,
                    resources=Resources(500_000, 500_000, 500_000, 500_000),
                    storage=Resources(500_000, 500_000, 500_000, 500_000),
                    energy=EnergyBalance(50, 10),
                    buildings={"b7": 8, "b19": 1},
                )
                st_cmd = GameState(
                    31,
                    1,
                    "u",
                    1000.0,
                    (ast_cmd_p,),
                    active_planet_id=905,
                    specs={"construction_commander": 1},
                    active_queues=(QueueJob("building", "Galaxy scanner", building_id=19, target_level=2),),
                )
                wait_b19 = PlannedAction(
                    priority="P2",
                    kind="WAIT_RESOURCES",
                    planet_id=905,
                    planet_coords="01:001:06",
                    target_id=19,
                    target_name="Galaxy scanner",
                    target_level=2,
                    cost=Resources(20_000, 80_000, 40_000, 16_000),
                    missing=Resources(),
                    reason="Upgrade Galaxy scanner to L2",
                    executable_now=False,
                )
                fake_api = MagicMock(spec=GameApiClient)
                fake_api.get_state.return_value = st_cmd
                with patch("commander.plan_actions", return_value=[wait_b19]):
                    commander.run_commander_tick(
                        fake_api,
                        live=False,
                        max_actions=1,
                        include_buildings=False,
                    )
                fake_api.get_state.assert_called_once_with(include_buildings=False)
            finally:
                bridge.close()

    def test_r12_http_error_resilience_huge_exc_args_full_secret_redaction_and_dashboard_speed(self) -> None:
        import time
        import requests
        from spaceinvasion.api_server import (
            _redact_api_payload,
            _sanitize_finite_json,
        )
        from spaceinvasion.client import RateLimitError

        class _RaiseFspath:
            def __fspath__(self) -> str:
                raise RuntimeError("fspath boom ?password=API_FS_PW")

        class _IntPath:
            def __fspath__(self) -> int:
                return 10**5000

        class _RaiseStrRepr:
            def __str__(self) -> str:
                raise RuntimeError("str boom")

            def __repr__(self) -> str:
                raise RuntimeError("repr boom")

        exc_red = _redact_api_payload(RuntimeError(10**5000, "?sid=API_EXC_RET&password=API_EXC_PW"))
        self.assertIsInstance(exc_red, str)
        self.assertNotIn("API_EXC_RET", exc_red)
        self.assertNotIn("API_EXC_PW", exc_red)

        deep_key: object = Path("?password=API_DP5")
        for _ in range(5):
            deep_key = (deep_key,)
        san = _sanitize_finite_json(
            {
                "auth_header": "Authorization: Bearer API_BEARER_SEC",
                "json_blob": '{"password": "API_JSON_PW", "api_key": "API_JSON_KEY"}',
                "authorization": "API_DICT_AUTH",
                "long_s": "\u017fid=API_LONGS_SEC",
                "bad_fs": _RaiseFspath(),
                "int_fs": _IntPath(),
                "bad_repr": _RaiseStrRepr(),
                deep_key: "ok",
            }
        )
        dumped_san = json.dumps(san, allow_nan=False)
        for sec in (
            "API_FS_PW",
            "API_DP5",
            "API_BEARER_SEC",
            "API_JSON_PW",
            "API_JSON_KEY",
            "API_DICT_AUTH",
            "API_LONGS_SEC",
        ):
            self.assertNotIn(sec, dumped_san)
        self.assertIn("authorization: bearer redacted", dumped_san.lower())

        for bad in (RuntimeError(10**5000, "Authorization: Bearer CMD_SEC"), _RaiseStrRepr(), _RaiseFspath()):
            cmd_txt = commander._safe_cmd_str(bad)
            self.assertIsInstance(cmd_txt, str)
            self.assertNotIn("CMD_SEC", cmd_txt)

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            bridge = GameApiBridge(
                debug_port=0,
                log_dir=None,
                config_path=tp / "cfg.yaml",
                ensure_session_fn=lambda: None,
            )
            try:
                planets_5 = tuple(
                    PlanetState(
                        planet_id=100 + i,
                        name=f"P{i}",
                        coords=f"02:380:0{i}",
                        planet_type="normal",
                        temperature=30,
                        fields_used=20,
                        fields_max=200,
                        resources=Resources(5000, 3000, 4000, 1000),
                        storage=Resources(100_000, 100_000, 100_000, 100_000),
                        energy=EnergyBalance(500, 200),
                        buildings={"b1": 8, "b2": 6, "b3": 8, "b4": 5, "b5": 10},
                    )
                    for i in range(1, 6)
                )
                bridge._last_state = GameState(31, 1, "u", 1000.0, planets_5, active_planet_id=101)
                _ = bridge.dashboard(refresh=False)
                t0 = time.perf_counter()
                dash_5 = bridge.dashboard(refresh=False)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                self.assertEqual(len(dash_5["state"]["planets"]), 5)
                self.assertLess(elapsed_ms, 25.0)

                srv = create_api_server(bridge, host="127.0.0.1", port=0)
                t_srv = threading.Thread(target=srv.serve_forever, daemon=True)
                t_srv.start()
                try:
                    base = f"http://127.0.0.1:{srv.server_port}"
                    with patch.object(
                        bridge,
                        "dashboard",
                        side_effect=RuntimeError(10**5000, "Authorization: Bearer HTTP_GET_SEC ?password=HTTP_GET_PW"),
                    ):
                        r_get = requests.get(f"{base}/dashboard", timeout=5)
                    self.assertEqual(r_get.status_code, 500)
                    r_get.json()
                    self.assertNotIn("HTTP_GET_SEC", r_get.text)
                    self.assertNotIn("HTTP_GET_PW", r_get.text)
                    self.assertIn("authorization: bearer redacted", r_get.text.lower())

                    with patch.object(
                        bridge,
                        "handle_command",
                        side_effect=RuntimeError(10**5000, "Authorization: Bearer HTTP_POST_SEC ?password=HTTP_POST_PW"),
                    ):
                        r_post = requests.post(f"{base}/command", json={"command": "plan"}, timeout=5)
                    self.assertEqual(r_post.status_code, 500)
                    r_post.json()
                    self.assertNotIn("HTTP_POST_SEC", r_post.text)
                    self.assertNotIn("HTTP_POST_PW", r_post.text)
                    self.assertIn("authorization: bearer redacted", r_post.text.lower())

                    rl_err = RateLimitError("Authorization: Bearer RL_BEAR_SEC", retry_after=10)
                    rl_err.retry_after = 10**5000  # type: ignore[assignment]
                    with patch.object(bridge, "handle_command", side_effect=rl_err):
                        r_429 = requests.post(f"{base}/command", json={"command": "plan"}, timeout=5)
                    self.assertEqual(r_429.status_code, 429)
                    self.assertNotIn("RL_BEAR_SEC", r_429.text)
                    self.assertIn("authorization: bearer redacted", r_429.text.lower())
                finally:
                    srv.shutdown()
                    srv.server_close()
                    t_srv.join(timeout=5.0)
            finally:
                bridge.close()

    def test_r13_client_redaction_pathlike_compound_secrets_and_commander_nested_exc(self) -> None:
        import os
        import subprocess
        import sys
        from types import SimpleNamespace
        from spaceinvasion import api_server as api_mod
        from spaceinvasion import commander_client as cc

        class _RaiseFspath:
            def __fspath__(self) -> str:
                raise RuntimeError("fspath boom ?password=CC_FS_PW")

        class _SelfPath:
            def __fspath__(self) -> "_SelfPath":
                return self

        self.assertNotIn("CC_PATH_SID", cc._safe_client_text(Path("/tmp/?sid=CC_PATH_SID")))
        self.assertEqual(cc._safe_client_text(_RaiseFspath(), "fallback"), "fallback")
        self.assertEqual(cc._safe_client_text(_SelfPath(), "fallback"), "fallback")

        exc_300 = RuntimeError(10**5000, {"client_secret": "CC_300_" + ("X" * 300)})
        out_cc_exc = cc._safe_client_text(exc_300)
        self.assertNotIn("CC_300_", out_cc_exc)

        exc_1100 = RuntimeError(10**5000, {"password": "CMD_1100_" + ("X" * 1100)})
        out_cmd_exc = commander._safe_cmd_str(exc_1100)
        self.assertNotIn("CMD_1100_", out_cmd_exc)
        self.assertIn("REDACTED", out_cmd_exc)

        cyc = RuntimeError("cyc")
        cyc.args = (10**5000, cyc)
        self.assertIsInstance(cc._safe_client_text(cyc), str)
        self.assertIsInstance(commander._safe_cmd_str(cyc), str)
        self.assertIsInstance(api_mod._format_api_exception(cyc), str)

        with cc.GameApiClient() as cli:
            non_json_body = ("A" * 195) + '?sid=DECODE_TAIL_SID&client_secret=DECODE_CSEC&password=DECODE_PW'
            with self.assertRaises(cc.GameApiError) as ctx_500:
                cli._decode(
                    SimpleNamespace(
                        status_code=500,
                        text=non_json_body,
                        json=lambda: (_ for _ in ()).throw(ValueError("not json")),
                    )
                )
            for sec in ("DECODE_TAIL_SID", "DECODE_CSEC", "DECODE_PW"):
                self.assertNotIn(sec, str(ctx_500.exception))

            with self.assertRaises(cc.GameApiError) as ctx_400:
                cli._decode(
                    SimpleNamespace(
                        status_code=400,
                        text="",
                        json=lambda: {
                            "ok": False,
                            "error": 'Auth fail ?sid=JSON_ERR_SID&user_password=JSON_ERR_UPW Authorization: Bearer "JSON_Q_BEAR"',
                        },
                    )
                )
            for sec in ("JSON_ERR_SID", "JSON_ERR_UPW", "JSON_Q_BEAR"):
                self.assertNotIn(sec, str(ctx_400.exception))
            self.assertIn("REDACTED", str(ctx_400.exception))

        # Selenium-free import DAG invariant across commander, commander_client, logging_utils
        env = dict(os.environ)
        env["SPACEINVASION_NO_REEXEC"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, 'bin'); import commander, spaceinvasion.commander_client, spaceinvasion.logging_utils; "
                "assert not any(m == 'selenium' or m.startswith('selenium.') for m in sys.modules)",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

        # api_server compound, Unicode, quoted/bracketed Bearer + zero false positives
        self.assertEqual(api_mod._scrub_api_str('Authorization: Bearer "SECRET"'), 'Authorization: Bearer "REDACTED"')
        self.assertEqual(api_mod._scrub_api_str("Authorization: Bearer [SECRET]"), "Authorization: Bearer [REDACTED]")
        self.assertEqual(api_mod._scrub_api_str("Bearer <SECRET>"), "Bearer <REDACTED>")
        self.assertEqual(
            api_mod._scrub_api_str("?s\u0130d=TURK_SID&?cook\u0130e=TURK_CK&?ap\u0131_key=TURK_AK"),
            "?s\u0130d=REDACTED&?cook\u0130e=REDACTED&?ap\u0131_key=REDACTED",
        )
        benign = "has_sid=false&has_password=true&no_secret=false&cookie_path=/tmp/c&token=abc&bsid=123"
        self.assertEqual(api_mod._scrub_api_str(benign), benign)

        red_payload = api_mod._redact_api_payload(
            {
                "client_secret": "API_CSEC",
                "user_password": "API_UPW",
                "bot_api_key": "API_BKEY",
                "session_cookie": "API_SCK",
                "session_sid": "API_SSID",
                "X-API-Key": "API_XAPI",
                "has_sid": False,
                "cookie_path": "/tmp/cookies.json",
                "token": "benign_tok",
            }
        )
        self.assertEqual(red_payload["has_sid"], False)
        self.assertEqual(red_payload["cookie_path"], "/tmp/cookies.json")
        self.assertEqual(red_payload["token"], "benign_tok")
        dumped_payload = json.dumps(red_payload, allow_nan=False)
        for sec in ("API_CSEC", "API_UPW", "API_BKEY", "API_SCK", "API_SSID", "API_XAPI"):
            self.assertNotIn(sec, dumped_payload)

    def test_r14_api_raid_throttle_stale_dry_run_lazy_offline_spio_and_cycles(self) -> None:
        import time
        from pathlib import Path
        from unittest.mock import patch
        from spaceinvasion import api_server as api_mod
        from spaceinvasion.auth import SpaceInvasionAuthError
        from spaceinvasion.client import RateLimitError
        from spaceinvasion.state import (
            BuildingOffer,
            EnergyBalance,
            GameState,
            PlanetState,
            ResearchOffer,
            Resources,
            UnitOffer,
        )

        # 1. api_server cycle/dag20/redaction/NaN/PathLike hardening
        cyc: list[object] = []
        cyc.append(cyc)
        self.assertEqual(api_mod._sanitize_finite_json(cyc), ["[Cycle]"])
        self.assertEqual(api_mod._sanitize_finite_json(float("nan")), 0.0)

        class _BoomPath:
            def __fspath__(self) -> str:
                raise RuntimeError("boom path")

        self.assertEqual(api_mod._sanitize_finite_json(_BoomPath()), "<unprintable>")

        dag_exc: BaseException = RuntimeError("Bearer API_DAG_SEC")
        for _ in range(20):
            dag_exc = RuntimeError(dag_exc, dag_exc, dag_exc, dag_exc)
        t0 = time.perf_counter()
        formatted = api_mod._format_api_exception(dag_exc)
        self.assertNotIn("API_DAG_SEC", formatted)
        self.assertLess((time.perf_counter() - t0) * 1000.0, 50.0)
        self.assertNotIn("API_UE_PW", api_mod._scrub_api_str('%22password%22%3A%22API_UE_PW%22'))
        self.assertNotIn("API_ESC_PW", api_mod._scrub_api_str('\\"password\\":\\"API_ESC_PW\\"'))

        # 2. _find_research_action / _find_ship_action reject raid + unknown ship; _find_build_action rejects stale offer even in live verify
        raid_p = PlanetState(
            planet_id=920,
            name="Raid",
            coords="02:380:20",
            planet_type="raid",
            temperature=10,
            fields_used=0,
            fields_max=50,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b8": 5, "b9": 5},
            ship_offers=(UnitOffer("ship", 4, "Small fighter", 0, Resources(3000, 1000, 0, 0), True, "ship[4]"),),
        )
        norm_p = PlanetState(
            planet_id=921,
            name="Norm",
            coords="02:380:21",
            planet_type="normal",
            temperature=25,
            fields_used=10,
            fields_max=200,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 4},
            building_offers=(BuildingOffer(1, "Pig iron mine", 5, 6, Resources(100, 100, 0, 0), True, "Build", "?b=1"),),
            ship_offers=(UnitOffer("ship", 4, "Small fighter", 0, Resources(3000, 1000, 0, 0), True, "ship[4]"),),
        )
        ast_p = PlanetState(
            planet_id=922,
            name="Ast",
            coords="02:380:22",
            planet_type="asteroid",
            temperature=10,
            fields_used=1,
            fields_max=20,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 1, "b7": 1},
            building_offers=(BuildingOffer(8, "Research lab", 0, 1, Resources(500, 500, 500, 0), True, "Build", "?b=8"),),
        )
        st = GameState(
            31,
            1,
            "u",
            time.time(),
            (raid_p, norm_p, ast_p),
            active_planet_id=921,
            research_offers=(ResearchOffer(2, "r2", "Energy technology", 0, 1, Resources(500, 500, 0, 0), True, "", "?r=2"),),
        )

        with tempfile.TemporaryDirectory() as tmp:
            def _fail_factory(_port: int) -> object:
                raise AssertionError("should not open browser")

            bridge = api_mod.GameApiBridge(
                db_path=Path(tmp) / "bridge.db",
                config_path=Path(tmp) / "missing_cfg.yaml",
                log_dir=Path(tmp) / "logs",
                client_factory=_fail_factory,
                ensure_session_fn=lambda: None,
            )
            try:
                bridge._record_state(
                    st,
                    fetched_buildings=True,
                    fetched_research=True,
                    fetched_shipyard=True,
                    fetched_home=True,
                )

                r_act = bridge._find_research_action(st, 2, 920)
                self.assertFalse(r_act.executable_now)
                s_raid = bridge._find_ship_action(st, 4, 1, 920)
                self.assertFalse(s_raid.executable_now)
                s_unk = bridge._find_ship_action(st, 999, 1, 921)
                self.assertFalse(s_unk.executable_now)

                stale_b = bridge._find_build_action(st, 1, 921, allow_live_verify=False)
                self.assertFalse(stale_b.executable_now)
                self.assertIsNone(stale_b.action_href)
                # Explicit b7=1 asteroid BuildingOffer(8) target_level==nxt_lvl remains allowed in live verify
                ast_b8 = bridge._find_build_action(st, 8, 922, allow_live_verify=True)
                self.assertTrue(ast_b8.executable_now)

                # 3. _sync_spio_if_unread skips get_client when unread==0, ignores SpaceInvasionAuthError, preserves RateLimitError
                calls = 0

                def _boom_auth() -> object:
                    nonlocal calls
                    calls += 1
                    raise SpaceInvasionAuthError("offline")

                self.assertEqual(bridge._sync_spio_if_unread(None, st, get_client=_boom_auth, ignore_auth_error=True), 0)
                self.assertEqual(calls, 0)

                st_spio = GameState(
                    31,
                    1,
                    "u",
                    time.time(),
                    (norm_p,),
                    active_planet_id=921,
                    unread_messages={"spio": 2},
                )
                bridge._last_spio_sync_at = 0.0
                self.assertEqual(bridge._sync_spio_if_unread(None, st_spio, get_client=_boom_auth, ignore_auth_error=True), 0)
                self.assertEqual(calls, 1)

                def _boom_rate() -> object:
                    raise RateLimitError("slow down", retry_after=12.0)

                bridge._last_spio_sync_at = 0.0
                with self.assertRaises(RateLimitError):
                    bridge._sync_spio_if_unread(None, st_spio, get_client=_boom_rate, ignore_auth_error=True)

                # 4. Cached plan + throttle rejection without constructing browser client
                plan_res = bridge.handle_command({"command": "plan", "force_refresh": False})
                self.assertTrue(plan_res["ok"])
                for bad_thr in (
                    {"command": "throttle_building", "planet_id": 920, "building_id": 1, "percent": 50},
                    {"command": "throttle_building", "planet_id": 922, "building_id": 1, "percent": 50},
                    {"command": "throttle_building", "planet_id": 921, "building_id": 9, "percent": 50},
                ):
                    with self.assertRaises(ValueError):
                        bridge.handle_command(bad_thr)
            finally:
                bridge.close()

    def test_sync_economy_command_loop_metal_check_and_home_queue_building_id(self) -> None:
        from pathlib import Path
        from unittest.mock import MagicMock
        import commander
        from spaceinvasion import api_server as api_mod
        from spaceinvasion.state import (
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
            parse_home_metadata,
            parse_ticker_rates,
        )

        # 1. parse_home_metadata extracts building_id & target_level; parse_ticker_rates reads #metall
        home_html = """
        <form id="ress" style="display:none;">
          <input type="hidden" id="roheisen" value="29.13" />
          <input type="hidden" id="metall" value="0" />
        </form>
        <script>SI_TPL={planetID:12476};</script>
        <div id="sp-own-building">Fusion plant (Level 19)</div>
        """
        pid, _, _, queues, _ = parse_home_metadata(home_html)
        self.assertEqual(pid, 12476)
        self.assertEqual(len(queues), 1)
        self.assertEqual(queues[0].building_id, 5)
        self.assertEqual(queues[0].target_level, 19)
        ticker = parse_ticker_rates(home_html)
        self.assertEqual(ticker["metal"], 0.0)
        self.assertAlmostEqual(ticker["pig_iron"], 29.13)

        # 2. sync_economy API command & commander loop check
        econ_html = """
        <form name="economy" action="" method="POST">
          <select name="resource1"><option value="100" selected>100%</option></select>
          <select name="resource2"><option value="0" selected>0%</option></select>
          <select name="resource5"><option value="100" selected>100%</option></select>
          <input type="submit" value="Calculate" />
        </form>
        """
        p_col = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=50,
            fields_max=230,
            resources=Resources(50_000, 22_925, 100_000, 40_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(3000, 2000),
            buildings={"b1": 18, "b2": 18, "b5": 20},
        )
        st_init = GameState(31, 1, "u", 1000.0, (p_col,), active_planet_id=12476, speedup_factor=20.0)
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.__exit__.return_value = False
        fake_client.navigate_menu.return_value = econ_html
        fake_client.read_full_snapshot.return_value = st_init

        with tempfile.TemporaryDirectory() as tmp:
            bridge = api_mod.GameApiBridge(
                db_path=Path(tmp) / "sync.db",
                config_path=Path(tmp) / "cfg.yaml",
                log_dir=Path(tmp) / "logs",
                client_factory=lambda _p: fake_client,
                ensure_session_fn=lambda: None,
            )
            try:
                bridge._record_state(st_init, fetched_buildings=True, fetched_home=True)
                res_sync = bridge.handle_command({"command": "sync_economy", "planet_id": 12476})
                self.assertTrue(res_sync["ok"])
                self.assertFalse(res_sync["metal_production_enabled"])
                self.assertEqual(res_sync["blast_furnace_pct"], 0)
                bot_st = bridge.get_bot_control()
                self.assertFalse(bot_st["metal_production_by_planet"]["12476"])
                self.assertIn("12476", bot_st["economy_sync_timestamps"])

                # Commander loop invokes sync_economy when economy_sync_timestamps is stale/empty
                mock_api = MagicMock()
                mock_api.get_state.return_value = st_init
                mock_api.send_command.return_value = res_sync
                st_out, _, _, _, _ = commander.run_commander_tick(
                    mock_api,
                    live=True,
                    max_actions=0,
                    include_buildings=True,
                    economy_sync_timestamps={},
                )
                self.assertEqual(mock_api.send_command.call_args_list[0].args[0]["command"], "sync_economy")
                self.assertEqual(st_out.planets[0].economy_rates["resource2"], 0)
            finally:
                bridge.close()

    def test_escape_flight_command_payload_and_fleet_slot_busy_cooldown(self) -> None:
        from pathlib import Path
        from unittest.mock import MagicMock, patch
        import commander
        from spaceinvasion import api_server as api_mod
        from spaceinvasion.executor import ExecutionResult
        from spaceinvasion.state import (
            EnergyBalance,
            FleetAlert,
            GameState,
            PlanetState,
            Resources,
        )
        from spaceinvasion.strategy import PlannedAction

        # 1. Commander `_action_to_command_payload` maps ESCAPE_FLIGHT -> `escape_flight`
        esc_act = PlannedAction(
            priority="P2",
            kind="ESCAPE_FLIGHT",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="Save -> 02:380:13",
            reason="Tutorial 7/11: Make an escape flight",
            executable_now=True,
        )
        cmd_payload = commander._action_to_command_payload(esc_act, dry_run=False)
        self.assertEqual(
            cmd_payload,
            {
                "command": "escape_flight",
                "planet_id": 12476,
                "target_coords": "02:380:13",
                "reason": "Tutorial 7/11: Make an escape flight",
                "dry_run": False,
            },
        )
        no_coord_act = PlannedAction(
            priority="P2",
            kind="ESCAPE_FLIGHT",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="Escape flight",
            reason="Survival save",
            executable_now=True,
        )
        self.assertIsNone(commander._action_to_command_payload(no_coord_act, dry_run=True)["target_coords"])

        # 2. `escape_flight` API command validation, adjacent-slot fallback (incl. pos 16 -> 01),
        #    `fleetsave_plan` untouched, and `return_home=True` home refresh
        p_16 = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:16",
            planet_type="normal",
            temperature=31,
            fields_used=50,
            fields_max=230,
            resources=Resources(50_000, 25_000, 50_000, 40_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(3000, 2000),
            buildings={"b1": 18, "b2": 18, "b5": 20, "b9": 2, "b10": 2},
            ships={"s1": 4, "s4": 10, "s10": 2},
        )
        st_esc = GameState(
            31,
            1,
            "u",
            1000.0,
            (p_16,),
            active_planet_id=12476,
            fleet_alerts=(FleetAlert("attack", True, "Hostile attack inbound on 02:380:16"),),
            raid_targets=({"coords": "02:380:04", "owner": "Idle", "status_color": "yellow", "is_inactive": True, "last_scanned_ts": 995.0},),
            speedup_factor=20.0,
        )
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.__exit__.return_value = False
        fake_client.navigate_menu.return_value = (
            "<script>SI_TPL={planetID:12476};</script>"
            '<div>Tutorial 7 / 11</div><div id="tutorial.text">'
            '<form action="indexInternal.es?action=internalHome">'
            '<input type="hidden" name="tutorialComplete" value="1"/><input type="submit" value="Claim"/>'
            "</form></div>"
        )
        fake_client.read_full_snapshot.return_value = st_esc

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "cfg.yaml"
            cfg_path.write_text("allow_fleetsave: false\nallow_raids: true\nallow_galaxy_scan: true\n", encoding="utf-8")
            bridge = api_mod.GameApiBridge(
                db_path=Path(tmp) / "esc.db",
                config_path=cfg_path,
                log_dir=Path(tmp) / "logs",
                client_factory=lambda _p: fake_client,
                ensure_session_fn=lambda: None,
            )
            try:
                bridge._record_state(st_esc, fetched_buildings=True, fetched_home=True)

                # `fleetsave_plan` remains intact
                fs_plan = bridge.handle_command({"command": "fleetsave_plan", "planet_id": 12476})
                self.assertTrue(fs_plan["ok"])
                self.assertIn("escape_plan", fs_plan)

                # Validation errors on bad fields
                for bad_payload in (
                    {"command": "escape_flight", "target_coords": 123},
                    {"command": "escape_flight", "target_coords": "not-coords"},
                    {"command": "escape_flight", "target_name": 123},
                    {"command": "escape_flight", "reason": ["bad"]},
                ):
                    with self.assertRaises(api_mod.ApiRequestError):
                        bridge.handle_command(bad_payload)

                # With allow_fleetsave=false: executable_now=False, adjacent wraparound 16 -> 01
                res_guarded = bridge.handle_command({"command": "escape_flight", "planet_id": 12476})
                self.assertFalse(res_guarded["executed"])
                self.assertFalse(res_guarded["action"]["executable_now"])
                self.assertEqual(res_guarded["action"]["target_name"], "Save -> 02:380:01")

                # Enable allow_fleetsave=true; simulate fleet-slot busy failure ("no ship input fields found on internalFleet")
                bridge.handle_command({"command": "update_config", "config": {"allow_fleetsave": True}})
                mono_now = [5000.0]
                with (
                    patch("spaceinvasion.api_server.time.monotonic", side_effect=lambda: mono_now[0]),
                    patch("spaceinvasion.api_server.is_debug_port_open", return_value=True),
                ):
                    bridge._last_snapshot_at = 5000.0
                    bridge._last_home_at = 5000.0
                    with patch.object(
                        bridge._live_executor,
                        "execute_action",
                        side_effect=lambda _c, _s, act, **_kw: ExecutionResult(
                            executed=False,
                            dry_run=False,
                            action=act,
                            message="Aborted: no ship input fields found on internalFleet.",
                        ),
                    ):
                        res_busy = bridge.handle_command(
                            {
                                "command": "escape_flight",
                                "planet_id": 12476,
                                "target_name": "Save -> 02:380:05",
                                "reason": "Tutorial 7/11: Make an escape flight",
                            }
                        )
                    self.assertFalse(res_busy["executed"])
                    self.assertEqual(res_busy["action"]["target_name"], "Save -> 02:380:05")

                    # `/bot_status` exposes 180s `fleet_slot_cooldowns`
                    mono_now[0] = 5010.0
                    bot_st = bridge.get_bot_control()
                    self.assertAlmostEqual(bot_st["fleet_slot_cooldowns"]["12476"], 170.0, places=1)

                    # `plan` & `get_dashboard` hold ESCAPE_FLIGHT / SPY / ATTACK with `[Hold: fleet slots busy / returning]`
                    plan_held = bridge.handle_command({"command": "plan"})["actions"]
                    esc_held = [a for a in plan_held if a["kind"] == "ESCAPE_FLIGHT"]
                    self.assertTrue(esc_held)
                    self.assertFalse(esc_held[0]["executable_now"])
                    self.assertIn("[Hold: fleet slots busy / returning]", esc_held[0]["reason"])
                    for a in plan_held:
                        if a["kind"] in ("ATTACK", "SPY", "ESCAPE_FLIGHT") and a["planet_id"] == 12476:
                            self.assertFalse(a["executable_now"])
                            self.assertIn("[Hold: fleet slots busy / returning]", a["reason"])

                    dash_held = bridge.get_dashboard()
                    dash_esc = next(a for a in dash_held["plan"] if a["kind"] == "ESCAPE_FLIGHT")
                    self.assertFalse(dash_esc["executable_now"])
                    self.assertIn("[Hold: fleet slots busy / returning]", dash_esc["reason"])

                    # Commander also holds fleet actions when `fleet_slot_cooldowns` is active
                    cmd_api = MagicMock()
                    cmd_api.get_state.return_value = st_esc
                    _, cmd_act, cmd_res, _, _ = commander.run_commander_tick(
                        cmd_api,
                        live=True,
                        max_actions=1,
                        config=bridge.strategy_config,
                        include_buildings=True,
                        fleet_slot_cooldowns={12476: 170.0},
                    )
                    self.assertFalse(cmd_res["executed"])
                    cmd_api.send_command.assert_not_called()

                    # After 180s cooldown expires, ESCAPE_FLIGHT unlocks and `escape_flight` returns home & parses tutorialComplete
                    mono_now[0] = 5185.0
                    self.assertEqual(bridge.get_bot_control()["fleet_slot_cooldowns"], {})
                    plan_unlocked = bridge.handle_command({"command": "plan"})["actions"]
                    esc_unlocked = next(a for a in plan_unlocked if a["kind"] == "ESCAPE_FLIGHT")
                    self.assertTrue(esc_unlocked["executable_now"])
                    self.assertNotIn("[Hold: fleet slots busy / returning]", esc_unlocked["reason"])

                    with patch.object(
                        bridge._live_executor,
                        "execute_action",
                        side_effect=lambda _c, s, act, **_kw: ExecutionResult(
                            executed=True,
                            dry_run=False,
                            action=act,
                            message="Dispatched escape flight (1x s1) to [02:380:01].",
                            updated_state=s,
                        ),
                    ):
                        res_ok = bridge.handle_command(
                            {
                                "command": "escape_flight",
                                "planet_id": 12476,
                                "reason": "Tutorial 7/11: Make an escape flight",
                            }
                        )
                    self.assertTrue(res_ok["executed"])
                    fake_client.navigate_menu.assert_any_call("internalHome", priority="interactive")
                    self.assertTrue(res_ok["state"]["tutorial"]["can_complete"])
                    self.assertEqual(res_ok["next_action"]["kind"], "CLAIM_TUTORIAL")
            finally:
                bridge.close()


    def test_action_graph_command_isolated_simulation_zero_mutation_and_zero_browser_views(self) -> None:
        from dataclasses import asdict
        import requests
        from spaceinvasion.config import StrategyConfig, save_strategy_config
        from spaceinvasion.optimizer import get_last_optimizer_summary, run_optimizer
        from spaceinvasion.state import BuildingOffer, ResearchOffer

        with tempfile.TemporaryDirectory(prefix="si_ag_iso_") as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.yaml"
            db_path = tmp_path / "state" / "spaceinvasion.db"
            log_dir = tmp_path / "logs"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            log_dir.mkdir(parents=True, exist_ok=True)

            init_cfg = StrategyConfig(
                planner_mode="priority",
                playstyle="fleeter",
                ga_population_size=16,
                ga_generations=4,
                ga_horizon_steps=5,
                allow_raids=False,
                allow_fleetsave=False,
                max_actions_per_hour=45,
            )
            save_strategy_config(init_cfg, cfg_path)
            disk_before = cfg_path.read_text(encoding="utf-8")

            p1 = PlanetState(
                planet_id=101,
                name="Homeworld",
                coords="01:042:07",
                planet_type="normal",
                temperature=25,
                fields_used=22,
                fields_max=230,
                resources=Resources(25_000, 18_000, 12_000, 8_000),
                storage=Resources(100_000, 100_000, 100_000, 100_000),
                energy=EnergyBalance(300, 110),
                buildings={"b1": 4, "b2": 3, "b3": 4, "b4": 3, "b5": 6, "b7": 3, "b8": 2, "b9": 2},
                building_offers=(
                    BuildingOffer(1, "Pig iron mine", 4, 5, Resources(800, 320, 0, 0), True, "Available", "indexInternal.es?b=1"),
                    BuildingOffer(3, "Kryptonite mine", 4, 5, Resources(700, 500, 0, 0), True, "Available", "indexInternal.es?b=3"),
                ),
            )
            st = GameState(
                universe_id=1,
                user_id=1,
                nickname="cmdr",
                timestamp=1200.0,
                planets=(p1,),
                active_planet_id=101,
                researches={"r2": 2, "r3": 2},
                research_offers=(
                    ResearchOffer(2, "r2", "Energy technology", 2, 3, Resources(0, 1600, 3200, 1600), True, "Available", "indexInternal.es?r=2"),
                ),
                speedup_factor=4.0,
            )

            client_calls = [0]

            def _counting_factory(_port: int):
                client_calls[0] += 1
                raise AssertionError("Browser client_factory must NOT be invoked when refresh=False")

            seed_opt_cfg = StrategyConfig(planner_mode="ga_nn", ga_population_size=6, ga_generations=2, ga_horizon_steps=3, ga_seed=5)
            run_optimizer(st, config=seed_opt_cfg, record_global_summary=True)
            global_summary_before = json.loads(json.dumps(get_last_optimizer_summary(), allow_nan=False))

            bridge = GameApiBridge(
                client_factory=_counting_factory,
                config_path=cfg_path,
                db_path=db_path,
                log_dir=log_dir,
                ensure_session_fn=lambda: None,
            )
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                bridge._last_state = st
                bridge.record_rate_limit(20.0)

                r_sim = requests.post(
                    f"{base}/command",
                    json={
                        "command": "action_graph",
                        "planner_mode": "ga_nn",
                        "config": {
                            "ga_population_size": 8,
                            "ga_generations": 2,
                            "ga_horizon_steps": 4,
                            "ga_seed": 42,
                            "playstyle": "miner",
                            "allow_raids": True,
                            "allow_fleetsave": True,
                            "max_actions_per_hour": 95,
                        },
                        "simulated": True,
                        "refresh": False,
                    },
                    timeout=10,
                )
                self.assertEqual(r_sim.status_code, 200, r_sim.text)
                body = r_sim.json()
                self.assertTrue(body["ok"])
                ag = body["action_graph"]
                self.assertEqual(ag["mode"], "ga_nn")
                self.assertTrue(ag["simulated"])
                self.assertTrue(ag["is_dirty"])
                self.assertIn("planner_mode", ag["changed_keys"])
                self.assertIsNotNone(ag["ga_summary"])
                self.assertIn("step_outcomes", ag["ga_summary"]["best_simulation"])
                self.assertGreater(len(ag["nodes"]), 0)

                # Strict isolation proof: zero browser calls, zero runtime/disk/executor/summary mutation
                self.assertEqual(client_calls[0], 0)
                self.assertEqual(bridge.strategy_config, init_cfg)
                self.assertEqual(cfg_path.read_text(encoding="utf-8"), disk_before)
                self.assertFalse(bridge._live_executor.allow_raids)
                self.assertFalse(bridge._live_executor.allow_fleetsave)
                self.assertEqual(bridge._live_executor.max_actions_per_hour, 45)
                self.assertFalse(bridge._dry_executor.allow_raids)
                self.assertIsNone(bridge._last_optimizer_summary)
                self.assertEqual(get_last_optimizer_summary(), global_summary_before)

                r_cfg = requests.post(f"{base}/command", json={"command": "config"}, timeout=5).json()
                self.assertEqual(r_cfg["config"], asdict(init_cfg))

                r_dash = requests.get(f"{base}/dashboard?refresh=0", timeout=10).json()
                self.assertEqual(r_dash["planner_mode"], "priority")
                self.assertEqual(r_dash["action_graph"]["mode"], "priority")
                self.assertFalse(r_dash["action_graph"]["simulated"])

                # Reverse direction: active mode is ga_nn -> simulate priority and tweaked ga_nn without clobbering active summary
                requests.post(
                    f"{base}/command",
                    json={
                        "command": "update_config",
                        "config": {
                            "planner_mode": "ga_nn",
                            "ga_population_size": 6,
                            "ga_generations": 2,
                            "ga_horizon_steps": 3,
                            "ga_seed": 11,
                        },
                    },
                    timeout=10,
                )
                dash_ga = requests.get(f"{base}/dashboard?refresh=0", timeout=10).json()
                self.assertEqual(dash_ga["planner_mode"], "ga_nn")
                active_bridge_sum = json.loads(json.dumps(bridge._last_optimizer_summary, allow_nan=False))
                active_global_sum = json.loads(json.dumps(get_last_optimizer_summary(), allow_nan=False))
                self.assertIsNotNone(active_bridge_sum)

                r_sim_prio = requests.post(
                    f"{base}/command",
                    json={"command": "action_graph", "planner_mode": "priority", "simulated": True, "refresh": False},
                    timeout=10,
                ).json()
                self.assertEqual(r_sim_prio["action_graph"]["mode"], "priority")
                self.assertEqual(bridge._last_optimizer_summary, active_bridge_sum)
                self.assertEqual(get_last_optimizer_summary(), active_global_sum)

                r_sim_ga2 = requests.post(
                    f"{base}/command",
                    json={
                        "command": "action_graph",
                        "config": {"ga_population_size": 10, "ga_generations": 3, "ga_seed": 99},
                        "simulated": True,
                        "refresh": False,
                    },
                    timeout=10,
                ).json()
                self.assertEqual(r_sim_ga2["action_graph"]["effective_config"]["ga_population_size"], 10)
                self.assertEqual(bridge.strategy_config.ga_population_size, 6)
                self.assertEqual(bridge._last_optimizer_summary, active_bridge_sum)
                self.assertEqual(get_last_optimizer_summary(), active_global_sum)
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()

    def test_action_graph_validation_errors_and_update_config_apply_persistence(self) -> None:
        import requests
        from spaceinvasion.config import StrategyConfig, load_strategy_config, save_strategy_config

        with tempfile.TemporaryDirectory(prefix="si_ag_val_") as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.yaml"
            db_path = tmp_path / "state" / "spaceinvasion.db"
            log_dir = tmp_path / "logs"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            log_dir.mkdir(parents=True, exist_ok=True)

            init_cfg = StrategyConfig(
                planner_mode="priority",
                shipyard_targets={"small-fighter": 12, "spy-probe": 10, "recycler": 4},
            )
            save_strategy_config(init_cfg, cfg_path)
            baseline_cfg = load_strategy_config(cfg_path)

            p1 = PlanetState(
                planet_id=101,
                name="Homeworld",
                coords="01:042:07",
                planet_type="normal",
                temperature=20,
                fields_used=15,
                fields_max=230,
                resources=Resources(10_000, 8_000, 5_000, 4_000),
                storage=Resources(100_000, 100_000, 100_000, 100_000),
                energy=EnergyBalance(200, 80),
                buildings={"b1": 3, "b5": 4, "b7": 2},
            )
            st = GameState(1, 1, "cmdr", 1000.0, (p1,), active_planet_id=101)

            bridge = GameApiBridge(
                debug_port=1,
                client_factory=lambda _port: MagicMock(),
                config_path=cfg_path,
                db_path=db_path,
                log_dir=log_dir,
                ensure_session_fn=lambda: None,
            )
            bridge._last_state = st
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                invalid_payloads = [
                    {"command": "action_graph", "planner_mode": "bogus_mode?sid=SECRET_TOK_77"},
                    {"command": "action_graph", "planner_mode": ""},
                    {"command": "action_graph", "config": "not_a_dict"},
                    {"command": "action_graph", "config": {"ga_population_size": 0}},
                    {"command": "action_graph", "config": {"ga_generations": 500}},
                    {"command": "action_graph", "config": {"ga_horizon_steps": 0}},
                    {"command": "action_graph", "config": {"ga_mutation_rate": 2.5}},
                    {"command": "action_graph", "config": {"storage_warn_ratio": 1.5}},
                    {"command": "action_graph", "config": {"ga_seed": "abc"}},
                    {"command": "action_graph", "config": {"allow_raids": "yes"}},
                    {"command": "action_graph", "config": {"shipyard_targets": {"small-fighter": -3}}},
                    {"command": "action_graph", "config": {"ga_population_size": "1_6"}},
                    {"command": "action_graph", "config": {"ga_mutation_rate": "0_1.5"}},
                    {"command": "action_graph", "config": {"shipyard_targets": {"small-fighter": "1_6"}}},
                    {"command": "action_graph", "simulated": "yes"},
                    {"command": "action_graph", "planet_id": 999999},
                    {"command": "action_graph", "planet_id": "1_01"},
                    {"command": "action_graph", "config": {"min_colony_fields": "1_00"}},
                    {"command": "action_graph", "config": {"min_colony_fields": 49}},
                    {"command": "action_graph", "config": {"min_colony_fields": 601}},
                    {"command": "update_config", "config": {"ga_population_size": "1_6"}},
                    {"command": "update_config", "config": {"min_colony_fields": "1_00"}},
                    {"command": "build", "planet_id": "1_01", "building_id": 1},
                ]
                for bad in invalid_payloads:
                    resp = requests.post(f"{base}/command", json=bad, timeout=5)
                    self.assertEqual(resp.status_code, 400, msg=f"bad={bad!r}: {resp.text}")
                    self.assertNotIn("SECRET_TOK_77", resp.text)

                raw_nan = requests.post(
                    f"{base}/command",
                    data='{"command": "action_graph", "config": {"ga_mutation_rate": NaN}}',
                    headers={"Content-Type": "application/json"},
                    timeout=5,
                )
                self.assertEqual(raw_nan.status_code, 400)
                self.assertEqual(bridge.strategy_config, baseline_cfg)

                r_apply = requests.post(
                    f"{base}/command",
                    json={
                        "command": "update_config",
                        "config": {
                            "planner_mode": "ga_nn",
                            "playstyle": "hybrid",
                            "ga_population_size": 8,
                            "ga_generations": 2,
                            "ga_horizon_steps": 4,
                            "ga_seed": 21,
                            "allow_raids": True,
                            "allow_fleetsave": True,
                            "max_actions_per_hour": 80,
                            "shipyard_targets": {"small-fighter": 28},
                        },
                        "include_action_graph": True,
                    },
                    timeout=10,
                )
                self.assertEqual(r_apply.status_code, 200, r_apply.text)
                applied = r_apply.json()
                self.assertEqual(applied["config"]["planner_mode"], "ga_nn")
                self.assertEqual(applied["config"]["shipyard_targets"]["small-fighter"], 28)
                self.assertEqual(applied["config"]["shipyard_targets"]["spy-probe"], 10)
                self.assertEqual(applied["action_graph"]["mode"], "ga_nn")
                self.assertFalse(applied["action_graph"]["simulated"])
                self.assertFalse(applied["action_graph"]["is_dirty"])
                self.assertEqual(applied["action_graph"]["changed_keys"], [])

                reloaded = load_strategy_config(cfg_path)
                self.assertEqual(reloaded.planner_mode, "ga_nn")
                self.assertEqual(reloaded.playstyle, "hybrid")
                self.assertEqual(reloaded.ga_seed, 21)
                self.assertEqual(reloaded.shipyard_targets["small-fighter"], 28)
                self.assertEqual(reloaded.shipyard_targets["spy-probe"], 10)
                self.assertTrue(bridge._live_executor.allow_raids)
                self.assertTrue(bridge._live_executor.allow_fleetsave)
                self.assertEqual(bridge._live_executor.max_actions_per_hour, 80)
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()

    def test_bash_protection_timeout_persistence_across_db_json_and_planners(self) -> None:
        from dataclasses import replace as dc_replace
        import time
        from spaceinvasion.executor import ExecutionResult
        from spaceinvasion.fleet import GalaxySlot, SpyReport
        from spaceinvasion.strategy import plan_actions

        base = _sample_state()
        p0 = dc_replace(
            base.planets[0],
            ships={"s1": 9, "s4": 20},
        )
        state = dc_replace(base, planets=(p0,), tutorial=None)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "state" / "spaceinvasion.db"
            hist_json_path = tmp_path / "data" / "attack_history.json"
            cfg_path = tmp_path / "config.yaml"
            cfg_path.write_text("allow_raids: true\nallow_galaxy_scan: true\n", encoding="utf-8")

            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.read_full_snapshot.return_value = state

            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=tmp_path / "logs",
                db_path=db_path,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            try:
                assert bridge._db is not None
                bridge._db.attack_history_path = hist_json_path
                bridge._db.record_galaxy_slots(
                    [
                        GalaxySlot(
                            coords="02:383:02",
                            position=2,
                            name="SpaciaWorld",
                            owner="Spacia",
                            status_color="yellow",
                            is_spacia=True,
                        )
                    ]
                )
                bridge._db.record_spy_report(
                    SpyReport(
                        target_coords="02:383:02",
                        owner="Spacia",
                        resources=Resources(50000, 30000, 20000, 10000),
                        is_spacia=True,
                    )
                )
                bridge._raid_targets = bridge._enrich_raid_targets(bridge._db.get_spy_targets(limit=100))

                def _mock_bash_blocked_exec(_client, _st, act, **_kw):
                    return ExecutionResult(
                        action=act,
                        executed=False,
                        dry_run=False,
                        message="Aborted on internalSendFleet: Attack (forbidden): Player was attacked too often by you (bash protection) (cooldown 59540s)",
                        target_cooldown_seconds=59540.0,
                        target_block_reason="Attack (forbidden): Player was attacked too often by you (bash protection)",
                        target_is_bash_block=True,
                    )

                with patch.object(bridge._live_executor, "execute_action", side_effect=_mock_bash_blocked_exec):
                    atk_resp = bridge.handle_command(
                        {"command": "attack", "target_coords": "02:383:02", "dry_run": False}
                    )
                self.assertFalse(atk_resp["executed"])
                self.assertIn("bash protection", atk_resp["message"].lower())

                # Verified persisted in SQLite spy_targets + bash_blocks + attack_history.json
                row = bridge._db.get_spy_target_by_coords("02:383:02")
                self.assertIsNotNone(row)
                assert row is not None
                self.assertGreater(row["blocked_until_ts"], time.time() + 50000.0)
                self.assertEqual(row["bash_hits"], 1)
                self.assertEqual(bridge._db.summary()["counts"]["bash_blocks"], 1)
                exported = json.loads(hist_json_path.read_text(encoding="utf-8"))
                self.assertIn("02:383:02", exported["blocked_targets"])
                self.assertEqual(len(exported["bash_blocks"]), 1)

                # Subsequent attack command is immediately rejected without touching Selenium
                atk_again = bridge.handle_command(
                    {"command": "attack", "target_coords": "02:383:02", "dry_run": False}
                )
                self.assertFalse(atk_again["executed"])
                self.assertIn("BashTracker", atk_again["message"])

                # Both priority and ga_nn planners exclude or hold 02:383:02 during cooldown
                enriched_state = bridge._attach_raid_stats(bridge._merge_economy_rates(state))
                prio_plan = plan_actions(
                    enriched_state,
                    config=dc_replace(bridge.strategy_config, planner_mode="priority"),
                )
                self.assertFalse(
                    any(a.kind == "ATTACK" and "02:383:02" in a.target_name for a in prio_plan)
                )
                ga_plan = plan_actions(
                    enriched_state,
                    config=dc_replace(
                        bridge.strategy_config,
                        planner_mode="ga_nn",
                        ga_population_size=6,
                        ga_generations=1,
                        ga_horizon_steps=3,
                        ga_seed=7,
                    ),
                )
                self.assertFalse(
                    any(
                        a.kind == "ATTACK" and "02:383:02" in a.target_name and a.executable_now
                        for a in ga_plan
                    )
                )
            finally:
                bridge.close()

    def test_tutorial_step9_recycler_build_scan_dispatch_and_debris_clear(self) -> None:
        from dataclasses import replace as dc_replace
        from spaceinvasion.executor import ExecutionResult
        from spaceinvasion.fleet import GalaxySlot
        from spaceinvasion.strategy import PlannedAction, plan_actions
        import commander

        base = _sample_state()
        tut9 = TutorialStatus(
            step=9,
            total_steps=11,
            tasks=(
                TutorialTask(
                    description="Recycle space rubble 0 / 1",
                    building_name="",
                    building_id=None,
                    current=0,
                    target=1,
                    category="milestone",
                ),
            ),
            reward_text="5000 Metal",
            can_complete=False,
        )

        # 1) When s11 == 0, both planners emit BUILD_SHIP for Recycler (s11)
        p_no_rec = dc_replace(base.planets[0], buildings={"b1": 5, "b5": 5, "b8": 3, "b9": 4}, ships={"s1": 5})
        st_no_rec = dc_replace(
            base,
            planets=(p_no_rec,),
            researches={"r2": 4, "r3": 4, "r6": 2},
            tutorial=tut9,
        )
        cfg_live = StrategyConfig(allow_raids=True, allow_galaxy_scan=True, allow_shipyard=True)
        plan_build_rec = plan_actions(st_no_rec, config=cfg_live)
        self.assertTrue(any(a.kind == "BUILD_SHIP" and a.target_id == 11 for a in plan_build_rec))

        # 2) When s11 > 0 and battle history is newer than last scan, planner scans the battle system for rubble
        p_with_rec = dc_replace(p_no_rec, ships={"s1": 9, "s4": 20, "s11": 3})
        st_scan = dc_replace(
            st_no_rec,
            planets=(p_with_rec,),
            raid_targets=(
                {
                    "coords": "02:383:02",
                    "owner": "Spacia",
                    "status_color": "yellow",
                    "is_spacia": True,
                    "has_debris": False,
                    "last_scanned_ts": 1000.0,
                },
            ),
            raid_stats={
                "history": [
                    {"target_coords": "02:383:02", "attacked_ts": 1500.0, "is_exempt": True, "outcome": "Dispatched"}
                ]
            },
        )
        plan_scan = plan_actions(st_scan, config=cfg_live, now_ts=1600.0)
        self.assertEqual(plan_scan[0].kind, "SCAN_GALAXY")
        self.assertIn("02:383", plan_scan[0].target_name)

        # 3) Once debris is discovered (even on a free slot or battle planet), planner emits RECYCLE and API clears has_debris
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "state" / "spaceinvasion.db"
            cfg_path = tmp_path / "config.yaml"
            cfg_path.write_text("allow_raids: true\nallow_galaxy_scan: true\n", encoding="utf-8")

            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.read_full_snapshot.return_value = dc_replace(st_no_rec, planets=(p_with_rec,))

            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=None,
                db_path=db_path,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            try:
                assert bridge._db is not None
                bridge._db.record_galaxy_slots(
                    [
                        GalaxySlot(
                            coords="02:383:02",
                            position=2,
                            name="",
                            owner="",
                            status_color="free",
                            has_debris=True,
                        )
                    ]
                )
                bridge._raid_targets = bridge._enrich_raid_targets(bridge._db.get_spy_targets(limit=100))
                plan_resp = bridge.handle_command({"command": "plan"})
                rec_actions = [a for a in plan_resp["actions"] if a["kind"] == "RECYCLE"]
                self.assertTrue(rec_actions)
                self.assertTrue(rec_actions[0]["executable_now"])
                self.assertIn("02:383:02", rec_actions[0]["target_name"])

                ga_resp = bridge.handle_command({"command": "plan", "planner_mode": "ga_nn"})
                self.assertTrue(any(a["kind"] == "RECYCLE" and a["executable_now"] for a in ga_resp["actions"]))

                planned_rec = PlannedAction(
                    priority="P2",
                    kind="RECYCLE",
                    planet_id=p_with_rec.planet_id,
                    planet_coords=p_with_rec.coords,
                    target_id=11,
                    target_level=3,
                    target_name="Rubble -> 02:383:02",
                    reason="Tutorial recycle",
                    executable_now=True,
                )
                cmd_payload = commander._action_to_command_payload(planned_rec, dry_run=False)
                self.assertEqual(cmd_payload["command"], "recycle")
                self.assertEqual(cmd_payload["target_coords"], "02:383:02")
                self.assertEqual(cmd_payload["quantity"], 3)

                def _mock_rec_exec(_client, _st, act, **_kw):
                    return ExecutionResult(
                        action=act,
                        executed=True,
                        dry_run=False,
                        message="Dispatched 3 Recycler(s) (s11) to harvest rubble at 02:383:02",
                        clear_target_debris=True,
                    )

                with patch.object(bridge._live_executor, "execute_action", side_effect=_mock_rec_exec):
                    rec_res = bridge.handle_command(cmd_payload)
                self.assertTrue(rec_res["executed"])
                updated_row = bridge._db.get_spy_target_by_coords("02:383:02")
                assert updated_row is not None
                self.assertFalse(updated_row["has_debris"])
            finally:
                bridge.close()

    def test_antibot_captcha_auto_pauses_bridge_and_stops_commander_immediately(self) -> None:
        import contextlib
        import io
        import threading
        import requests
        from spaceinvasion.auth import AntiBotChallengeError
        import commander

        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.read_full_snapshot.side_effect = AntiBotChallengeError(
            "Anti-bot or CAPTCHA challenge detected (Sicherheitspruefung) — complete verification manually in Chrome."
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.yaml"
            cfg_path.write_text("allow_build: true\n", encoding="utf-8")
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                db_path=tmp_path / "captcha.db",
                log_dir=None,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            base_url = f"http://127.0.0.1:{port}"
            try:
                resp = requests.get(f"{base_url}/state", timeout=5)
                self.assertEqual(resp.status_code, 503)
                body = resp.json()
                self.assertTrue(body["captcha_detected"])
                self.assertTrue(body["paused"])

                bot_status = requests.get(f"{base_url}/bot_status", timeout=5).json()
                self.assertTrue(bot_status["paused"])
                self.assertTrue(bot_status["captcha_blocked"])

                # Persistent unread captcha_gate notification was created
                notifs = requests.post(
                    f"{base_url}/command",
                    json={"command": "notifications_list"},
                    timeout=5,
                ).json()
                self.assertGreaterEqual(notifs["unread_count"], 1)
                self.assertTrue(any(n.get("dedupe_key") == "captcha:gate" for n in notifs["items"]))

                # Commander in --loop mode stays alive, logs [ALERT] once, makes ZERO browser calls while blocked,
                # and resumes cleanly when operator clicks Resume (paused=False)!
                calls_before_wait = fake_client.read_full_snapshot.call_count
                sleep_count = [0]

                def _fake_sleep(_s: float) -> None:
                    sleep_count[0] += 1
                    if sleep_count[0] == 2:
                        # Operator solved CAPTCHA in Chrome and clicked Resume Bot
                        fake_client.read_full_snapshot.side_effect = None
                        fake_client.read_full_snapshot.return_value = _sample_state()
                        requests.post(
                            f"{base_url}/command",
                            json={"command": "bot_control", "paused": False},
                            timeout=5,
                        )

                out_buf = io.StringIO()
                with (
                    patch("spaceinvasion.api_server.is_debug_port_open", return_value=True),
                    patch("commander.time.sleep", side_effect=_fake_sleep),
                    contextlib.redirect_stdout(out_buf),
                ):
                    rc = commander.main(
                        [
                            "--api-url",
                            base_url,
                            "--loop",
                            "--interval",
                            "0.05",
                            "--max-ticks",
                            "3",
                            "--config",
                            str(cfg_path),
                            "--log-dir",
                            "",
                        ]
                    )
                self.assertEqual(rc, 0)
                out_txt = out_buf.getvalue()
                self.assertEqual(out_txt.count("[CMD][ALERT]"), 1)
                self.assertIn("CAPTCHA gate cleared and automation resumed", out_txt)
                # Zero snapshot calls happened on ticks 1 & 2 while blocked; 1 snapshot call happened on tick 3 after Resume!
                self.assertEqual(fake_client.read_full_snapshot.call_count, calls_before_wait + 1)

                # Resuming auto-acknowledged the captcha:gate notification and cleared captcha_blocked
                bot_cleared = requests.get(f"{base_url}/bot_status", timeout=5).json()
                self.assertFalse(bot_cleared["captcha_blocked"])
                notifs_after = requests.post(
                    f"{base_url}/command",
                    json={"command": "notifications_list", "status": "unread"},
                    timeout=5,
                ).json()
                self.assertFalse(any(n.get("dedupe_key") == "captcha:gate" for n in notifs_after["items"]))

                # Direct tick exception path also pauses & waits passively instead of exiting with code 2
                fake_client.read_full_snapshot.side_effect = AntiBotChallengeError("Turnstile gate")
                bridge._last_snapshot_at = -1.0
                out_buf2 = io.StringIO()
                with (
                    patch("commander.time.sleep", side_effect=lambda _s: None),
                    contextlib.redirect_stdout(out_buf2),
                ):
                    rc2 = commander.main(
                        [
                            "--api-url",
                            base_url,
                            "--loop",
                            "--interval",
                            "0.05",
                            "--max-ticks",
                            "2",
                            "--config",
                            str(cfg_path),
                            "--log-dir",
                            "",
                        ]
                    )
                self.assertEqual(rc2, 0)
                self.assertEqual(out_buf2.getvalue().count("[CMD][ALERT]"), 1)
                self.assertTrue(requests.get(f"{base_url}/bot_status", timeout=5).json()["captcha_blocked"])
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()

    def test_batch_interval_spy_inbox_sync_and_exact_build_wakeup_timers(self) -> None:
        from dataclasses import replace as dc_replace
        import commander
        from spaceinvasion.config import load_strategy_config, save_strategy_config
        from spaceinvasion.executor import ExecutionResult
        from spaceinvasion.fleet import GalaxySlot
        from spaceinvasion.state import QueueJob, parse_unread_banner_count
        from spaceinvasion.strategy import PlannedAction

        # 1) Top banner unread count ("You have 13 unread Messages!") & German/RETRO inbox spy reports
        self.assertEqual(parse_unread_banner_count("<div>✉️ You have 13 unread Messages! [i]</div>"), 13)
        self.assertEqual(parse_unread_banner_count("<div>Du hast 4 ungelesene Nachrichten!</div>"), 4)

        base = _sample_state()
        st_unread = dc_replace(base, unread_messages={"total": 13}, tutorial=None)
        inbox_html = """
        <html><body>
          <div class="message">
            Spionagebericht auf 02:381:05 Spieler: LazyMiner
            Roheisen: 45.000 Metall: 22.000 Kryptonit: 11.000 Spice: 4.500
          </div>
        </body></html>
        """

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.yaml"
            db_path = tmp_path / "state" / "spaceinvasion.db"
            save_strategy_config(
                StrategyConfig(action_batch_interval_seconds=180, allow_raids=True, allow_galaxy_scan=True),
                cfg_path,
            )
            loaded_cfg = load_strategy_config(cfg_path)
            self.assertEqual(loaded_cfg.action_batch_interval_seconds, 180)

            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.read_full_snapshot.return_value = st_unread
            # First call ("spio") has no reports; fallback call ("") reads main inbox!
            fake_client.open_messages.side_effect = ["<html>empty spio tab</html>", inbox_html]

            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=None,
                db_path=db_path,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            try:
                # Live executor does NOT enforce hourly cap when using action_batch_interval_seconds
                self.assertFalse(bridge._live_executor.enforce_hourly_cap)
                assert bridge._db is not None
                bridge._db.record_galaxy_slots(
                    [
                        GalaxySlot(
                            coords="02:381:05",
                            position=5,
                            name="Colony",
                            owner="LazyMiner",
                            status_color="yellow",
                        )
                    ]
                )
                bridge._raid_targets = bridge._enrich_raid_targets(bridge._db.get_spy_targets(limit=100))

                # GET /state automatically reads unread spy reports from main inbox once and deduplicates
                state_dict = bridge.get_state(include_buildings=True)
                self.assertEqual(fake_client.open_messages.call_count, 2)
                spied_row = bridge._db.get_spy_target_by_coords("02:381:05")
                self.assertIsNotNone(spied_row)
                assert spied_row is not None
                self.assertTrue(spied_row["has_spy_intel"])
                self.assertEqual(spied_row["resources"]["pig_iron"], 45000)
                self.assertEqual(spied_row["resources"]["metal"], 22000)
                self.assertEqual(spied_row["spy_count"], 1)

                # Record a second spy report + uninhabited colony probe and verify append-only spy_reports table
                import time
                from spaceinvasion.fleet import SpyReport

                bridge._db.record_spy_report(
                    SpyReport(
                        target_coords="02:381:05",
                        owner="LazyMiner",
                        resources=Resources(60000, 30000, 15000, 9000),
                    ),
                    is_inactive=True,
                    ts=time.time() + 10.0,
                )
                bridge._db.record_spy_report(
                    SpyReport(
                        target_coords="02:381:09",
                        owner="",
                        resources=Resources(),
                        uninhabited_fields=245,
                    ),
                    status_color="free",
                    ts=time.time() + 20.0,
                )
                hist_reports = bridge._db.get_spy_reports(target_coords="02:381:05")
                self.assertEqual(len(hist_reports), 2)
                self.assertEqual(hist_reports[0]["resources"]["pig_iron"], 60000)
                self.assertEqual(hist_reports[1]["resources"]["pig_iron"], 45000)
                col_row = bridge._db.get_spy_target_by_coords("02:381:09")
                self.assertIsNotNone(col_row)
                assert col_row is not None
                self.assertEqual(col_row["uninhabited_fields"], 245)
                self.assertEqual(bridge._db.summary()["counts"]["spy_reports"], 3)

                # Power building selection blocks Thermosolar plant (b6) when fields are tight (<=40 free) or b6 >= 6
                from spaceinvasion.strategy import _pick_best_power_building

                tight_cold = PlanetState(
                    planet_id=12476,
                    name="EndGameCold",
                    coords="02:380:15",
                    planet_type="normal",
                    temperature=-120,
                    fields_used=200,
                    fields_max=230,
                    resources=Resources(50000, 50000, 50000, 10000),
                    storage=Resources(500000, 500000, 500000, 500000),
                    energy=EnergyBalance(100, 150),
                    buildings={"b1": 15, "b2": 10, "b3": 14, "b4": 14, "b5": 15, "b6": 0},
                )
                bid_tight, _, _ = _pick_best_power_building(
                    tight_cold, dict(tight_cold.buildings), {"r2": 4}
                )
                self.assertEqual(bid_tight, 5)  # Fusion plant (b5), preserving scarce fields

                # Force snapshot cache expiry with unchanged unread total=13 -> zero extra open_messages calls
                bridge._last_snapshot_at = 0.0
                bridge.get_state(include_buildings=False)
                self.assertEqual(fake_client.open_messages.call_count, 2)

                # update_config validates and persists action_batch_interval_seconds
                upd = bridge.handle_command(
                    {"command": "update_config", "config": {"action_batch_interval_seconds": 240}}
                )
                self.assertEqual(upd["config"]["action_batch_interval_seconds"], 240)

                # 2) Exact build/queue wakeup timer calculation
                wait_act = PlannedAction(
                    priority="P2",
                    kind="WAIT_RESOURCES",
                    planet_id=12476,
                    planet_coords="02:380:12",
                    target_id=3,
                    target_name="Kryptonite mine",
                    target_level=7,
                    reason="Waiting for resources",
                    executable_now=False,
                    wait_seconds=420.0,
                )
                wake_long, reason_long = commander.compute_next_wakeup_seconds(
                    st_unread, wait_act, [], min_interval_s=120.0, max_idle_s=900.0
                )
                self.assertAlmostEqual(wake_long, 422.0, places=1)
                self.assertIn("Kryptonite mine", reason_long)

                # Short wait (< min_interval_s) honors min_interval_s so actions accumulate into a batch
                short_act = dc_replace(wait_act, wait_seconds=35.0)
                wake_short, _ = commander.compute_next_wakeup_seconds(
                    st_unread, short_act, [], min_interval_s=120.0, max_idle_s=900.0
                )
                self.assertAlmostEqual(wake_short, 120.0, places=1)

                # Queue completion timer wakes right when research/fleet finishes if earlier than wait_act
                st_queue = dc_replace(
                    st_unread,
                    active_queues=(QueueJob(category="research", name="Energy technology", remaining_text="00:03:20"),),
                )
                wake_q, reason_q = commander.compute_next_wakeup_seconds(
                    st_queue, wait_act, [], min_interval_s=120.0, max_idle_s=900.0
                )
                self.assertAlmostEqual(wake_q, 202.0, places=1)
                self.assertIn("research queue completion", reason_q)

                # 3) Batch execution sends multiple accumulated builds in one tick with 1 get_state call
                st_after_1 = dict(state_dict)
                st_after_1["planets"] = [dict(state_dict["planets"][0])]
                st_after_1["planets"][0]["buildings"] = {"b1": 3, "b3": 2, "b5": 4}
                st_after_1["planets"][0]["building_offers"] = [
                    o for o in state_dict["planets"][0]["building_offers"] if o["building_id"] != 5
                ]
                st_after_2 = dict(state_dict)
                st_after_2["planets"] = [dict(state_dict["planets"][0])]
                st_after_2["planets"][0]["buildings"] = {"b1": 4, "b3": 2, "b5": 4}
                st_after_2["planets"][0]["building_offers"] = []
                mock_api = MagicMock()
                mock_api.get_state.return_value = base
                mock_api.send_command.side_effect = [
                    {"ok": True, "command": "build", "executed": True, "message": "Built b5", "state": st_after_1},
                    {"ok": True, "command": "build", "executed": True, "message": "Built b1", "state": st_after_2},
                ]
                _, _, _, _, batch_outcomes = commander.run_commander_tick(
                    mock_api,
                    live=True,
                    max_actions=10,
                    config=StrategyConfig(action_batch_interval_seconds=120),
                    include_buildings=True,
                )
                self.assertEqual(mock_api.get_state.call_count, 1)
                self.assertGreaterEqual(len([o for o in batch_outcomes if o.get("executed")]), 2)
            finally:
                bridge.close()

    def test_realistic_5_planet_dashboard_refresh_false_latency_under_20ms(self) -> None:
        import statistics
        import time
        from spaceinvasion.config import save_strategy_config
        from spaceinvasion.state import FleetAlert, ResearchOffer, UnitOffer

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.yaml"
            db_path = tmp_path / "spaceinvasion.db"
            log_dir = tmp_path / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            save_strategy_config(StrategyConfig(allow_multi_planet=True, allow_raids=True, allow_fleetsave=True), cfg_path)

            planets = []
            for idx in range(5):
                pid = 500 + idx
                coords = f"01:0{40 + idx}:0{idx + 3}"
                b_offers = tuple(
                    BuildingOffer(
                        building_id=bid,
                        name=f"Building {bid}",
                        current_level=4,
                        target_level=5,
                        cost=Resources(800 * bid, 500 * bid, 300 * bid, 100 * bid),
                        can_build=True,
                        status_text="Available",
                        build_href=f"indexInternal.es?action=internalBuildItem&buildingID={bid}",
                    )
                    for bid in (1, 2, 3, 4, 5, 7, 8, 9)
                )
                s_offers = tuple(
                    UnitOffer(
                        unit_type="ship",
                        unit_id=sid,
                        name=sname,
                        owned=2,
                        cost=Resources(2000, 1500, 500, 200),
                        can_build=True,
                        input_name=f"ship[{sid}]",
                    )
                    for sid, sname in ((1, "Small transporter"), (4, "Small fighter"), (10, "Spy probe"), (11, "Recycler"))
                )
                planets.append(
                    PlanetState(
                        planet_id=pid,
                        name=f"Colony-{idx + 1}",
                        coords=coords,
                        planet_type="normal",
                        temperature=25 - idx * 4,
                        fields_used=36,
                        fields_max=230,
                        resources=Resources(45_000, 32_000, 24_000, 15_000),
                        storage=Resources(200_000, 200_000, 200_000, 200_000),
                        energy=EnergyBalance(420, 310),
                        buildings={"b1": 6, "b2": 5, "b3": 6, "b4": 5, "b5": 8, "b7": 4, "b8": 3, "b9": 3, "b12": 4},
                        ships={"s1": 8, "s4": 15, "s10": 6, "s11": 2},
                        building_offers=b_offers,
                        ship_offers=s_offers,
                    )
                )
            st = GameState(
                universe_id=31,
                user_id=103603,
                nickname="cmdr_perf",
                timestamp=2000.0,
                planets=tuple(planets),
                active_planet_id=500,
                researches={"r1": 2, "r2": 3, "r3": 3, "r4": 2, "r5": 2},
                research_offers=(
                    ResearchOffer(2, "r2", "Energy technology", 3, 4, Resources(0, 3200, 6400, 3200), True, "Available", "indexInternal.es?r=2"),
                    ResearchOffer(5, "r5", "Spy technology", 2, 3, Resources(800, 4000, 800, 0), True, "Available", "indexInternal.es?r=5"),
                ),
                fleet_alerts=(
                    FleetAlert(
                        mission="attack",
                        hostile=True,
                        summary="Hostile fleet inbound to [01:040:03]",
                    ),
                ),
                speedup_factor=4.0,
            )

            bridge = GameApiBridge(
                debug_port=1,
                client_factory=lambda _port: MagicMock(),
                config_path=cfg_path,
                db_path=db_path,
                log_dir=log_dir,
                ensure_session_fn=lambda: None,
            )
            try:
                bridge._record_state(st)
                warm = bridge.dashboard(refresh=False)
                self.assertTrue(warm["ok"])
                self.assertGreaterEqual(warm["action_graph"]["summary"]["total_nodes"], 15)

                samples_ms: list[float] = []
                for _ in range(20):
                    t0 = time.perf_counter()
                    d = bridge.dashboard(refresh=False)
                    samples_ms.append((time.perf_counter() - t0) * 1000.0)
                    self.assertTrue(d["ok"])

                median_ms = statistics.median(samples_ms)
                self.assertLess(
                    median_ms,
                    20.0,
                    msg=f"5-planet dashboard(refresh=False) median={median_ms:.2f}ms samples={[round(x, 2) for x in samples_ms]}",
                )

                bridge.handle_command({"command": "update_config", "config": {"playstyle": "miner"}})
                after_cfg = bridge.dashboard(refresh=False)
                self.assertEqual(after_cfg["action_graph"]["effective_config"]["playstyle"], "miner")
                self.assertFalse(after_cfg["action_graph"]["is_dirty"])
            finally:
                bridge.close()

    def test_full_sqlite_state_persistence_across_bridge_restarts(self) -> None:
        import time
        from spaceinvasion.fleet import GalaxySlot, SpyReport
        from spaceinvasion.state import FleetAlert, QueueJob, ResearchOffer, UnitOffer

        with tempfile.TemporaryDirectory(prefix="si_full_db_") as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.yaml"
            db_path = tmp_path / "spaceinvasion.db"
            log_dir = tmp_path / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            now_wall = time.time()
            planet = PlanetState(
                planet_id=777,
                name="Prime",
                coords="02:380:08",
                planet_type="normal",
                temperature=22,
                fields_used=31,
                fields_max=242,
                resources=Resources(64_000, 41_000, 29_000, 18_500),
                storage=Resources(200_000, 200_000, 200_000, 200_000),
                energy=EnergyBalance(520, 390),
                buildings={"b1": 7, "b2": 6, "b3": 7, "b4": 5, "b5": 8, "b10": 3},
                ships={"s1": 12, "s4": 18, "s8": 1, "s10": 6, "s11": 3},
                economy_rates={"b1": 100, "b2": 80, "b3": 100, "b4": 100, "b5": 100, "b6": 0},
                building_offers=(
                    BuildingOffer(
                        building_id=1,
                        name="Pig iron mine",
                        current_level=7,
                        target_level=8,
                        cost=Resources(2400, 900, 0, 0),
                        can_build=True,
                        status_text="Available",
                        build_href="indexInternal.es?action=internalBuildItem&buildingID=1",
                    ),
                ),
                ship_offers=(
                    UnitOffer(
                        unit_type="ship",
                        unit_id=11,
                        name="Recycler",
                        owned=3,
                        cost=Resources(10_000, 6000, 2000, 0),
                        can_build=True,
                        input_name="ship[11]",
                    ),
                ),
            )
            persisted_state = GameState(
                universe_id=31,
                user_id=103603,
                nickname="persist_cmdr",
                timestamp=now_wall,
                planets=(planet,),
                active_planet_id=777,
                researches={"r1": 3, "r2": 4, "r3": 4, "r5": 3},
                unread_messages={"spio": 2, "fleet": 1, "total": 3},
                active_queues=(
                    QueueJob(
                        category="building",
                        name="Pig iron mine",
                        building_id=1,
                        target_level=8,
                        remaining_text="00:03:05",
                    ),
                ),
                fleet_alerts=(
                    FleetAlert(
                        mission="recycle",
                        hostile=False,
                        summary="Recycling [02:380:04]",
                    ),
                ),
                research_offers=(
                    ResearchOffer(
                        research_id=5,
                        research_key="r5",
                        name="Spy technology",
                        current_level=3,
                        target_level=4,
                        cost=Resources(1600, 8000, 1600, 0),
                        can_research=True,
                        status_text="Available",
                        research_href="indexInternal.es?action=internalBuildResearch&researchID=5",
                    ),
                ),
                speedup_factor=4.0,
            )

            bridge1 = GameApiBridge(
                debug_port=1,
                client_factory=lambda _port: MagicMock(),
                config_path=cfg_path,
                db_path=db_path,
                log_dir=log_dir,
                ensure_session_fn=lambda: None,
            )
            try:
                bridge1.handle_command(
                    {
                        "command": "update_config",
                        "config": {
                            "action_batch_interval_seconds": 180,
                            "planner_mode": "ga_nn",
                            "playstyle": "fleeter",
                        },
                    }
                )
                bridge1._record_state(persisted_state)
                assert bridge1._db is not None
                slots = [
                    GalaxySlot(
                        position=4,
                        coords="02:380:04",
                        name="Spacia Outpost",
                        owner="Spacia",
                        status_color="yellow",
                        is_spacia=True,
                        has_debris=True,
                    ),
                    GalaxySlot(
                        position=9,
                        coords="02:380:09",
                        name="",
                        owner="",
                        status_color="free",
                        is_spacia=False,
                        has_debris=False,
                    ),
                    GalaxySlot(
                        position=15,
                        coords="02:380:15",
                        name="",
                        owner="",
                        status_color="free",
                        is_spacia=False,
                        has_debris=False,
                    ),
                ]
                saved_targets = bridge1._db.record_galaxy_slots(slots, ts=now_wall)
                self.assertEqual(saved_targets, 1)
                bridge1._db.record_galaxy_scan(2, 380, slots, ts=now_wall)

                bridge1._db.record_spy_report(
                    SpyReport(
                        target_coords="02:380:04",
                        owner="Spacia",
                        is_spacia=True,
                        resources=Resources(40_000, 30_000, 20_000, 10_000),
                        fleet={},
                        defense={},
                        dug_in=False,
                    ),
                    ts=now_wall - 60.0,
                )
                bridge1._db.record_spy_report(
                    SpyReport(
                        target_coords="02:380:04",
                        owner="Spacia",
                        is_spacia=True,
                        resources=Resources(55_000, 42_000, 28_000, 14_000),
                        fleet={"s1": 2},
                        defense={"d1": 1},
                        dug_in=False,
                    ),
                    ts=now_wall - 10.0,
                )
                bridge1._db.record_spy_report(
                    SpyReport(
                        target_coords="02:380:09",
                        owner="",
                        is_spacia=False,
                        resources=Resources(0, 0, 0, 0),
                        fleet={},
                        defense={},
                        dug_in=None,
                        uninhabited_fields=248,
                    ),
                    ts=now_wall - 5.0,
                )
                bridge1._db.record_target_block(
                    "02:383:02",
                    blocked_until_ts=now_wall + 7200.0,
                    reason="Attack (forbidden): Player was attacked too often by you (bash protection)",
                    is_bash=True,
                    ts=now_wall,
                )

                now_mono = time.monotonic()
                bridge1._fleet_slot_cooldown_until[777] = now_mono + 150.0
                bridge1._economy_sync_timestamps[777] = now_wall - 20.0
                bridge1._last_synced_unread_total = 3
                bridge1._pending_spy_sync = True
                bridge1._last_spio_sync_at = now_mono - 5.0
                bridge1._last_url = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalBuildings"
                bridge1._last_optimizer_summary = {"mode": "ga_nn", "improvement_pct": 14.5}
                bridge1._bot_control["mode_override"] = "dry-run"
                bridge1._record_antibot_pause(RuntimeError("Cloudflare Turnstile challenge"))
            finally:
                bridge1.close()

            # Remove config.yaml on disk to prove strategy_config rehydrates from SQLite meta
            if cfg_path.exists():
                cfg_path.unlink()

            client_calls = 0

            def _forbidden_client(_port: int) -> Any:
                nonlocal client_calls
                client_calls += 1
                raise AssertionError("Offline dashboard read must use SQLite state without opening Chrome")

            bridge2 = GameApiBridge(
                debug_port=1,
                client_factory=_forbidden_client,
                config_path=cfg_path,
                db_path=db_path,
                log_dir=log_dir,
                ensure_session_fn=lambda: None,
            )
            try:
                self.assertEqual(bridge2.strategy_config.action_batch_interval_seconds, 180)
                self.assertEqual(bridge2.strategy_config.planner_mode, "ga_nn")
                self.assertEqual(bridge2.strategy_config.playstyle, "fleeter")
                self.assertTrue(bridge2._bot_control["paused"])
                self.assertTrue(bridge2._bot_control["captcha_blocked"])
                self.assertIn("Turnstile", bridge2._bot_control["captcha_message"] or "")
                self.assertEqual(bridge2._bot_control["mode_override"], "dry-run")
                self.assertEqual(bridge2._last_synced_unread_total, 3)
                self.assertTrue(bridge2._pending_spy_sync)
                self.assertGreater(bridge2._get_fleet_slot_cooldowns().get("777", 0.0), 60.0)
                self.assertIsNotNone(bridge2._last_state)
                assert bridge2._last_state is not None
                self.assertEqual(len(bridge2._last_state.active_queues), 1)
                self.assertEqual(bridge2._last_state.active_queues[0].name, "Pig iron mine")
                self.assertEqual(len(bridge2._last_state.research_offers), 1)
                self.assertEqual(len(bridge2._last_state.planets[0].building_offers), 1)
                self.assertEqual(len(bridge2._last_state.planets[0].ship_offers), 1)

                dash = bridge2.dashboard(refresh=False)
                self.assertEqual(client_calls, 0)
                self.assertTrue(dash["ok"])
                self.assertTrue(dash["bot_control"]["paused"])
                self.assertTrue(dash["bot_control"]["captcha_blocked"])
                self.assertEqual(dash["state"]["nickname"], "persist_cmdr")

                assert bridge2._db is not None
                reports_04 = bridge2._db.get_spy_reports(target_coords="02:380:04")
                self.assertEqual(len(reports_04), 2)
                self.assertEqual(reports_04[0]["resources"]["pig_iron"], 55_000)
                self.assertEqual(reports_04[1]["resources"]["pig_iron"], 40_000)
                reports_09 = bridge2._db.get_spy_reports(target_coords="02:380:09")
                self.assertEqual(len(reports_09), 1)
                self.assertEqual(reports_09[0]["uninhabited_fields"], 248)

                free_slots = bridge2._db.get_galaxy_slots(galaxy=2, system=380, status_color="free")
                self.assertEqual([s["coords"] for s in free_slots], ["02:380:09", "02:380:15"])

                db_sum = bridge2.handle_command({"command": "db_summary"})
                self.assertTrue(db_sum["ok"])
                self.assertEqual(db_sum["db"]["counts"]["galaxy_slots"], 3)
                self.assertEqual(db_sum["db"]["counts"]["spy_reports"], 3)
                self.assertEqual(db_sum["db"]["counts"]["bash_blocks"], 1)
                for key in ("latest_game_state", "bridge_runtime_state", "strategy_config"):
                    self.assertIn(key, db_sum["db"]["persisted_meta_keys"])
            finally:
                bridge2.close()

    def test_archive_messages_delete_in_game_after_db_commit_and_append_only_universe_history(self) -> None:
        import time
        from dataclasses import replace as dc_replace
        from spaceinvasion.fleet import GalaxySlot, SpyReport

        with tempfile.TemporaryDirectory(prefix="si_msg_archive_") as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.yaml"
            db_path = tmp_path / "spaceinvasion.db"
            log_dir = tmp_path / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            base_state = _sample_state()
            st_unread = dc_replace(base_state, unread_messages={"total": 3}, tutorial=None)

            inbox_html = """
            <html><body>
              <span id="planet-coords">[02:380:12]</span>
              <form id="ress">
                <span id="roheisen">Pig iron: 269.575</span>
                <span id="metall">Metal: 7.044</span>
                <span id="kryptonit">Kryptonite: 400.432</span>
                <span id="spice">Spice: 157.956</span>
              </form>
              <form action="indexInternal.es?action=internalMessages&sid=TOPSECRET" method="post">
                <table>
                  <tr>
                    <td><input type="checkbox" name="del[701]" value="701" /></td>
                    <td>2026-11-16 16:10:01</td>
                    <td>Fleet Command</td>
                    <td>Espionage report on Vault [02:381:07]</td>
                  </tr>
                  <tr>
                    <td colspan="4">
                      Player: StarMiner | Pig iron: 92.000 Metal: 46.000 Kryptonite: 28.000 Spice: 14.000
                      Pig iron mine: 15 Fusion plant: 13 Energy technology: 6
                      Small transporter: 4 Mortar: 1 Defenses are not dug-in
                    </td>
                  </tr>
                  <tr>
                    <td><input type="checkbox" name="del[702]" value="702" /></td>
                    <td>2026-11-16 16:11:05</td>
                    <td>Fleet Command</td>
                    <td>Battle report [02:382:04]</td>
                  </tr>
                  <tr>
                    <td colspan="4">
                      Attacker won! Debris field: Pig iron: 24.000 Metal: 12.000
                    </td>
                  </tr>
                  <tr>
                    <td><input type="checkbox" name="del[703]" value="703" /></td>
                    <td>2026-11-16 16:12:30</td>
                    <td>Recycler Fleet</td>
                    <td>Recycling report [02:380:04]</td>
                  </tr>
                  <tr>
                    <td colspan="4">
                      Your Recycler fleet harvested Pig iron: 20.000 Metal: 10.000 at [02:380:04].
                    </td>
                  </tr>
                </table>
              </form>
            </body></html>
            """

            db_rows_at_delete_time: list[int] = []
            deleted_batches: list[list[str]] = []

            class _FakeDeletingClient:
                def __init__(self, bridge_ref_holder: list[Any]) -> None:
                    self._holder = bridge_ref_holder
                    self.open_calls: list[str] = []

                def __enter__(self) -> "_FakeDeletingClient":
                    return self

                def __exit__(self, *_exc: Any) -> None:
                    return None

                def ensure_in_game(self, **_kw: Any) -> None:
                    return None

                def read_full_snapshot(self, **_kw: Any) -> GameState:
                    return st_unread

                def open_messages(self, msg_type: str = "", **_kw: Any) -> str:
                    self.open_calls.append(msg_type)
                    if msg_type == "spio":
                        return "<html><body>No espionage tab messages</body></html>"
                    return inbox_html

                def expand_collapsed_messages_on_page(self) -> Optional[str]:
                    return None

                def delete_messages_on_current_page(
                    self,
                    server_msg_ids: Any = (),
                    *,
                    all_on_page_persisted: bool = False,
                    priority: str = "normal",
                ) -> tuple[bool, str]:
                    br = self._holder[0]
                    assert br._db is not None
                    # Strictly verify SQLite already committed all 3 messages BEFORE in-game deletion runs
                    committed = br._db.get_game_messages()
                    db_rows_at_delete_time.append(len(committed))
                    self.assert_before_delete_ok = (
                        len(committed) == 3
                        and all(not m["deleted_in_game"] for m in committed)
                        and all_on_page_persisted
                    )
                    deleted_batches.append(list(server_msg_ids))
                    return (True, "<html><body>No messages</body></html>")

            holder: list[Any] = [None]
            client = _FakeDeletingClient(holder)
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                log_dir=log_dir,
                db_path=db_path,
                client_factory=lambda _port: client,
                ensure_session_fn=lambda: None,
            )
            holder[0] = bridge
            try:
                assert bridge._db is not None
                t0 = time.time()
                # Pre-seed own planet 02:380:12 (simulating historical self-planet bug), legacy unpadded 2:381:7,
                # plus battle target 02:382:04 (no debris yet) and recycle target 02:380:04 (with debris)
                bridge._db.record_spy_report(
                    SpyReport(target_coords="02:380:12", owner="self", resources=Resources(100, 100, 100, 100)),
                    ts=t0 - 120.0,
                )
                # Insert both raw unnormalized '2:381:7' and canonical '02:381:07' to verify non-destructive merged_into
                with bridge._db._lock:
                    bridge._db._conn.execute(
                        "INSERT INTO spy_targets(coords, owner, status_color, is_inactive, last_scanned_ts) VALUES ('2:381:7', 'StarMiner', 'yellow', 1, ?)",
                        (t0 - 95.0,),
                    )
                    bridge._db._conn.execute(
                        "INSERT INTO spy_targets(coords, owner, status_color, is_inactive, last_scanned_ts) VALUES ('02:381:07', 'StarMiner', 'yellow', 1, ?)",
                        (t0 - 90.0,),
                    )
                    bridge._db._conn.commit()
                bridge._db.record_galaxy_slots(
                    [
                        GalaxySlot("02:382:04", 4, "Warzone", "Defender", "yellow", has_debris=False),
                        GalaxySlot("02:380:04", 4, "Scrapyard", "Spacia", "yellow", is_spacia=True, has_debris=True),
                    ],
                    ts=t0 - 60.0,
                )
                # Repeated scan at a later timestamp appends new rows to galaxy_slot_history and galaxy_scan_history
                bridge._db.record_galaxy_scan(
                    2,
                    380,
                    [GalaxySlot("02:380:04", 4, "Scrapyard", "Spacia", "yellow", is_spacia=True, has_debris=True)],
                    ts=t0 - 30.0,
                )
                bridge._db.record_galaxy_scan(
                    2,
                    380,
                    [GalaxySlot("02:380:04", 4, "Scrapyard", "Spacia", "yellow", is_spacia=True, has_debris=False)],
                    ts=t0 - 10.0,
                )

                # Trigger state read -> archives inbox to SQLite first, then deletes in-game, then marks deleted_in_game=1
                bridge.get_state(include_buildings=True)

                self.assertEqual(db_rows_at_delete_time, [3])
                self.assertTrue(getattr(client, "assert_before_delete_ok", False))
                self.assertEqual(deleted_batches, [["701", "702", "703"]])
                # Because messages were deleted in-game, _last_synced_unread_total resets to 0 for future arrivals
                self.assertEqual(bridge._last_synced_unread_total, 0)

                archived = bridge._db.get_game_messages()
                self.assertEqual(len(archived), 3)
                self.assertTrue(all(m["deleted_in_game"] for m in archived))
                self.assertTrue(all((m["deleted_ts"] or 0.0) > 0.0 for m in archived))

                cmd_msgs = bridge.handle_command({"command": "game_messages", "category": "spy_report"})
                self.assertTrue(cmd_msgs["ok"])
                self.assertEqual(cmd_msgs["count"], 1)
                self.assertEqual(cmd_msgs["messages"][0]["coords"], "02:381:07")
                self.assertEqual(cmd_msgs["messages"][0]["parsed"]["buildings"].get("b1"), 15)
                self.assertEqual(cmd_msgs["messages"][0]["parsed"]["researches"].get("r2"), 6)
                self.assertNotIn("TOPSECRET", cmd_msgs["messages"][0]["raw_html"])

                # Battle report set has_debris=True on 02:382:04; Recycle report cleared has_debris=False on 02:380:04
                t_war = bridge._db.get_spy_target_by_coords("02:382:04")
                t_rec = bridge._db.get_spy_target_by_coords("02:380:04")
                assert t_war is not None and t_rec is not None
                self.assertTrue(t_war["has_debris"])
                self.assertFalse(t_rec["has_debris"])

                # Own planet 02:380:12 is hidden from active get_spy_targets() via is_own_planet=1, NEVER deleted
                active_targets = {t["coords"] for t in bridge._db.get_spy_targets(limit=50)}
                self.assertNotIn("02:380:12", active_targets)
                self.assertIn("02:381:07", active_targets)
                raw_spy_target_rows = bridge._db._conn.execute(
                    "SELECT coords, merged_into, is_own_planet FROM spy_targets"
                ).fetchall()
                by_raw = {r["coords"]: (r["merged_into"], r["is_own_planet"]) for r in raw_spy_target_rows}
                self.assertEqual(by_raw["02:380:12"][1], 1)
                self.assertEqual(by_raw["2:381:7"][0], "02:381:07")

                # Repeated config updates append to config_history without deleting prior configs
                bridge.handle_command({"command": "update_config", "config": {"playstyle": "miner"}})
                bridge.handle_command({"command": "update_config", "config": {"playstyle": "fleeter"}})

                counts = bridge._db.summary()["counts"]
                self.assertEqual(counts["game_messages"], 3)
                self.assertEqual(counts["spy_reports"], 2)
                self.assertEqual(counts["galaxy_scan_history"], 2)
                self.assertGreaterEqual(counts["galaxy_slot_history"], 3)
                self.assertGreaterEqual(counts["config_history"], 2)
            finally:
                bridge.close()

    def test_dashboard_cache_mutation_isolation_deep_sig_null_planet_and_ga_nn_warm_hit(self) -> None:
        from dataclasses import fields as dc_fields
        import threading
        import requests
        import spaceinvasion.api_server as api_srv

        # Every field of StrategyConfig must be covered by _validate_config_patch
        for f in dc_fields(StrategyConfig):
            bad_val: object = [] if f.name != "shipyard_targets" else "not_a_mapping"
            with self.assertRaises(api_srv.ApiRequestError, msg=f"unvalidated StrategyConfig field: {f.name}"):
                api_srv._validate_config_patch({f.name: bad_val})

        # R2-1 & R2-2: Caller mutation isolation (including HTTP /dashboard) & deep nested state sig invalidation
        p1 = PlanetState(
            101,
            "Alpha",
            "01:042:07",
            "normal",
            25,
            20,
            220,
            Resources(15_000, 10_000, 8_000, 5_000),
            Resources(100_000, 100_000, 100_000, 100_000),
            EnergyBalance(300, 120),
            buildings={"b1": 5, "b5": 6},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 5, 6, Resources(1200, 500, 0, 0), True, "Available", "?b=1"),
            ),
        )
        st = GameState(
            31,
            7,
            "cmdr",
            1e9,
            (p1,),
            active_planet_id=101,
            raid_targets=({"coords": "01:042:08", "resources": {"spice": 15_000}},),
            raid_stats={"per_target": {"01:042:08": {"wins": 3}}},
        )
        with (
            patch("spaceinvasion.api_server.is_debug_port_open", return_value=False),
            tempfile.TemporaryDirectory() as td,
        ):
            td_path = Path(td)
            bridge = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                config_path=td_path / "c.yaml",
                db_path=None,
                log_dir=None,
                client_factory=lambda _: MagicMock(),
                ensure_session_fn=lambda: None,
            )
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                bridge._last_state = st
                bridge._rank_samples = [
                    {"ts": 100.0, "server_points": 1000, "estimated_points": 1000.0},
                    {"ts": 4000.0, "server_points": 1200, "estimated_points": 1200.0},
                ]
                d1 = bridge.dashboard(refresh=False)
                orig_n = len(d1["action_graph"]["nodes"])
                self.assertGreater(orig_n, 0)

                # Mutate returned dashboard payload aggressively in place (R2-1)
                d1["action_graph"]["nodes"].clear()
                d1["action_graph"]["is_dirty"] = True
                d1["action_graph"]["summary"]["critical_path_seconds"] = float("nan")
                d1["plan"].clear()
                d1["state"]["planets"].clear()
                d1["config"]["planner_mode"] = "CORRUPTED_MODE"

                d2 = bridge.dashboard(refresh=False)
                self.assertEqual(len(d2["action_graph"]["nodes"]), orig_n)
                self.assertFalse(d2["action_graph"]["is_dirty"])
                self.assertEqual(len(d2["plan"]), orig_n)
                self.assertEqual(len(d2["state"]["planets"]), 1)
                self.assertEqual(d2["config"]["planner_mode"], "priority")
                json.dumps(d2, allow_nan=False)

                http_dash = requests.get(f"{base_url}/dashboard?refresh=0", timeout=5)
                self.assertEqual(http_dash.status_code, 200)
                self.assertEqual(len(http_dash.json()["action_graph"]["nodes"]), orig_n)

                # Mutate nested raid_stats, raid_targets, and rank_samples[0] in place (R2-2)
                bridge._invalidate_dashboard_cache()
                _ = bridge.dashboard(refresh=False)
                bridge._last_state.raid_stats["per_target"]["01:042:08"]["wins"] = 777
                bridge._last_state.raid_targets[0]["resources"]["spice"] = 999_888
                bridge._rank_samples[0]["server_points"] = 500
                d3 = bridge.dashboard(refresh=False)
                self.assertEqual(d3["state"]["raid_stats"]["per_target"]["01:042:08"]["wins"], 777)
                self.assertEqual(d3["state"]["raid_targets"][0]["resources"]["spice"], 999_888)
                self.assertEqual(d3["ranking"]["observed_trend"]["delta_points"], 700.0)
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()

        # R2-3 & R2-4: PlanetState(..., None, None, None) safety & ga_nn warm poll cache hit without DB churn
        p_none = PlanetState(101, "C", "01:010:01", "normal", 25, 10, 200, None, None, None)  # type: ignore[arg-type]
        self.assertEqual(p_none.resources, Resources())
        self.assertEqual(p_none.storage, Resources())
        self.assertEqual(p_none.energy, EnergyBalance())

        with (
            patch("spaceinvasion.api_server.is_debug_port_open", return_value=False),
            tempfile.TemporaryDirectory() as td2,
        ):
            td2_path = Path(td2)
            bridge2 = GameApiBridge(
                debug_port=19999,
                env_file=td2_path / "no.env",
                config_path=td2_path / "c.yaml",
                db_path=td2_path / "s.db",
                log_dir=None,
                client_factory=lambda _: MagicMock(),
                ensure_session_fn=lambda: None,
            )
            try:
                bridge2._last_state = GameState(1, 1, "u", 1000.0, (p_none,), active_planet_id=101)
                d_none = bridge2.dashboard(refresh=False)
                json.dumps(d_none, allow_nan=False)
                ag_none = bridge2.handle_command({"command": "action_graph", "refresh": False})
                self.assertTrue(ag_none["ok"])
                json.dumps(ag_none, allow_nan=False)

                bridge2._last_state = st
                bridge2.strategy_config = StrategyConfig(
                    planner_mode="ga_nn",
                    ga_population_size=6,
                    ga_generations=2,
                    ga_horizon_steps=3,
                    ga_seed=42,
                )
                bridge2._invalidate_dashboard_cache()
                with (
                    patch.object(
                        bridge2, "_persist_runtime_state", wraps=bridge2._persist_runtime_state
                    ) as mock_persist,
                    patch.object(
                        api_srv, "build_action_graph", wraps=api_srv.build_action_graph
                    ) as mock_bag,
                ):
                    cold_ga = bridge2.dashboard(refresh=False)
                    self.assertEqual(cold_ga["planner_mode"], "ga_nn")
                    self.assertEqual(mock_persist.call_count, 1)
                    self.assertEqual(mock_bag.call_count, 1)
                    for _ in range(3):
                        warm_ga = bridge2.dashboard(refresh=False)
                        self.assertEqual(warm_ga["planner_mode"], "ga_nn")
                        self.assertEqual(
                            warm_ga["action_graph"]["ga_summary"]["elapsed_ms"],
                            warm_ga["optimizer_summary"]["elapsed_ms"],
                        )
                    self.assertEqual(mock_persist.call_count, 1)
                    self.assertEqual(mock_bag.call_count, 1)
            finally:
                bridge2.close()

    def test_demolish_building_command_guards_cache_invalidation_and_null_planet_offers(self) -> None:
        import requests
        import time
        from spaceinvasion.executor import ExecutionResult
        from spaceinvasion.optimizer import _LAST_OPTIMIZER_SUMMARY, run_optimizer

        # R3-N1: PlanetState(..., building_offers=None, ship_offers=None, defense_offers=None) safe in run_optimizer
        p_null_offers = PlanetState(
            101,
            "NullOffers",
            "01:042:07",
            "normal",
            25,
            12,
            220,
            Resources(15000, 10000, 8000, 5000),
            Resources(100000, 100000, 100000, 100000),
            EnergyBalance(300, 120),
            buildings={"b1": 4, "b5": 6, "b6": 1},
            building_offers=None,  # type: ignore[arg-type]
            ship_offers=None,  # type: ignore[arg-type]
            defense_offers=None,  # type: ignore[arg-type]
        )
        self.assertEqual(p_null_offers.building_offers, ())
        self.assertEqual(p_null_offers.ship_offers, ())
        self.assertEqual(p_null_offers.defense_offers, ())
        st_null_opt = GameState(31, 7, "cmdr", 1000.0, (p_null_offers,), active_planet_id=101)
        prev_global_summary = _LAST_OPTIMIZER_SUMMARY
        opt_summary = run_optimizer(
            st_null_opt,
            config=StrategyConfig(
                planner_mode="ga_nn",
                ga_population_size=6,
                ga_generations=2,
                ga_horizon_steps=3,
                ga_seed=19,
            ),
            record_global_summary=False,
        )
        self.assertIs(_LAST_OPTIMIZER_SUMMARY, prev_global_summary)
        self.assertIsInstance(opt_summary.actions, tuple)
        json.dumps(opt_summary.to_dict(), allow_nan=False)

        # R3-UI-1: POST /command {"command": "demolish_building", ...} across dry_run, guards, and live invalidation
        p_main = PlanetState(
            101,
            "Alpha",
            "01:042:07",
            "normal",
            25,
            20,
            220,
            Resources(25000, 15000, 12000, 6000),
            Resources(100000, 100000, 100000, 100000),
            EnergyBalance(500, 120),
            buildings={"b1": 4, "b2": 3, "b5": 8, "b6": 2, "b7": 2},
            economy_rates={"resource1": 80, "resource2": 70},
        )
        p_remote = PlanetState(
            202,
            "Beta",
            "01:042:09",
            "normal",
            20,
            15,
            200,
            Resources(10000, 8000, 6000, 4000),
            Resources(100000, 100000, 100000, 100000),
            EnergyBalance(300, 80),
            buildings={"b1": 3, "b5": 6, "b6": 1},
        )
        p_ast = PlanetState(
            303,
            "Rock",
            "01:042:10",
            "asteroid",
            10,
            5,
            40,
            Resources(5000, 4000, 3000, 2000),
            Resources(100000, 100000, 100000, 100000),
            EnergyBalance(100, 20),
            buildings={"b7": 2},
        )
        st_dem = GameState(
            31,
            7,
            "cmdr",
            1000.0,
            (p_main, p_remote, p_ast),
            active_planet_id=101,
            researches={"r1": 2, "r2": 5},
        )

        with (
            patch("spaceinvasion.api_server.is_debug_port_open", return_value=False),
            tempfile.TemporaryDirectory() as td,
        ):
            td_path = Path(td)
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            bridge = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                config_path=td_path / "c.yaml",
                db_path=td_path / "s.db",
                log_dir=None,
                client_factory=lambda _: fake_client,
                ensure_session_fn=lambda: None,
            )
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                bridge._record_state(st_dem, fetched_buildings=True, fetched_home=True, observed_active_planet=True)
                bridge._economy_rates[101] = {"resource1": 80, "resource2": 70}
                bridge._last_snapshot_at = time.monotonic()

                # 1. Valid b6 >= 1 dry_run=True with throttled economy_rates & r2 > 0
                r_ok = requests.post(
                    f"{base_url}/command",
                    json={"command": "demolish_building", "building_id": 6, "planet_id": 101, "dry_run": True},
                    timeout=5,
                )
                self.assertEqual(r_ok.status_code, 200, r_ok.text)
                body_ok = r_ok.json()
                self.assertTrue(body_ok["ok"])
                self.assertTrue(body_ok["dry_run"])
                self.assertFalse(body_ok["executed"])
                self.assertEqual(body_ok["action"]["kind"], "DEMOLISH_BUILDING")
                self.assertEqual(body_ok["action"]["target_id"], 6)
                self.assertEqual(body_ok["action"]["target_name"], "Thermosolar plant")
                self.assertEqual(body_ok["action"]["target_level"], 1)
                self.assertTrue(body_ok["action"]["executable_now"])
                self.assertIn("[DRY-RUN] Would execute DEMOLISH_BUILDING: Thermosolar plant -> L1", body_ok["message"])
                json.dumps(body_ok, allow_nan=False)

                # 2. General bid != 6 (Fusion plant b5 & Development center b7)
                r_b5 = requests.post(
                    f"{base_url}/command",
                    json={"command": "demolish_building", "building_id": 5, "planet_id": 101, "dry_run": True},
                    timeout=5,
                )
                self.assertEqual(r_b5.status_code, 200)
                self.assertTrue(r_b5.json()["ok"])
                self.assertTrue(r_b5.json()["action"]["executable_now"])
                self.assertEqual(r_b5.json()["action"]["target_name"], "Fusion plant")

                r_b7 = requests.post(
                    f"{base_url}/command",
                    json={"command": "demolish_building", "building_id": 7, "planet_id": 101, "dry_run": True},
                    timeout=5,
                )
                self.assertEqual(r_b7.status_code, 200)
                self.assertTrue(r_b7.json()["action"]["executable_now"])
                self.assertEqual(r_b7.json()["action"]["target_name"], "Development center")

                # 3. Missing building b99 (level 0)
                r_b99 = requests.post(
                    f"{base_url}/command",
                    json={"command": "demolish_building", "building_id": 99, "planet_id": 101, "dry_run": True},
                    timeout=5,
                )
                self.assertEqual(r_b99.status_code, 200, r_b99.text)
                body_b99 = r_b99.json()
                self.assertTrue(body_b99["ok"])
                self.assertFalse(body_b99["action"]["executable_now"])
                self.assertEqual(body_b99["action"]["target_name"], "Building 99")
                self.assertIn("already at Level 0", body_b99["action"]["reason"])

                # 4. Asteroid guard
                r_ast = requests.post(
                    f"{base_url}/command",
                    json={"command": "demolish_building", "building_id": 7, "planet_id": 303, "dry_run": True},
                    timeout=5,
                )
                self.assertEqual(r_ast.status_code, 200)
                self.assertFalse(r_ast.json()["action"]["executable_now"])
                self.assertIn("unavailable on asteroid", r_ast.json()["action"]["reason"])

                # 5. Busy building queue (b6 in queue and full 5-slot queue on 101, while remote 202 stays unblocked)
                st_busy = GameState(
                    31,
                    7,
                    "cmdr",
                    1005.0,
                    (p_main, p_remote, p_ast),
                    active_planet_id=101,
                    researches={"r1": 2, "r2": 5},
                    active_queues=tuple(
                        QueueJob(
                            category="building",
                            name="Pig iron mine",
                            building_id=1,
                            target_level=4 + idx,
                            queue_slot=idx + 1,
                        )
                        for idx in range(5)
                    ),
                )
                bridge._record_state(st_busy, fetched_buildings=True, fetched_home=True, observed_active_planet=True)
                bridge._last_snapshot_at = time.monotonic()

                r_busy_101 = requests.post(
                    f"{base_url}/command",
                    json={"command": "demolish_building", "building_id": 6, "planet_id": 101, "dry_run": True},
                    timeout=5,
                )
                self.assertEqual(r_busy_101.status_code, 200)
                self.assertFalse(r_busy_101.json()["action"]["executable_now"])
                self.assertEqual(r_busy_101.json()["action"]["reason"], "Building queue busy")

                r_free_202 = requests.post(
                    f"{base_url}/command",
                    json={"command": "demolish_building", "building_id": 6, "planet_id": 202, "dry_run": True},
                    timeout=5,
                )
                self.assertEqual(r_free_202.status_code, 200)
                self.assertTrue(r_free_202.json()["action"]["executable_now"])

                # 6. Energy-deficit guard driven by throttled mine reserve + r2 Energy tech bonus
                p_tight_e = PlanetState(
                    101,
                    "Alpha",
                    "01:042:07",
                    "normal",
                    25,
                    20,
                    220,
                    Resources(25000, 15000, 12000, 6000),
                    Resources(100000, 100000, 100000, 100000),
                    EnergyBalance(100, 50),  # net=+50, but b1=8 @ 50% reserves 49 + b6 L2 @ r2=20 loses 15 -> -14 < 0
                    buildings={"b1": 8, "b5": 4, "b6": 2},
                    economy_rates={"resource1": 50},
                )
                st_deficit = GameState(
                    31,
                    7,
                    "cmdr",
                    1010.0,
                    (p_tight_e, p_remote),
                    active_planet_id=101,
                    researches={"r2": 20},
                )
                bridge._economy_rates[101] = {"resource1": 50}
                bridge._record_state(st_deficit, fetched_buildings=True, fetched_home=True, observed_active_planet=True)
                bridge._last_snapshot_at = time.monotonic()

                r_def = requests.post(
                    f"{base_url}/command",
                    json={"command": "demolish_building", "building_id": 6, "planet_id": 101, "dry_run": True},
                    timeout=5,
                )
                self.assertEqual(r_def.status_code, 200)
                body_def = r_def.json()
                self.assertTrue(body_def["ok"])
                self.assertFalse(body_def["action"]["executable_now"])
                self.assertIn("Insufficient spare energy", body_def["action"]["reason"])

                # 7. Live dry_run=False state mutation invalidates dashboard cache and returns finite JSON
                bridge._economy_rates[101] = {"resource1": 80, "resource2": 70}
                bridge._record_state(st_dem, fetched_buildings=True, fetched_home=True, observed_active_planet=True)
                bridge._last_snapshot_at = time.monotonic()
                d_before = bridge.dashboard(refresh=False)
                self.assertEqual(d_before["state"]["planets"][0]["buildings"]["b6"], 2)

                p_main_after = PlanetState(
                    101,
                    "Alpha",
                    "01:042:07",
                    "normal",
                    25,
                    19,
                    220,
                    Resources(25000, 15000, 12000, 6000),
                    Resources(100000, 100000, 100000, 100000),
                    EnergyBalance(488, 120),
                    buildings={"b1": 4, "b2": 3, "b5": 8, "b6": 1, "b7": 2},
                    economy_rates={"resource1": 80, "resource2": 70},
                )
                st_after = GameState(
                    31,
                    7,
                    "cmdr",
                    1020.0,
                    (p_main_after, p_remote, p_ast),
                    active_planet_id=101,
                    researches={"r1": 2, "r2": 5},
                )
                with (
                    patch.object(
                        bridge._live_executor,
                        "execute_action",
                        side_effect=lambda _cli, _st, act, **_kw: ExecutionResult(
                            executed=True,
                            dry_run=False,
                            action=act,
                            message="Demolished/queued downgrade of Thermosolar plant -> L1 (current_lvl=1).",
                            updated_state=st_after,
                        ),
                    ),
                    patch.object(
                        bridge,
                        "_invalidate_dashboard_cache",
                        wraps=bridge._invalidate_dashboard_cache,
                    ) as mock_inv,
                ):
                    r_live = requests.post(
                        f"{base_url}/command",
                        json={"command": "demolish_building", "building_id": 6, "planet_id": 101, "dry_run": False},
                        timeout=5,
                    )
                self.assertEqual(r_live.status_code, 200, r_live.text)
                body_live = r_live.json()
                self.assertTrue(body_live["ok"])
                self.assertTrue(body_live["executed"])
                self.assertGreaterEqual(mock_inv.call_count, 1)
                json.dumps(body_live, allow_nan=False)

                d_after = requests.get(f"{base_url}/dashboard?refresh=0", timeout=5).json()
                self.assertEqual(d_after["state"]["planets"][0]["buildings"]["b6"], 1)
                json.dumps(body_live, allow_nan=False)
                json.dumps(d_after, allow_nan=False)
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()

    def test_intel_views_colonize_forbid_solar_and_game_messages_report_backfill(self) -> None:
        import time
        import requests
        from spaceinvasion.fleet import GalaxySlot, SpyReport
        from spaceinvasion.storage import GameDatabase

        with tempfile.TemporaryDirectory(prefix="si_intel_backfill_") as td:
            td_path = Path(td)
            db_path = td_path / "intel.db"
            cfg_path = td_path / "config.yaml"
            cfg_path.write_text(
                "planner_mode: priority\nallow_build: true\nallow_colonize: true\nmin_colony_fields: 300\n",
                encoding="utf-8",
            )

            # Seed legacy un-backfilled game_messages rows directly before reopening GameDatabase
            db_seed = GameDatabase(db_path)
            with db_seed._lock:
                db_seed._conn.execute("DELETE FROM meta WHERE key = 'reports_backfill_v2'")
                db_seed._conn.execute(
                    """
                    INSERT INTO game_messages(
                        ingested_ts, msg_uid, server_msg_id, folder_type, category, sender, subject, msg_date_text,
                        coords, body_text, raw_html, parsed_json, deleted_in_game
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        1700000010.0,
                        "uid:99001",
                        "99001",
                        "fleet",
                        "recycle_report",
                        "System",
                        "Colony [02:380:12] fleet return",
                        "11/16/2026 19:00:00",
                        "02:380:12",
                        "Pig iron 86.812 11% Message from System: A fleet returning from duty reaches Colony [02:380:12]. 17.036 Pig iron, 12.036 Metal, 14.310 Kryptonite and 18.098 Spice were unloaded. Fleet reports★",
                        "",
                        "{}",
                    ),
                )
                db_seed._conn.execute(
                    """
                    INSERT INTO game_messages(
                        ingested_ts, msg_uid, server_msg_id, folder_type, category, sender, subject, msg_date_text,
                        coords, body_text, raw_html, parsed_json, deleted_in_game
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        1700000020.0,
                        "uid:99002",
                        "99002",
                        "fleet",
                        "general",
                        "System",
                        "Space rubble [02:380:05]",
                        "11/16/2026 18:55:00",
                        "02:380:05",
                        "Pig iron 86.812 11% Message from System: 3 Recyclers with a total cargo capacity of 60000 reach their goal [02:380:05]. In space float 17036 Pig iron, 12036 Metal, 14310 Kryptonite, 18098 Spice pieces of rubble. You have processed 17036 Pig iron, 12036 Metal, 14310 Kryptonite, 18098 Spice pieces of rubble. Fleet reports★",
                        "",
                        "{}",
                    ),
                )
                db_seed._conn.execute(
                    """
                    INSERT INTO game_messages(
                        ingested_ts, msg_uid, server_msg_id, folder_type, category, sender, subject, msg_date_text,
                        coords, body_text, raw_html, parsed_json, deleted_in_game
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        1700000030.0,
                        "uid:99003",
                        "99003",
                        "battle",
                        "battle_report",
                        "System",
                        "Battle report Spacia [02:383:02] (V:0,A:0)",
                        "11/16/2026 18:40:00",
                        "02:383:02",
                        "Message from System: Battle report #16254211 Attacker ventkx86 vs Defender Spacia [02:383:02] Winner: Attacker",
                        '<div data-battle-json=\'{"ok":true,"id":16254211,"winner":1,"rounds":1,"stolen":[{"r":1,"v":"3.075"},{"r":2,"v":"2.250"},{"r":3,"v":"5.565"},{"r":4,"v":"2.550"}],"rubble":[],"attacker":{"name":"ventkx86","lost":0},"defender":{"name":"Spacia","lost":0}}\'></div>',
                        "{}",
                    ),
                )
                db_seed._conn.execute(
                    """
                    INSERT INTO game_messages(
                        ingested_ts, msg_uid, server_msg_id, folder_type, category, sender, subject, msg_date_text,
                        coords, body_text, raw_html, parsed_json, deleted_in_game
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        1700000035.0,
                        "uid:99004",
                        "99004",
                        "spio",
                        "spy_report",
                        "System",
                        "Colony [02:378:15] spy report",
                        "11/16/2026 17:09:42",
                        "02:378:15",
                        '2026-11-16 17:09:42 System Colony [02:378:15] spy report Spaceinvasion { "info": { "si": "classic", "v": 1, "type": "spy-report" }, "planet": { "id": 12447, "coords": "02:378:15", "type": "PLANET", "name": "Colony", "resources": [ 480817, 174581, 357879, 27926 ], "buildings": { "b1": { "name": "Pig iron mine", "level": 24 } }, "ships": false, "defense": false, "researches": false } }',
                        "",
                        "{}",
                    ),
                )
                db_seed._conn.commit()
            db_seed.close()

            # Reopen via GameApiBridge so startup reports_backfill_v2 runs automatically (zero row deletions!)
            p_home = PlanetState(
                planet_id=12476,
                name="Colony",
                coords="02:380:12",
                planet_type="normal",
                temperature=-5,
                fields_used=126,
                fields_max=230,
                resources=Resources(90000, 65000, 45000, 25000),
                storage=Resources(500000, 500000, 500000, 500000),
                energy=EnergyBalance(5500, 1500),
                buildings={"b1": 19, "b2": 18, "b5": 21, "b6": 10, "b13": 6},
                ships={"s1": 12, "s8": 1, "s10": 11, "s11": 4},
                building_offers=_sample_state().planets[0].building_offers,
            )
            st = GameState(
                universe_id=31,
                user_id=103603,
                nickname="ventkx86",
                timestamp=1700000100.0,
                planets=(p_home,),
                active_planet_id=12476,
                researches={"r1": 4, "r2": 8, "r3": 6, "r6": 3},
                speedup_factor=10.0,
            )
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=tok")
            fake_client.read_full_snapshot.return_value = st

            bridge = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                config_path=cfg_path,
                db_path=db_path,
                log_dir=None,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            assert bridge._db is not None
            bridge._record_state(st, fetched_buildings=True, fetched_home=True, observed_active_planet=True)
            bridge._last_snapshot_at = time.monotonic()

            # Seed scanned free slots + spied 325-field slot + bash block
            slots = [
                GalaxySlot("02:380:05", 5, name="DebrisTarget", owner="Pirate", status_color="yellow", has_debris=True),
                GalaxySlot("02:380:09", 9, status_color="free"),
                GalaxySlot("02:380:10", 10, status_color="free"),
                GalaxySlot("02:378:15", 15, name="Colony", owner="Rival", status_color="white"),
            ]
            bridge._db.record_galaxy_scan(2, 380, slots[:3], ts=1700000050.0)
            bridge._db.record_galaxy_slots(slots, ts=1700000050.0)
            bridge._db.record_spy_report(
                SpyReport(
                    target_coords="02:380:09",
                    owner="",
                    resources=Resources(),
                    uninhabited_fields=325,
                ),
                status_color="free",
                ts=1700000060.0,
            )
            bridge._db.record_spy_report(
                SpyReport(
                    target_coords="02:383:02",
                    owner="Spacia",
                    planet_name="Spacia",
                    is_spacia=True,
                    resources=Resources(60000, 40000, 30000, 20000),
                ),
                ts=1700000070.0,
            )
            bridge._db.record_target_block("02:378:15", blocked_until_ts=time.time() + 3600.0, reason="bash protection", is_bash=True)
            bridge._db.record_raid("02:383:02", is_exempt=True, outcome="win", loot={"pig_iron": 3075, "spice": 2550})

            server = create_api_server(bridge, host="127.0.0.1", port=0)
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                # Verify all 4 game_messages rows remain preserved (append-only, 0 deleted)
                self.assertEqual(bridge._db.summary()["counts"]["game_messages"], 4)

                r_cfg = requests.post(
                    f"{base_url}/command",
                    json={"command": "update_config", "config": {"forbid_planetary_solar": True}},
                    timeout=5,
                )
                self.assertEqual(r_cfg.status_code, 200, r_cfg.text)
                self.assertTrue(r_cfg.json()["config"]["forbid_planetary_solar"])

                r_esp = requests.post(f"{base_url}/command", json={"command": "espionage_intel"}, timeout=5).json()
                self.assertTrue(r_esp["ok"])
                self.assertGreaterEqual(r_esp["summary"]["spied_targets"], 2)
                self.assertGreaterEqual(r_esp["summary"]["spacia_count"], 1)

                r_col = requests.post(f"{base_url}/command", json={"command": "colony_candidates"}, timeout=5).json()
                self.assertTrue(r_col["ok"])
                self.assertEqual(r_col["min_colony_fields"], 300)
                self.assertEqual(r_col["qualified_count"], 1)
                by_coords = {c["coords"]: c for c in r_col["candidates"]}
                self.assertIn(by_coords["02:380:09"]["grade"], ("S", "A+"))
                self.assertTrue(by_coords["02:380:09"]["probed"])
                self.assertEqual(by_coords["02:380:10"]["grade"], "?")
                self.assertFalse(by_coords["02:380:10"]["probed"])

                r_atk = requests.post(f"{base_url}/command", json={"command": "attack_results"}, timeout=5).json()
                self.assertTrue(r_atk["ok"])
                self.assertEqual(r_atk["summary"]["total_battles"], 1)
                self.assertEqual(r_atk["summary"]["wins"], 1)
                self.assertGreater(r_atk["battles"][0]["loot"]["spice_eq"], 5000.0)
                self.assertEqual(r_atk["raid_stats"]["total_24h"], 1)
                self.assertGreaterEqual(r_atk["summary"]["active_blocks_count"], 1)

                r_rec = requests.post(f"{base_url}/command", json={"command": "recycler_results"}, timeout=5).json()
                self.assertTrue(r_rec["ok"])
                self.assertEqual(r_rec["summary"]["total_runs"], 1)
                self.assertEqual(r_rec["summary"]["total_pig_iron"], 17036)
                self.assertEqual(r_rec["summary"]["total_metal"], 12036)
                self.assertEqual(r_rec["summary"]["total_kryptonite"], 14310)
                self.assertEqual(r_rec["summary"]["total_spice"], 18098)
                # Fleet return was reclassified out of recycle_reports and appears under other_emails!
                self.assertEqual(len(r_rec["other_emails"]), 1)
                self.assertEqual(r_rec["other_emails"][0]["category"], "fleet_return")
                self.assertNotIn("86.812", r_rec["other_emails"][0]["body_text"])

                with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                    r_col_sim = requests.post(
                        f"{base_url}/command",
                        json={"command": "colonize", "target_coords": "02:380:09", "planet_id": 12476, "dry_run": True},
                        timeout=5,
                    ).json()
                self.assertTrue(r_col_sim["ok"])
                self.assertTrue(r_col_sim["dry_run"])
                self.assertEqual(r_col_sim["action"]["kind"], "COLONIZE")
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()

    def test_manual_queue_api_crud_reprioritize_commander_tick_execution_and_append_only_events(self) -> None:
        from dataclasses import replace
        import time
        import requests
        from commander import run_commander_tick
        from spaceinvasion.commander_client import GameApiClient
        from spaceinvasion.executor import ExecutionResult

        p_home = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=15,
            fields_used=55,
            fields_max=230,
            resources=Resources(95_000, 80_000, 65_000, 45_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(2200, 900),
            buildings={"b1": 10, "b2": 9, "b3": 8, "b4": 7, "b5": 15, "b7": 5, "b8": 4, "b9": 4},
            ships={"s1": 6, "s8": 1, "s10": 8, "s11": 2},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 10, 11, Resources(10_000, 5_000, 0, 0), True, "Build", "?b=1"),
                BuildingOffer(4, "Spice mine", 7, 8, Resources(15_000, 12_000, 8_000, 0), True, "Build", "?b=4"),
            ),
        )
        st = GameState(
            universe_id=31,
            user_id=103603,
            nickname="ventkx86",
            timestamp=1700000500.0,
            planets=(p_home,),
            active_planet_id=12476,
            researches={"r1": 3, "r2": 6, "r6": 3},
            speedup_factor=10.0,
        )

        with tempfile.TemporaryDirectory(prefix="si_mq_api_") as td:
            td_path = Path(td)
            cfg_path = td_path / "config.yaml"
            cfg_path.write_text("planner_mode: priority\nallow_build: true\n", encoding="utf-8")
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=tok")
            fake_client.read_full_snapshot.return_value = st
            fake_client.navigate_menu.return_value = "<html>ok</html>"

            bridge = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                config_path=cfg_path,
                db_path=td_path / "mq.db",
                log_dir=None,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            assert bridge._db is not None
            bridge._record_state(st, fetched_buildings=True, fetched_home=True, observed_active_planet=True)
            bridge._last_snapshot_at = time.monotonic()

            server = create_api_server(bridge, host="127.0.0.1", port=0)
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                r1 = requests.post(
                    f"{base_url}/command",
                    json={"command": "queue_add", "kind": "SPY", "priority": "P2", "target_coords": "02:381:12", "target_level": 1},
                    timeout=5,
                ).json()
                self.assertTrue(r1["ok"])
                qid_spy = r1["item"]["id"]

                r2 = requests.post(
                    f"{base_url}/command",
                    json={"command": "queue_add", "kind": "BUILD", "priority": "P2", "position": "bottom", "planet_id": 12476, "target_id": 4, "target_level": 8},
                    timeout=5,
                ).json()
                self.assertTrue(r2["ok"])
                qid_bld = r2["item"]["id"]
                self.assertEqual([x["id"] for x in r2["manual_queue"]], [qid_spy, qid_bld])

                r_upd = requests.post(
                    f"{base_url}/command",
                    json={"command": "queue_update", "queue_id": qid_bld, "priority": "P0", "move": "top"},
                    timeout=5,
                ).json()
                self.assertTrue(r_upd["ok"])
                self.assertEqual([x["id"] for x in r_upd["manual_queue"]], [qid_bld, qid_spy])

                dash = requests.get(f"{base_url}/dashboard?refresh=0", timeout=5).json()
                self.assertEqual(dash["plan"][0]["manual_queue_id"], qid_bld)
                self.assertEqual(dash["plan"][0]["priority"], "P0")
                self.assertEqual(dash["plan"][0]["target_id"], 4)

                bot_st = requests.get(f"{base_url}/bot_status", timeout=5).json()
                self.assertEqual([x["id"] for x in bot_st["manual_queue"]], [qid_bld, qid_spy])

                p_after = replace(p_home, buildings={**p_home.buildings, "b4": 8})
                st_after = replace(st, planets=(p_after,))

                def _mock_live_exec(_client, _st, act, **_kw):
                    return ExecutionResult(
                        executed=True,
                        dry_run=False,
                        action=act,
                        message=f"Built {act.target_name}",
                        updated_state=st_after,
                    )

                with (
                    patch("spaceinvasion.api_server.is_debug_port_open", return_value=True),
                    patch.object(bridge._live_executor, "execute_action", side_effect=_mock_live_exec),
                    GameApiClient(base_url=base_url) as api_cli,
                ):
                    _, actual_act, actual_res, _, _ = run_commander_tick(
                        api_cli,
                        live=True,
                        max_actions=1,
                        config=bridge.strategy_config,
                        include_buildings=False,
                        manual_queue=bot_st["manual_queue"],
                    )
                self.assertIsNotNone(actual_act)
                self.assertEqual(actual_act.manual_queue_id, qid_bld)
                self.assertTrue(actual_res["executed"])

                r_canc = requests.post(
                    f"{base_url}/command",
                    json={"command": "queue_cancel", "queue_id": qid_spy},
                    timeout=5,
                ).json()
                self.assertTrue(r_canc["ok"])
                self.assertTrue(r_canc["canceled"])
                self.assertEqual(r_canc["manual_queue"], [])

                q_list = requests.post(f"{base_url}/command", json={"command": "queue_list"}, timeout=5).json()
                self.assertEqual(len(q_list["manual_queue"]), 0)
                statuses = {h["id"]: h["status"] for h in q_list["history"]}
                self.assertEqual(statuses[qid_bld], "completed")
                self.assertEqual(statuses[qid_spy], "canceled")
                self.assertEqual(bridge._db.summary()["counts"]["manual_queue"], 2)
                self.assertGreaterEqual(bridge._db.summary()["counts"]["manual_queue_events"], 4)
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()

    def test_static_assets_traversal_guard_attack_free_cooldown_and_universe_research_sync(self) -> None:
        import time
        import requests
        from spaceinvasion.executor import ExecutionResult
        from spaceinvasion.fleet import GalaxySlot
        from spaceinvasion.runtime import api_server as api_mod

        with tempfile.TemporaryDirectory(prefix="si_api_assets_") as tmp:
            tp = Path(tmp)
            fake_ui = tp / "ui"
            (fake_ui / "assets").mkdir(parents=True)
            (fake_ui / "assets" / "chunk-test.js").write_text("export const x = 42;\n", encoding="utf-8")
            (tp / "secret.txt").write_text("TOP_SECRET", encoding="utf-8")

            cfg_path = tp / "config.yaml"
            cfg_path.write_text("allow_raids: true\nallow_galaxy_scan: true\n", encoding="utf-8")
            st = _sample_state()
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=tok")
            fake_client.read_full_snapshot.return_value = st
            fake_client.navigate_menu.return_value = (
                "<html><body><table><tr><td>Mining technology</td><td>21</td><td>TopMiner [01:015:08]</td></tr></table></body></html>"
            )

            bridge = GameApiBridge(
                debug_port=19999,
                config_path=cfg_path,
                db_path=tp / "api_assets.db",
                log_dir=None,
                client_factory=lambda _p: fake_client,
                ensure_session_fn=lambda: None,
            )
            with patch.object(api_mod, "UI_DIR", fake_ui):
                server = create_api_server(bridge, host="127.0.0.1", port=0)
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                threading.Thread(target=server.serve_forever, daemon=True).start()
                try:
                    # 1. /assets/<name> serves valid chunk and rejects traversal
                    ok_js = requests.get(f"{base_url}/assets/chunk-test.js", timeout=5)
                    self.assertEqual(ok_js.status_code, 200)
                    self.assertIn("javascript", ok_js.headers.get("Content-Type", ""))
                    self.assertEqual(ok_js.text, "export const x = 42;\n")

                    for bad_path in ("/assets/../secret.txt", "/assets/..%2Fsecret.txt", "/assets/", "/assets/a\\b.js"):
                        r_bad = requests.get(f"{base_url}{bad_path}", timeout=5)
                        self.assertIn(r_bad.status_code, (400, 404), bad_path)
                        self.assertNotIn("TOP_SECRET", r_bad.text)

                    # 2. attack rejects free/ownerless slots & 6h target protection cooldowns before touching Selenium
                    assert bridge._db is not None
                    bridge._last_state = st
                    bridge._last_snapshot_at = time.monotonic()
                    bridge._db.record_galaxy_slots(
                        [
                            GalaxySlot("02:381:08", 8, status_color="free"),
                            GalaxySlot("02:381:06", 6, name="Fort", owner="IdleUser", status_color="yellow"),
                        ],
                        ts=time.time(),
                    )
                    atk_free = requests.post(
                        f"{base_url}/command",
                        json={"command": "attack", "target_coords": "02:381:08"},
                        timeout=5,
                    ).json()
                    self.assertFalse(atk_free["executed"])
                    self.assertIn("uninhabited", atk_free["message"].lower())

                    # Simulate Step 2 missing ATTACK radio on 02:381:06 -> persists 6h block WITHOUT UI notification spam
                    unread_prot_before = bridge._db.unread_notification_count()

                    def _no_atk_radio(_c, _s, act, **_kw):
                        return ExecutionResult(
                            action=act,
                            executed=False,
                            validated=False,
                            dry_run=False,
                            message="Aborted: Target cannot be attacked on internalSendFleet (ATTACK mission unavailable).",
                            validation_error="Target cannot be attacked on internalSendFleet (ATTACK mission unavailable)",
                            target_cooldown_seconds=6 * 3600.0,
                            target_block_reason="Target cannot be attacked on internalSendFleet (ATTACK mission unavailable)",
                        )

                    with (
                        patch("spaceinvasion.api_server.is_debug_port_open", return_value=True),
                        patch.object(bridge._live_executor, "execute_action", side_effect=_no_atk_radio) as mock_ex,
                    ):
                        atk1 = requests.post(
                            f"{base_url}/command",
                            json={"command": "attack", "target_coords": "02:381:06"},
                            timeout=5,
                        ).json()
                        self.assertFalse(atk1["executed"])
                        self.assertEqual(mock_ex.call_count, 1)
                        self.assertEqual(bridge._db.unread_notification_count(), unread_prot_before)

                        # Immediate retry is blocked by persisted cooldown WITHOUT calling executor!
                        atk2 = requests.post(
                            f"{base_url}/command",
                            json={"command": "attack", "target_coords": "02:381:06"},
                            timeout=5,
                        ).json()
                        self.assertFalse(atk2["executed"])
                        self.assertIn("cooldown", atk2["message"].lower())
                        self.assertEqual(mock_ex.call_count, 1)

                    # Free-slot SPY probe rejection records 6h cooldown silently WITHOUT unread popup spam
                    unread_before = bridge._db.unread_notification_count()

                    def _free_spy_reject(_c, _s, act, **_kw):
                        return ExecutionResult(
                            action=act,
                            executed=False,
                            validated=False,
                            dry_run=False,
                            message="Aborted: Spy mission unavailable on free slot.",
                            validation_error="Spy mission unavailable on free slot",
                            target_cooldown_seconds=6 * 3600.0,
                            target_block_reason="Spy mission unavailable on free slot",
                        )

                    with (
                        patch("spaceinvasion.api_server.is_debug_port_open", return_value=True),
                        patch.object(bridge._live_executor, "execute_action", side_effect=_free_spy_reject),
                    ):
                        spy_free = requests.post(
                            f"{base_url}/command",
                            json={"command": "spy", "target_coords": "02:381:08"},
                            timeout=5,
                        ).json()
                    self.assertFalse(spy_free["executed"])
                    self.assertEqual(bridge._db.unread_notification_count(), unread_before)

                    # 3. universe_research_max sync merges live server records into SQLite and dashboard
                    with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                        uni_res = requests.post(
                            f"{base_url}/command",
                            json={"command": "universe_research_max", "sync": True},
                            timeout=5,
                        ).json()
                    self.assertTrue(uni_res["ok"])
                    self.assertTrue(uni_res["synced"])
                    r1_row = next(r for r in uni_res["rows"] if r["key"] == "r1")
                    self.assertEqual(r1_row["universe_max_level"], 21)
                    self.assertEqual(r1_row["holder"], "TopMiner")
                    dash = requests.get(f"{base_url}/dashboard?refresh=0", timeout=5).json()
                    self.assertIn("universe_research_max", dash)

                    # 4. _ingest_and_delete_page_messages must never persist fleet-return notices as fake Spacia spy reports
                    fleet_ret_html = """
                    <html><body><table>
                      <tr id="msg_7881299569787197">
                        <td><input type="checkbox" id="check_7881299569787197" name="check_7881299569787197" value="7881299569787197" /></td>
                        <td>2026-11-17 14:19:26</td>
                        <td>System</td>
                        <td>
                          Colony [02:380:12] fleet return
                          <div class="si-expanded-body" data-si-expanded="1">
                            02:380:12 Colony [02:380:12] Colony Pig iron 185.293 6% Metal 7.790 1% Kryptonite 656.686 41% Spice 551.163 69%
                            Message from System: Colony [02:380:12] fleet return One of your fleets (46 Recycler) is returning from duty
                            from Spacia Planet [02:380:14] to its home planet Colony [02:380:12]. The assignment was to: Recycle.
                            0 Pig iron, 0 Metal, 0 Kryptonite, 0 Spice were unloaded. ' Fleet reports&#9733; Favourites
                          </div>
                        </td>
                      </tr>
                    </table></body></html>
                    """
                    reps_ing, _cnt_ing, _del_ing, _post_ing = bridge._ingest_and_delete_page_messages(
                        MagicMock(),
                        fleet_ret_html,
                        folder_type="inbox",
                        own_coords={"02:380:12"},
                        delete_after_persist=False,
                    )
                    self.assertEqual(reps_ing, [])
                    self.assertIsNone(bridge._db.get_spy_target_by_coords("02:380:14"))
                finally:
                    server.shutdown()
                    server.server_close()
                    bridge.close()

    def test_transport_api_command_commander_payload_and_4tier_slot_holds(self) -> None:
        """Verify TRANSPORT API command, commander payload, config sync, and 4-tier slot reservation holds."""
        from spaceinvasion.executor import ExecutionResult
        from spaceinvasion.state import QueueJob
        from spaceinvasion.strategy import PlannedAction

        # 1. Commander payload mapping for TRANSPORT
        tr_act = PlannedAction(
            priority="P1",
            kind="TRANSPORT",
            planet_id=101,
            planet_coords="02:380:01",
            target_id=102,
            target_name="ColonyB [02:380:02]",
            cost=Resources(12_000, 6_000, 2_000, 0),
            executable_now=True,
            reason="Supply ColonyB",
        )
        payload = commander._action_to_command_payload(tr_act, dry_run=False)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["command"], "transport")
        self.assertEqual(payload["planet_id"], 101)
        self.assertEqual(payload["dest_planet_id"], 102)
        self.assertEqual(payload["target_coords"], "02:380:02")
        self.assertEqual(
            payload["resources"],
            {"pig_iron": 12_000, "metal": 6_000, "kryptonite": 2_000, "spice": 0},
        )

        # 2. Bridge API transport validation, allow_transport config sync, dry-run & live mock execution
        p1 = PlanetState(
            planet_id=101,
            name="Hub",
            coords="02:380:01",
            planet_type="planet",
            temperature=20,
            fields_used=50,
            fields_max=300,
            resources=Resources(100_000, 80_000, 50_000, 20_000),
            storage=Resources(300_000, 300_000, 300_000, 300_000),
            energy=EnergyBalance(500, 200),
            buildings={"b10": 2},  # 3 total fleet slots
            ships={"s2": 10, "s10": 10, "s11": 5, "s12": 1, "s4": 40},
        )
        p2 = PlanetState(
            planet_id=102,
            name="ColonyB",
            coords="02:380:02",
            planet_type="planet",
            temperature=15,
            fields_used=10,
            fields_max=300,
            resources=Resources(500, 500, 200, 100),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(100, 40),
            buildings={},
            ships={},
        )
        st = GameState(
            universe_id=31,
            user_id=1,
            nickname="cmdr",
            timestamp=2000.0,
            planets=(p1, p2),
            active_planet_id=101,
            active_queues=(QueueJob(category="fleet", name="Attack → 02:380:14", remaining_seconds=120.0, detail="Outbound"),),
            researches={"r4": 4, "r5": 4},
        )
        with tempfile.TemporaryDirectory(prefix="si_tr_api_") as tmp:
            tmp_path = Path(tmp)
            mock_cli = MagicMock()
            mock_cli.__enter__.return_value = mock_cli
            mock_cli.read_full_snapshot.return_value = st
            mock_cli.read_snapshot.return_value = st
            mock_cli.read_planets_overview.return_value = st
            mock_cli.last_snapshot_observed_active_planet = True
            bridge = GameApiBridge(
                debug_port=1,
                client_factory=lambda _port: mock_cli,
                config_path=tmp_path / "config.yaml",
                db_path=tmp_path / "state.db",
                log_dir=tmp_path / "logs",
                ensure_session_fn=lambda: None,
            )
            try:
                bridge._record_state(st)
                self.assertFalse(bridge.strategy_config.allow_transport)
                self.assertFalse(bridge._live_executor.allow_transport)

                # Reject non-owned coordinates
                res_foreign = bridge.handle_command(
                    {
                        "command": "transport",
                        "planet_id": 101,
                        "target_coords": "02:380:09",
                        "resources": {"pig_iron": 5000},
                        "dry_run": True,
                    }
                )
                self.assertTrue(res_foreign["ok"])
                self.assertFalse(res_foreign["executed"])
                self.assertIn("own planet", res_foreign["message"].lower())

                # Reject same origin/destination
                res_same = bridge.handle_command(
                    {
                        "command": "transport",
                        "planet_id": 101,
                        "target_coords": "02:380:01",
                        "resources": {"pig_iron": 5000},
                        "dry_run": True,
                    }
                )
                self.assertTrue(res_same["ok"])
                self.assertFalse(res_same["executed"])
                self.assertIn("same planet", res_same["message"].lower())

                # Live transport blocked while allow_transport=False
                res_blocked = bridge.handle_command(
                    {
                        "command": "transport",
                        "planet_id": 101,
                        "target_coords": "02:380:02",
                        "resources": {"pig_iron": 5000},
                        "dry_run": False,
                    }
                )
                self.assertTrue(res_blocked["ok"])
                self.assertFalse(res_blocked["executed"])
                self.assertIn("allow_transport", res_blocked["message"])

                # update_config enables allow_transport & syncs both executors
                upd = bridge.handle_command({"command": "update_config", "config": {"allow_transport": True}})
                self.assertTrue(upd["ok"])
                self.assertTrue(bridge.strategy_config.allow_transport)
                self.assertTrue(bridge._live_executor.allow_transport)
                self.assertTrue(bridge._dry_executor.allow_transport)

                # Dry-run transport succeeds without touching Chrome
                res_dry = bridge.handle_command(
                    {
                        "command": "transport",
                        "planet_id": 101,
                        "target_coords": "02:380:02",
                        "resources": {"pig_iron": 5000, "metal": 2000},
                        "dry_run": True,
                    }
                )
                self.assertTrue(res_dry["ok"])
                self.assertTrue(res_dry["dry_run"])

                # Live transport delegates to _live_executor when allow_transport=True
                def _ok_tr(_c, _s, act, **_kw):
                    return ExecutionResult(
                        action=act,
                        executed=True,
                        validated=True,
                        dry_run=False,
                        message="Dispatched Transport to 02:380:02",
                    )

                with (
                    patch("spaceinvasion.api_server.is_debug_port_open", return_value=True),
                    patch.object(bridge._live_executor, "execute_action", side_effect=_ok_tr),
                ):
                    res_live = bridge.handle_command(
                        {
                            "command": "transport",
                            "planet_id": 101,
                            "target_coords": "02:380:02",
                            "resources": {"pig_iron": 5000},
                            "dry_run": False,
                        }
                    )
                self.assertTrue(res_live["ok"])
                self.assertTrue(res_live["executed"])

                # 3. Verify 4-tier fleet slot holds: b10=2 (3 slots), 1 in flight -> 2 free slots.
                # SPY reserves 1 -> TRANSPORT gets 1 -> COLONIZE, RECYCLE, ATTACK all held!
                sample_actions = [
                    PlannedAction(priority="P1", kind="SPY", planet_id=101, planet_coords="02:380:01", target_name="02:380:05", executable_now=True, reason="Spy"),
                    PlannedAction(priority="P1", kind="TRANSPORT", planet_id=101, planet_coords="02:380:01", target_id=102, target_name="ColonyB [02:380:02]", cost=Resources(5000, 0, 0, 0), executable_now=True, reason="Supply"),
                    PlannedAction(priority="P1", kind="COLONIZE", planet_id=101, planet_coords="02:380:01", target_name="02:380:07", executable_now=True, reason="Colonize"),
                    PlannedAction(priority="P2", kind="RECYCLE", planet_id=101, planet_coords="02:380:01", target_name="Rubble -> 02:380:04", executable_now=True, reason="Recycle"),
                    PlannedAction(priority="P2", kind="ATTACK", planet_id=101, planet_coords="02:380:01", target_name="02:380:03", executable_now=True, reason="Attack"),
                ]
                held = bridge._apply_fleet_slot_holds(st, sample_actions)
                by_kind = {a.kind: a for a in held}
                self.assertTrue(by_kind["SPY"].executable_now)
                self.assertTrue(by_kind["TRANSPORT"].executable_now)
                self.assertFalse(by_kind["COLONIZE"].executable_now)
                self.assertFalse(by_kind["RECYCLE"].executable_now)
                self.assertFalse(by_kind["ATTACK"].executable_now)
                # Relative order between RECYCLE and ATTACK preserved (RECYCLE still before ATTACK)!
                kinds_order = [a.kind for a in held]
                self.assertLess(kinds_order.index("RECYCLE"), kinds_order.index("ATTACK"))
            finally:
                bridge.close()

    def test_broken_pipe_client_disconnect_suppressed_cleanly(self) -> None:
        """Client disconnecting mid-response (GET /dashboard or POST /command) must not print BrokenPipeError tracebacks."""
        import contextlib
        import io
        import socket
        import time

        entered = threading.Event()
        release = threading.Event()
        bridge = MagicMock()
        bridge.log_dir = None

        def _slow_dashboard(refresh: bool = False) -> dict[str, Any]:
            entered.set()
            release.wait(timeout=2.0)
            return {"ok": True, "blob": "x" * 65536}

        bridge.dashboard.side_effect = _slow_dashboard
        bridge.health.return_value = {"ok": True}
        bridge.handle_command.side_effect = lambda _p: _slow_dashboard(False)

        server = create_api_server(bridge, host="127.0.0.1", port=0)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        err_buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(err_buf):
                # 1. GET /dashboard client drops connection before headers/body write
                with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
                    sock.sendall(b"GET /dashboard?refresh=0 HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
                    self.assertTrue(entered.wait(timeout=2.0))
                # Socket is now closed; release handler to attempt write -> BrokenPipeError/ConnectionResetError
                release.set()
                time.sleep(0.1)

                # 2. POST /command client drops connection before response write
                entered.clear()
                release.clear()
                body_bytes = b'{"command":"plan"}'
                with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
                    sock.sendall(
                        f"POST /command HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: {len(body_bytes)}\r\nConnection: close\r\n\r\n".encode("ascii")
                        + body_bytes
                    )
                    self.assertTrue(entered.wait(timeout=2.0))
                release.set()
                time.sleep(0.1)

            stderr_txt = err_buf.getvalue()
            self.assertNotIn("BrokenPipeError", stderr_txt)
            self.assertNotIn("ConnectionResetError", stderr_txt)
            self.assertNotIn("Traceback", stderr_txt)

            # 3. Server remains healthy for subsequent requests
            import json as _json
            import urllib.request as _urlreq

            with _urlreq.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertTrue(_json.loads(resp.read().decode("utf-8"))["ok"])
        finally:
            release.set()
            server.shutdown()
            server.server_close()

    def test_reloader_ignores_mtime_only_touch_without_content_change(self) -> None:
        import os
        import sys
        import time
        from spaceinvasion.reloader import watch_and_run

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            watched = tp / "watched.py"
            watched.write_text("V = 1\n", encoding="utf-8")
            pids_log = tp / "pids.txt"
            ready = tp / "ready.flag"
            exit_flag = tp / "exit.flag"

            child_code = (
                "import os, pathlib, sys, time\n"
                f"open({str(pids_log)!r}, 'a', encoding='utf-8').write(f'{{os.getpid()}}\\n')\n"
                f"pathlib.Path({str(ready)!r}).write_text('1', encoding='utf-8')\n"
                "deadline = time.monotonic() + 10.0\n"
                f"exit_p = pathlib.Path({str(exit_flag)!r})\n"
                "while not exit_p.exists() and time.monotonic() < deadline:\n"
                "    time.sleep(0.03)\n"
                "sys.exit(0)\n"
            )

            def _touch_then_exit() -> None:
                deadline = time.monotonic() + 5.0
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                st = watched.stat()
                os.utime(watched, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
                time.sleep(0.3)
                exit_flag.write_text("done", encoding="utf-8")

            worker = threading.Thread(target=_touch_then_exit, daemon=True)
            worker.start()
            rc = watch_and_run(
                label="TEST_TOUCH",
                cmd=[sys.executable, "-c", child_code],
                watch_paths=[watched],
                poll_interval=0.05,
                debounce_seconds=0.05,
            )
            worker.join(timeout=5.0)
            self.assertEqual(rc, 0)
            pids = [int(x) for x in pids_log.read_text(encoding="utf-8").splitlines() if x.strip()]
            self.assertEqual(len(pids), 1)

    def test_health_bot_status_and_dashboard_return_fast_when_lock_busy(self) -> None:
        import time

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            bridge = GameApiBridge(
                debug_port=9222,
                log_dir=None,
                config_path=tp / "cfg.yaml",
                db_path=tp / "test.db",
            )
            try:
                with patch("spaceinvasion.runtime.api_server.is_debug_port_open", return_value=True):
                    bridge._last_state = _sample_state()
                    bridge._last_snapshot_at = time.monotonic()
                    bridge._last_bot_control_payload = bridge.get_bot_control()
                    bridge._last_dashboard_payload = bridge.dashboard(refresh=False)

                    locked = threading.Event()
                    release = threading.Event()

                    def _hold_lock() -> None:
                        with bridge._lock:
                            locked.set()
                            release.wait(timeout=10.0)

                    holder = threading.Thread(target=_hold_lock, daemon=True)
                    holder.start()
                    self.assertTrue(locked.wait(timeout=2.0))
                    try:
                        h = bridge.health()
                        self.assertTrue(h["ok"])
                        self.assertTrue(h["busy"])

                        bc = bridge.get_bot_control()
                        self.assertTrue(bc["ok"])
                        self.assertTrue(bc["busy"])

                        dash = bridge.dashboard(refresh=False)
                        self.assertTrue(dash["ok"])
                        self.assertTrue(dash["busy"])
                    finally:
                        release.set()
                        holder.join(timeout=3.0)
            finally:
                bridge.close()

    def test_station_command_per_planet_slot_holds_and_commander_mapping(self) -> None:
        """Verify POST /command station (dry-run, live, own-planet/raid/s9/allow_transport guards),
        strictly per-planet _apply_fleet_slot_holds + Tier #3.5 STATION reservation, and commander.py STATION mapping."""
        from spaceinvasion.api_server import ApiRequestError
        from spaceinvasion.session.executor import ExecutionResult
        from spaceinvasion.strategy import PlannedAction

        # 1. commander.py _action_to_command_payload maps STATION with parsed ships dict + _hold_busy_fleet_slots holds STATION
        st_action = PlannedAction(
            priority="P2",
            kind="STATION",
            planet_id=101,
            planet_coords="02:380:12",
            target_id=102,
            target_level=16,
            target_name="Station -> 02:380:08 (ColonyA) [10x s11, 6x s4]",
            reason="One-way deploy",
            executable_now=True,
        )
        payload = commander._action_to_command_payload(st_action, dry_run=True)
        self.assertEqual(payload["command"], "station")
        self.assertEqual(payload["planet_id"], 101)
        self.assertEqual(payload["dest_planet_id"], 102)
        self.assertEqual(payload["target_coords"], "02:380:08")
        self.assertEqual(payload["ships"], {"s11": 10, "s4": 6})
        self.assertTrue(payload["dry_run"])

        # _hold_busy_fleet_slots holds STATION when fleet slots are busy on that planet
        p1_busy = PlanetState(
            planet_id=101,
            name="Home",
            coords="02:380:12",
            planet_type="normal",
            temperature=25,
            fields_used=60,
            fields_max=230,
            resources=Resources(50_000, 50_000, 30_000, 5_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(500, 200),
            buildings={"b1": 14, "b2": 12, "b3": 12, "b4": 10, "b9": 4, "b10": 1},  # 2 slots
            ships={"s11": 20, "s4": 15, "s10": 5, "s9": 10},
        )
        p2_free = PlanetState(
            planet_id=102,
            name="ColonyA",
            coords="02:380:08",
            planet_type="normal",
            temperature=20,
            fields_used=30,
            fields_max=320,
            resources=Resources(20_000, 20_000, 10_000, 5_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(300, 100),
            buildings={"b1": 8, "b2": 7, "b3": 8, "b4": 6, "b10": 2},  # 3 slots free!
            ships={"s11": 5, "s4": 10},
        )
        p3_raid = PlanetState(
            planet_id=103,
            name="RaidCol",
            coords="02:380:09",
            planet_type="raid",
            temperature=15,
            fields_used=5,
            fields_max=100,
            resources=Resources(1000, 1000, 1000, 1000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(50, 10),
            buildings={"b10": 2},
            ships={},
        )
        st_two = GameState(
            universe_id=31,
            user_id=1,
            nickname="cmdr",
            timestamp=2000.0,
            planets=(p1_busy, p2_free, p3_raid),
            active_planet_id=101,
            active_queues=(
                QueueJob(category="fleet", name="Transport → 02:380:08", remaining_seconds=120.0, detail="Outbound", origin_coords="02:380:12"),
                QueueJob(category="fleet", name="Attack → 02:380:15", remaining_seconds=180.0, detail="Outbound", origin_coords="02:380:12"),
            ),
        )
        held_cmd = commander._hold_busy_fleet_slots([st_action], {101: 120.0})
        self.assertFalse(held_cmd[0].executable_now)

        # 2. GameApiBridge POST /command {"command": "station"} guards + dry_run + live dispatch
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.read_full_snapshot.return_value = st_two
        fake_client.read_planets_overview.return_value = st_two

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=tp / "config.yaml",
                log_dir=tp / "logs",
                db_path=tp / "test.db",
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            try:
                bridge._last_state = st_two

                # Reject foreign target coords (not an owned planet) with clean aborted payload
                res_foreign = bridge.handle_command(
                    {
                        "command": "station",
                        "planet_id": 101,
                        "target_coords": "02:380:15",
                        "ships": {"s11": 5},
                        "dry_run": True,
                    }
                )
                self.assertFalse(res_foreign["executed"])
                self.assertIn("not one of your own", res_foreign["message"])

                # Reject raid colony destination with clean aborted payload
                res_raid = bridge.handle_command(
                    {
                        "command": "station",
                        "planet_id": 101,
                        "target_coords": "02:380:09",
                        "ships": {"s11": 5},
                        "dry_run": True,
                    }
                )
                self.assertFalse(res_raid["executed"])
                self.assertIn("not one of your own", res_raid["message"])

                # Reject same origin == destination with clean aborted payload
                res_same = bridge.handle_command(
                    {
                        "command": "station",
                        "planet_id": 101,
                        "target_coords": "02:380:12",
                        "ships": {"s11": 5},
                        "dry_run": True,
                    }
                )
                self.assertFalse(res_same["executed"])
                self.assertIn("same planet", res_same["message"].lower())

                # Reject s9-only (Solar cells are stationary and stripped)
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command(
                        {
                            "command": "station",
                            "planet_id": 101,
                            "target_coords": "02:380:08",
                            "ships": {"s9": 5},
                            "dry_run": True,
                        }
                    )

                # Live station blocked when allow_transport=False
                with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                    res_blocked = bridge.handle_command(
                        {
                            "command": "station",
                            "planet_id": 101,
                            "target_coords": "02:380:08",
                            "ships": {"s11": 5, "s4": 4},
                            "dry_run": False,
                        }
                    )
                self.assertFalse(res_blocked["executed"])
                self.assertIn("allow_transport", res_blocked["message"])

                # Enable allow_transport
                upd = bridge.handle_command({"command": "update_config", "config": {"allow_transport": True}})
                self.assertTrue(upd["ok"])

                # Dry-run succeeds & strips s9 cleanly
                res_dry = bridge.handle_command(
                    {
                        "command": "station",
                        "planet_id": 101,
                        "target_coords": "02:380:08",
                        "ships": {"s11": 5, "s4": 4, "s9": 3},
                        "dry_run": True,
                    }
                )
                self.assertTrue(res_dry["ok"])
                self.assertTrue(res_dry["dry_run"])
                self.assertEqual(res_dry["action"]["kind"], "STATION")
                self.assertIn("5x s11", res_dry["action"]["target_name"])
                self.assertIn("4x s4", res_dry["action"]["target_name"])
                self.assertNotIn("s9", res_dry["action"]["target_name"])

                # Live station delegates to _live_executor when allow_transport=True
                def _ok_st(_c, _s, act, **_kw):
                    return ExecutionResult(
                        action=act,
                        executed=True,
                        validated=True,
                        dry_run=False,
                        message="Dispatched Station to 02:380:08",
                    )

                with (
                    patch("spaceinvasion.api_server.is_debug_port_open", return_value=True),
                    patch.object(bridge._live_executor, "execute_action", side_effect=_ok_st),
                ):
                    res_live = bridge.handle_command(
                        {
                            "command": "station",
                            "planet_id": 101,
                            "dest_planet_id": 102,
                            "ships_spec": "5x s11, 4x s4",
                            "dry_run": False,
                        }
                    )
                self.assertTrue(res_live["ok"])
                self.assertTrue(res_live["executed"])

                # 3. Strictly per-planet _apply_fleet_slot_holds:
                # Planet 101 is 2/2 full -> Planet 101 ATTACK held, while Planet 102 (0/3 used) ATTACK & RECYCLE stay executable!
                sample_iso = [
                    PlannedAction(priority="P2", kind="ATTACK", planet_id=101, planet_coords="02:380:12", target_name="02:380:15", executable_now=True, reason="Hub raid"),
                    PlannedAction(priority="P2", kind="RECYCLE", planet_id=102, planet_coords="02:380:08", target_name="Rubble -> 02:380:14", executable_now=True, reason="ColonyA recycle"),
                    PlannedAction(priority="P2", kind="ATTACK", planet_id=102, planet_coords="02:380:08", target_name="02:380:16", executable_now=True, reason="ColonyA raid"),
                ]
                held_iso = bridge._apply_fleet_slot_holds(st_two, sample_iso)
                self.assertFalse(held_iso[0].executable_now)  # Planet 101 full (2/2)
                self.assertTrue(held_iso[1].executable_now)   # Planet 102 free (0/3)
                self.assertTrue(held_iso[2].executable_now)   # Planet 102 free (0/3)

                # Tier #3.5 STATION reservation on origin planet:
                # Planet 101 with 1/2 used (1 free slot) & reserved_spy_slots=0 -> STATION gets the slot, ATTACK on 101 is held!
                bridge.handle_command({"command": "update_config", "config": {"reserved_spy_slots": 0}})
                st_one_free = GameState(
                    universe_id=31,
                    user_id=1,
                    nickname="cmdr",
                    timestamp=2000.0,
                    planets=(p1_busy, p2_free),
                    active_planet_id=101,
                    active_queues=(
                        QueueJob(category="fleet", name="Attack → 02:380:15", remaining_seconds=180.0, detail="Outbound", origin_coords="02:380:12"),
                    ),
                )
                held_tier = bridge._apply_fleet_slot_holds(
                    st_one_free,
                    [
                        st_action,
                        PlannedAction(priority="P2", kind="ATTACK", planet_id=101, planet_coords="02:380:12", target_name="02:380:16", executable_now=True, reason="Hub raid"),
                    ],
                )
                self.assertTrue(held_tier[0].executable_now)   # STATION takes the last free slot
                self.assertFalse(held_tier[1].executable_now)  # ATTACK held behind STATION
            finally:
                bridge.close()

    def test_format_clean_error_and_api_commander_strip_webdriver_stacktrace(self) -> None:
        import io
        import threading
        from contextlib import redirect_stdout
        import requests
        from selenium.common.exceptions import TimeoutException
        import bin.commander as commander_cli
        from spaceinvasion.core.logging_utils import format_clean_error, strip_webdriver_stacktrace
        from spaceinvasion.runtime.commander_client import GameApiError

        noisy_msg = (
            "timeout: Timed out receiving message from renderer: 20.000 "
            "(url=https://spaceinvasion.bitmeup.com/indexGame.es?sid=SecretSid999)\n"
            "  (Session info: chrome=158.0.8127.36)\n"
            "Stacktrace:\n"
            "#0 0x5635f98bdf7a <unknown>\n"
            "#1 0x5635f90b1935 <unknown>\n"
            "#24 0x7f7fbe121678 <unknown>\n"
        )
        exc = TimeoutException(noisy_msg)

        stripped = strip_webdriver_stacktrace(str(exc))
        self.assertIn("Timed out receiving message from renderer: 20.000", stripped)
        self.assertNotIn("Stacktrace:", stripped)
        self.assertNotIn("#0 0x", stripped)
        self.assertNotIn("#24 0x", stripped)
        self.assertNotIn("Session info:", stripped)
        self.assertNotIn("\n", stripped)

        cleaned = format_clean_error(exc)
        self.assertIn("Timed out receiving message from renderer: 20.000", cleaned)
        self.assertIn("sid=REDACTED", cleaned)
        self.assertNotIn("SecretSid999", cleaned)
        self.assertNotIn("Stacktrace:", cleaned)
        self.assertNotIn("Session info:", cleaned)
        self.assertNotIn("\n", cleaned)

        # HTTP GET /state and POST /command return clean single-line error JSON without C++ stacktrace
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            bridge = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                config_path=td_path / "c.yaml",
                db_path=None,
                log_dir=None,
                client_factory=lambda _: MagicMock(),
                ensure_session_fn=lambda: None,
            )
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with patch.object(bridge, "get_state", side_effect=TimeoutException(noisy_msg)):
                    r_get = requests.get(f"{base_url}/state?refresh=1", timeout=5)
                self.assertEqual(r_get.status_code, 500)
                err_get = r_get.json()["error"]
                self.assertIn("Timed out receiving message from renderer: 20.000", err_get)
                self.assertIn("sid=REDACTED", err_get)
                self.assertNotIn("SecretSid999", err_get)
                self.assertNotIn("Stacktrace:", err_get)
                self.assertNotIn("#0 0x", err_get)
                self.assertNotIn("Session info:", err_get)

                with patch.object(bridge, "handle_command", side_effect=TimeoutException(noisy_msg)):
                    r_post = requests.post(f"{base_url}/command", json={"command": "plan"}, timeout=5)
                self.assertEqual(r_post.status_code, 500)
                err_post = r_post.json()["error"]
                self.assertIn("Timed out receiving message from renderer: 20.000", err_post)
                self.assertIn("sid=REDACTED", err_post)
                self.assertNotIn("SecretSid999", err_post)
                self.assertNotIn("Stacktrace:", err_post)
                self.assertNotIn("#24 0x", err_post)
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()

        # Commander loop logs a clean single-line API unavailable message with stacktrace stripped & sid redacted
        fake_api = MagicMock()
        fake_api.send_command.side_effect = GameApiError(str(exc))
        buf = io.StringIO()
        with (
            patch.object(commander_cli, "GameApiClient", return_value=fake_api),
            redirect_stdout(buf),
        ):
            rc = commander_cli.main(["--api-url", "http://127.0.0.1:8787"])
        out = buf.getvalue()
        self.assertEqual(rc, 1)
        self.assertIn("API unavailable", out)
        self.assertIn("Timed out receiving message from renderer: 20.000", out)
        self.assertIn("sid=REDACTED", out)
        self.assertNotIn("SecretSid999", out)
        self.assertNotIn("Stacktrace:", out)
        self.assertNotIn("#0 0x", out)
        self.assertNotIn("Session info:", out)

    def test_4tab_config_optimizer_ai_planner_fleet_planner_and_exports_sync_and_async(self) -> None:
        import time
        from spaceinvasion.core.state import (
            BuildingOffer,
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
            UnitOffer,
        )
        from spaceinvasion.db.storage import GameDatabase
        from spaceinvasion.runtime.api_server import ApiRequestError

        p1 = PlanetState(
            planet_id=701,
            name="Prime",
            coords="01:100:04",
            planet_type="normal",
            temperature=30,
            fields_used=90,
            fields_max=300,
            resources=Resources(pig_iron=250_000, metal=150_000, kryptonite=100_000, spice=60_000),
            storage=Resources(1_000_000, 1_000_000, 1_000_000, 1_000_000),
            energy=EnergyBalance(produced=4000, used=1500),
            buildings={"b1": 12, "b2": 8, "b3": 10, "b4": 8, "b5": 12, "b7": 6, "b8": 8, "b9": 6, "b10": 4},
            ships={"s2": 15, "s10": 8, "s4": 10},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 12, 13, Resources(25_000, 10_000, 0, 0), True, "Available", "?b=1"),
                BuildingOffer(3, "Kryptonite mine", 10, 11, Resources(15_000, 10_000, 0, 0), True, "Available", "?b=3"),
            ),
            ship_offers=(
                UnitOffer("ship", 4, "Small fighter", 10, Resources(3000, 1000, 0, 0), True, "ship[4]", "Available"),
            ),
        )
        p2 = PlanetState(
            planet_id=702,
            name="Outpost",
            coords="01:100:09",
            planet_type="normal",
            temperature=-40,
            fields_used=60,
            fields_max=310,
            resources=Resources(pig_iron=80_000, metal=50_000, kryptonite=35_000, spice=20_000),
            storage=Resources(1_000_000, 1_000_000, 1_000_000, 1_000_000),
            energy=EnergyBalance(produced=3000, used=1200),
            buildings={"b1": 10, "b2": 6, "b3": 9, "b4": 8, "b5": 11, "b7": 5, "b10": 2},
            ships={"s2": 5},
            building_offers=(
                BuildingOffer(4, "Spice mine", 8, 9, Resources(12_000, 8_000, 0, 0), True, "Available", "?b=4"),
            ),
        )
        st = GameState(
            universe_id=31,
            user_id=1,
            nickname="cmdr",
            timestamp=2000.0,
            planets=(p1, p2),
            active_planet_id=701,
            researches={"r1": 6, "r2": 5, "r3": 4, "r8": 5},
        )
        fast_co_params = {
            "catalogue_grid_size": 120,
            "catalogue_priority_steps": 2,
            "ga_population_size": 8,
            "ga_generations": 3,
            "ga_elite_count": 2,
            "ga_random_seed": 42,
        }
        fast_ai_params = {
            "reward_weeks": 4,
            "ga_population_size": 4,
            "ga_generations": 2,
            "ga_elite_count": 1,
            "ga_random_seed": 42,
            "revalue_every_steps": 20,
        }
        client_factory = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            db_path = td_path / "planner.db"
            # Seed raid history so SQLite live attack telemetry is populated
            seed_db = GameDatabase(db_path, attack_history_path="")
            seed_db.record_raid(
                "01:100:12",
                attacked_ts=1500.0,
                outcome="win",
                loot={"pig_iron": 40_000, "metal": 20_000, "kryptonite": 15_000, "spice": 8_000},
            )
            seed_db.close()

            cfg_path = td_path / "cfg.yaml"
            cfg_path.write_text(
                "planner_mode: priority\nmax_planets: 11\nallow_raids: false\nallow_transport: true\n",
                encoding="utf-8",
            )
            bridge = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                config_path=cfg_path,
                db_path=db_path,
                log_dir=None,
                client_factory=client_factory,
                ensure_session_fn=lambda: None,
            )
            try:
                bridge._last_state = st

                # 1. Initial status (2/11 colonies -> locked)
                st_resp = bridge.handle_command({"command": "planner_jobs_status"})
                self.assertTrue(st_resp["ok"])
                self.assertEqual(st_resp["colonies_owned"], 2)
                self.assertEqual(st_resp["max_planets"], 11)
                self.assertFalse(st_resp["live_ai_unlocked"])
                self.assertEqual(st_resp["config_optimizer_job"]["status"], "idle")
                self.assertEqual(st_resp["ai_planner_job"]["status"], "idle")

                # 2. ai_planner_run fails clearly when auto_run_config_if_missing=False and no config target
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command(
                        {
                            "command": "ai_planner_run",
                            "params": fast_ai_params,
                            "auto_run_config_if_missing": False,
                            "sync": True,
                        }
                    )

                # 3. config_optimizer_run (sync=True, auto_replan_ai=False) + export JSON
                co_run = bridge.handle_command(
                    {
                        "command": "config_optimizer_run",
                        "params": fast_co_params,
                        "auto_replan_ai": False,
                        "sync": True,
                    }
                )
                self.assertTrue(co_run["ok"])
                self.assertEqual(co_run["job"]["status"], "completed")
                self.assertEqual(co_run["result"]["schema_version"], 1)
                self.assertEqual(len(co_run["result"]["planets"]), 2)

                co_get = bridge.handle_command({"command": "config_optimizer_get"})
                self.assertTrue(co_get["ok"])
                self.assertIsNotNone(co_get["active"])
                self.assertEqual(len(co_get["runs"]), 1)
                self.assertIn("target_ship_key", co_get["default_params"])
                self.assertEqual(co_get["planet_specializations"], {})
                self.assertIn("kryptonite", co_get["specialization_options"])

                # 3b. Per-planet specialization lock persistence & sticky rerun via API commands
                spec_set = bridge.handle_command(
                    {
                        "command": "config_optimizer_set_specializations",
                        "planet_specializations": {"701": "kryptonite"},
                        "replace": True,
                    }
                )
                self.assertTrue(spec_set["ok"])
                self.assertEqual(spec_set["planet_specializations"], {"701": "kryptonite"})
                self.assertEqual(spec_set["locked_planets_count"], 1)

                co_get_locked = bridge.handle_command({"command": "config_optimizer_get"})
                self.assertEqual(co_get_locked["planet_specializations"], {"701": "kryptonite"})

                co_run_locked = bridge.handle_command(
                    {
                        "command": "config_optimizer_run",
                        "params": {**fast_co_params, "warehouse_hours_of_production": 26.0},
                        "auto_replan_ai": False,
                        "sync": True,
                    }
                )
                self.assertTrue(co_run_locked["ok"])
                p701 = next(p for p in co_run_locked["result"]["planets"] if p["planet_id"] == 701)
                self.assertEqual(p701["locked_specialization"], "kryptonite")
                self.assertEqual(p701["detected_specialization"], "kryptonite")
                self.assertEqual(co_run_locked["result"]["summary"]["locked_planets_count"], 1)

                # Clear specialization locks back to auto
                spec_clear = bridge.handle_command(
                    {
                        "command": "config_optimizer_set_specializations",
                        "planet_specializations": {},
                        "replace": True,
                    }
                )
                self.assertEqual(spec_clear["planet_specializations"], {})

                with self.assertRaises(ApiRequestError):
                    bridge.handle_command({"command": "config_optimizer_export"})

                # 4. ai_planner_run (sync=True) + ai_planner_get pagination/search/graph + export TXT
                ai_run = bridge.handle_command(
                    {
                        "command": "ai_planner_run",
                        "params": fast_ai_params,
                        "sync": True,
                    }
                )
                self.assertTrue(ai_run["ok"])
                self.assertEqual(ai_run["job"]["status"], "completed")
                self.assertGreater(len(ai_run["result"]["steps"]), 0)

                ai_get = bridge.handle_command(
                    {
                        "command": "ai_planner_get",
                        "window_size": 25,
                        "page": 1,
                        "page_size": 10,
                        "search": "mine",
                    }
                )
                self.assertTrue(ai_get["ok"])
                self.assertTrue(ai_get["has_plan"])
                self.assertIsInstance(ai_get["active_summary"], dict)
                self.assertIsInstance(ai_get["weekly_breakdown"], list)
                self.assertEqual(ai_get["graph"]["planner_mode"], "ai_planner")
                self.assertLessEqual(len(ai_get["steps_page"]["items"]), 10)
                self.assertGreaterEqual(ai_get["attack_telemetry"]["sample_count"], 1)
                self.assertFalse(ai_get["live_ai_unlocked"])
                self.assertIsInstance(ai_get.get("smelting"), dict)
                self.assertIn("initial_live_mode", ai_get["smelting"])
                self.assertIn("initial_target_enabled", ai_get["smelting"])
                self.assertIsInstance(ai_get["smelting"].get("smelting_events"), list)
                self.assertIsInstance(ai_get["smelting"].get("planets"), list)
                self.assertIsInstance(ai_get.get("live_blast_furnace"), dict)
                self.assertIn("mode", ai_get["live_blast_furnace"])

                with self.assertRaises(ApiRequestError):
                    bridge.handle_command({"command": "ai_planner_export"})

                # 5. fleet_planner_get with isolated config preview override (no mutation of active config)
                fp_get = bridge.handle_command(
                    {
                        "command": "fleet_planner_get",
                        "config": {"allow_raids": True, "shipyard_targets": {"small-fighter": 20}},
                    }
                )
                self.assertTrue(fp_get["ok"])
                self.assertEqual(fp_get["graph"]["planner_mode"], "fleet_planner")
                self.assertIsInstance(fp_get["slot_budgets"], list)
                self.assertTrue(fp_get["config"]["allow_raids"])
                self.assertFalse(bridge.strategy_config.allow_raids)
                self.assertIn("available_budget", fp_get["spare_budget"])

                # 6. Non-blocking async background worker + auto-replan chaining (sync=False)
                async_resp = bridge.handle_command(
                    {
                        "command": "config_optimizer_run",
                        "params": fast_co_params,
                        "auto_replan_ai": True,
                        "sync": False,
                    }
                )
                self.assertTrue(async_resp["ok"])
                self.assertIn(async_resp["job"]["status"], ("running", "completed"))
                # Verify /health and planner_jobs_status never block while background GA runs
                deadline = time.monotonic() + 20.0
                done_co = False
                done_ai = False
                while time.monotonic() < deadline:
                    polled = bridge.handle_command({"command": "planner_jobs_status"})
                    co_j = polled["config_optimizer_job"]
                    ai_j = polled["ai_planner_job"]
                    if co_j["status"] == "completed" and (co_j.get("run_id") or 0) >= 2:
                        done_co = True
                    if done_co and ai_j["status"] == "completed" and (ai_j.get("run_id") or 0) >= 2:
                        done_ai = True
                        break
                    time.sleep(0.05)
                self.assertTrue(done_co, "Background config_optimizer_run did not complete")
                self.assertTrue(done_ai, "Chained auto-replan ai_planner_run did not complete")
                # Zero Selenium/browser client invocations across all planner jobs!
                self.assertEqual(client_factory.call_count, 0)
            finally:
                bridge.close()

    def test_all_colonies_gate_hybrid_ai_planner_field_conflicts_and_commander_tick(self) -> None:
        from dataclasses import replace
        import time
        from spaceinvasion.core.state import (
            BuildingOffer,
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
            UnitOffer,
        )
        from spaceinvasion.runtime.api_server import ApiRequestError

        p1 = PlanetState(
            planet_id=801,
            name="Alpha",
            coords="01:200:04",
            planet_type="normal",
            temperature=25,
            fields_used=80,
            fields_max=300,
            resources=Resources(pig_iron=220_000, metal=140_000, kryptonite=90_000, spice=50_000),
            storage=Resources(1_000_000, 1_000_000, 1_000_000, 1_000_000),
            energy=EnergyBalance(produced=4000, used=1500),
            buildings={"b1": 10, "b2": 6, "b3": 8, "b4": 7, "b5": 12, "b7": 5, "b8": 8, "b9": 5, "b10": 3},
            ships={"s2": 10, "s10": 5, "s4": 4},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 10, 11, Resources(15_000, 6_000, 0, 0), True, "Available", "?b=1"),
                BuildingOffer(3, "Kryptonite mine", 8, 9, Resources(10_000, 5_000, 0, 0), True, "Available", "?b=3"),
                BuildingOffer(4, "Spice mine", 7, 8, Resources(9_000, 4_000, 0, 0), True, "Available", "?b=4"),
                BuildingOffer(5, "Fusion plant", 12, 13, Resources(20_000, 10_000, 5_000, 0), True, "Available", "?b=5"),
            ),
            ship_offers=(
                UnitOffer("ship", 4, "Small fighter", 4, Resources(3000, 1000, 0, 0), True, "ship[4]", "Available"),
            ),
        )
        p2 = PlanetState(
            planet_id=802,
            name="Beta",
            coords="01:200:08",
            planet_type="normal",
            temperature=-30,
            fields_used=70,
            fields_max=300,
            resources=Resources(pig_iron=15_000, metal=10_000, kryptonite=5_000, spice=5_000),
            storage=Resources(1_000_000, 1_000_000, 1_000_000, 1_000_000),
            energy=EnergyBalance(produced=3000, used=1000),
            buildings={"b1": 8, "b2": 5, "b3": 7, "b4": 6, "b5": 10, "b7": 4, "b10": 2},
            ships={},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 8, 9, Resources(50_000, 25_000, 0, 0), False, "Not enough resources", None),
            ),
        )
        st_1col = GameState(
            universe_id=31,
            user_id=1,
            nickname="cmdr",
            timestamp=3000.0,
            planets=(p1,),
            active_planet_id=801,
            researches={"r1": 5, "r2": 4, "r3": 3, "r8": 4},
        )
        st_2col = replace(st_1col, planets=(p1, p2))

        with (
            patch("spaceinvasion.runtime.api_server.is_debug_port_open", return_value=False),
            tempfile.TemporaryDirectory() as td,
        ):
            td_path = Path(td)
            db_path = td_path / "gate.db"
            cfg_path = td_path / "gate.yaml"
            cfg_path.write_text(
                "planner_mode: ai_planner\nmax_planets: 2\nallow_transport: true\nallow_shipyard: true\nallow_transmitter: true\n",
                encoding="utf-8",
            )
            bridge = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                config_path=cfg_path,
                db_path=db_path,
                log_dir=None,
                client_factory=lambda _: MagicMock(),
                ensure_session_fn=lambda: None,
            )
            try:
                def _seed_state(st_obj: GameState) -> None:
                    bridge._record_state(
                        st_obj,
                        fetched_buildings=True,
                        fetched_research=True,
                        fetched_shipyard=True,
                        fetched_home=True,
                        observed_active_planet=True,
                    )
                    bridge._last_spio_sync_at = time.monotonic()

                # Phase A: 1/2 colonies -> live AI-Planner locked -> clean fallback to Heuristics!
                _seed_state(st_1col)
                plan_locked = bridge.handle_command({"command": "plan", "refresh": False})
                self.assertEqual(plan_locked["planner_mode"], "ai_planner")
                self.assertEqual(plan_locked["effective_planner_mode"], "priority")
                self.assertFalse(plan_locked["live_ai_unlocked"])
                self.assertIn("1/2", plan_locked["fallback_reason"])

                dash_locked = bridge.dashboard(refresh=False)
                self.assertEqual(dash_locked["planner_mode"], "ai_planner")
                self.assertEqual(dash_locked["effective_planner_mode"], "priority")
                self.assertFalse(dash_locked["live_ai_unlocked"])

                # Phase B: 2/2 colonies + saved AI-Planner plan -> live hybrid AI-Planner + Fleet unlocked!
                _seed_state(st_2col)
                synthetic_plan = {
                    "generated_at": "2026-11-18T00:00:00Z",
                    "meta_parameters": {
                        "reward_weeks": 4,
                        "resource_spare_percent": {
                            "pig_iron": 10.0,
                            "metal": 10.0,
                            "kryptonite": 10.0,
                            "spice": 10.0,
                        },
                    },
                    "summary": {"actions_count": 2, "discounted_reward_spice_eq": 100000.0},
                    "weekly_breakdown": [],
                    "steps": [
                        {
                            "seq": 1,
                            "start_sec": 0.0,
                            "duration_sec": 600.0,
                            "finish_sec": 600.0,
                            "week": 1,
                            "kind": "BUILD",
                            "planet_id": 801,
                            "planet_coords": "01:200:04",
                            "planet_name": "Alpha",
                            "entity_key": "b1",
                            "entity_name": "Pig iron mine",
                            "target_level": 11,
                            "cost": {"pig_iron": 15000, "metal": 6000, "kryptonite": 0, "spice": 0},
                        },
                        {
                            "seq": 2,
                            "start_sec": 600.0,
                            "duration_sec": 900.0,
                            "finish_sec": 1500.0,
                            "week": 1,
                            "kind": "BUILD",
                            "planet_id": 802,
                            "planet_coords": "01:200:08",
                            "planet_name": "Beta",
                            "entity_key": "b1",
                            "entity_name": "Pig iron mine",
                            "target_level": 9,
                            "cost": {"pig_iron": 50000, "metal": 25000, "kryptonite": 0, "spice": 0},
                        },
                    ],
                }
                bridge._db.record_ai_planner_run(
                    synthetic_plan,
                    params=synthetic_plan["meta_parameters"],
                    trigger_source="test",
                    set_active=True,
                )
                bridge._invalidate_dashboard_cache()

                plan_live = bridge.handle_command({"command": "plan", "refresh": False})
                self.assertEqual(plan_live["effective_planner_mode"], "ai_planner")
                self.assertTrue(plan_live["live_ai_unlocked"])
                self.assertEqual(plan_live["fallback_reason"], "")
                acts = plan_live["actions"]
                # Emits executable AI-Planner BUILD b1 L11 on Alpha AND inter-planet TRANSPORT to Beta!
                self.assertTrue(
                    any(a["kind"] == "BUILD" and a["planet_id"] == 801 and a["target_id"] == 1 and a["executable_now"] for a in acts)
                )
                self.assertTrue(
                    any(a["kind"] == "TRANSPORT" and a["planet_id"] == 801 and a["target_id"] == 802 and a["executable_now"] for a in acts)
                )
                self.assertFalse(any(a["kind"] == "DEMOLISH_BUILDING" for a in acts))

                # Commander tick in ai_planner mode fetches live hybrid plan from API and dispatches ready action
                fake_api = MagicMock()
                fake_api.get_state.return_value = st_2col
                fake_api.send_command.side_effect = [
                    plan_live,
                    {"ok": True, "executed": False, "dry_run": True, "message": "dry-run ok"},
                ]
                _, actual_act, _, _, _ = commander.run_commander_tick(
                    fake_api,
                    live=False,
                    max_actions=1,
                    config=bridge.strategy_config,
                    include_buildings=True,
                )
                self.assertIsNotNone(actual_act)
                self.assertEqual(fake_api.send_command.call_args_list[0].args[0]["command"], "plan")

                # Phase C: Max-field blocker on planet 1 -> NEVER auto-demolishes, records conflict & 1 notification!
                p1_full = replace(
                    p1,
                    fields_used=300,
                    fields_max=300,
                    energy=EnergyBalance(produced=1510, used=1500),  # Tight energy: demolishing b5 is unsafe!
                    buildings={**p1.buildings, "b11": 5},
                )
                _seed_state(replace(st_2col, planets=(p1_full, p2)))
                plan_conflict_1 = bridge.handle_command({"command": "plan", "refresh": False})
                self.assertFalse(any(a["kind"] == "DEMOLISH_BUILDING" for a in plan_conflict_1["actions"]))
                # Calling plan a second time must NOT duplicate notifications
                bridge.handle_command({"command": "plan", "refresh": False})
                pending_fc = bridge._db.list_field_conflict_prompts(status="pending")
                self.assertEqual(len(pending_fc), 1)
                ck = pending_fc[0]["conflict_key"]
                fc_notifs = [
                    n
                    for n in bridge._db.get_notifications(limit=50)
                    if n.get("category") == "field_conflict"
                ]
                self.assertEqual(len(fc_notifs), 1)

                # Energy-safety guard rejects demolishing Fusion plant (b5) when spare energy is tight
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command(
                        {
                            "command": "resolve_field_conflict",
                            "conflict_key": ck,
                            "decision": "demolish",
                            "building_key": "b5",
                        }
                    )

                # Safe demolish on Pig iron warehouse (b11, 0 energy) enqueues DEMOLISH_BUILDING into manual_queue!
                dem_resp = bridge.handle_command(
                    {
                        "command": "resolve_field_conflict",
                        "conflict_key": ck,
                        "decision": "demolish",
                        "building_key": "b11",
                    }
                )
                self.assertTrue(dem_resp["ok"])
                self.assertEqual(dem_resp["conflict"]["status"], "resolved")
                self.assertEqual(dem_resp["conflict"]["decision"], "demolish")
                self.assertEqual(dem_resp["queued_demolish"]["kind"], "DEMOLISH_BUILDING")
                self.assertEqual(dem_resp["queued_demolish"]["target_id"], 11)

                # Phase D: keep_reserve updates reserved floor & triggers chained config_optimizer -> ai_planner rerun!
                # Seed fast params in active config_optimizer & ai_planner so sync rerun is quick (<1s)
                bridge._db.record_config_optimizer_run(
                    {"schema_version": 1, "planets": [], "summary": {}},
                    params={
                        "catalogue_grid_size": 100,
                        "catalogue_priority_steps": 2,
                        "ga_population_size": 6,
                        "ga_generations": 2,
                        "ga_elite_count": 2,
                        "ga_random_seed": 7,
                    },
                    trigger_source="test_seed",
                    set_active=True,
                )
                bridge._db.record_ai_planner_run(
                    synthetic_plan,
                    params={
                        "reward_weeks": 2,
                        "ga_population_size": 4,
                        "ga_generations": 1,
                        "ga_elite_count": 1,
                        "ga_random_seed": 7,
                    },
                    trigger_source="test_seed",
                    set_active=True,
                )
                ck2 = "801:b3:9"
                bridge._db.upsert_field_conflict_prompt(
                    {
                        "conflict_key": ck2,
                        "planet_id": 801,
                        "planet_coords": "01:200:04",
                        "blocked_building_key": "b3",
                        "blocked_target_level": 9,
                        "candidate_building_key": "b11",
                        "candidate_level": 5,
                    }
                )
                kr_resp = bridge.handle_command(
                    {
                        "command": "resolve_field_conflict",
                        "conflict_key": ck2,
                        "decision": "keep_reserve",
                        "sync": True,
                    }
                )
                self.assertTrue(kr_resp["ok"])
                self.assertEqual(kr_resp["conflict"]["decision"], "keep_reserve")
                self.assertGreaterEqual(kr_resp["reserved_floors"]["801"]["b11"], 5)
                self.assertEqual(kr_resp["job"]["status"], "completed")
                self.assertEqual(kr_resp["ai_planner_job"]["status"], "completed")
            finally:
                bridge.close()

    def test_ai_planner_auto_bootstrap_and_http_4tab_commands_redaction(self) -> None:
        import requests
        from spaceinvasion.core.state import (
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
        )

        p1 = PlanetState(
            planet_id=901,
            name="Boot?sid=SecretToken999",
            coords="01:300:04",
            planet_type="normal",
            temperature=20,
            fields_used=50,
            fields_max=300,
            resources=Resources(150_000, 100_000, 75_000, 40_000),
            storage=Resources(1_000_000, 1_000_000, 1_000_000, 1_000_000),
            energy=EnergyBalance(3000, 1200),
            buildings={"b1": 10, "b2": 6, "b3": 8, "b4": 7, "b5": 11, "b7": 5, "b8": 8, "b9": 5, "b10": 3},
            ships={"s2": 10, "s10": 5},
        )
        st = GameState(
            universe_id=31,
            user_id=1,
            nickname="cmdr",
            timestamp=4000.0,
            planets=(p1,),
            active_planet_id=901,
            researches={"r1": 5, "r2": 4, "r8": 4},
        )
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            bridge = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                config_path=td_path / "c.yaml",
                db_path=td_path / "boot.db",
                log_dir=None,
                client_factory=lambda _: MagicMock(),
                ensure_session_fn=lambda: None,
            )
            bridge._last_state = st
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                # Fresh DB with no Config Optimizer target -> ai_planner_run auto-bootstraps Config Optimizer!
                r_run = requests.post(
                    f"{base_url}/command",
                    json={
                        "command": "ai_planner_run",
                        "params": {
                            "reward_weeks": 2,
                            "ga_population_size": 4,
                            "ga_generations": 1,
                            "ga_elite_count": 1,
                            "ga_random_seed": 11,
                        },
                        "sync": True,
                    },
                    timeout=30,
                )
                self.assertEqual(r_run.status_code, 200)
                run_body = r_run.json()
                self.assertTrue(run_body["ok"])
                self.assertEqual(run_body["job"]["status"], "completed")
                self.assertNotIn("SecretToken999", r_run.text)

                for cmd_payload in (
                    {"command": "planner_jobs_status"},
                    {"command": "config_optimizer_get"},
                    {"command": "ai_planner_get", "window_size": 15, "page": 1, "page_size": 10},
                    {"command": "fleet_planner_get"},
                ):
                    resp = requests.post(f"{base_url}/command", json=cmd_payload, timeout=15)
                    self.assertEqual(resp.status_code, 200, msg=f"{cmd_payload} -> {resp.text}")
                    self.assertTrue(resp.json()["ok"])
                    self.assertNotIn("SecretToken999", resp.text)
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()

    def test_live_recycle_pre_dispatch_aborts_clean_planet_and_trims_qty_for_live_debris(self) -> None:
        from dataclasses import replace as dc_replace
        import time
        from spaceinvasion.combat.fleet import GalaxySlot
        from spaceinvasion.core.state import PlanetState, Resources
        from spaceinvasion.planner.strategy import PlannedAction
        from spaceinvasion.session.executor import ExecutionResult

        with tempfile.TemporaryDirectory(prefix="si_rec_verify_") as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.yaml"
            cfg_path.write_text("allow_raids: true\nallow_galaxy_scan: true\n", encoding="utf-8")
            db_path = tmp_path / "spaceinvasion.db"

            base_st = _sample_state()
            p0 = dc_replace(base_st.planets[0], ships={"s11": 46})
            st = dc_replace(base_st, planets=(p0,), tutorial=None)

            clean_gal_html = """
            <html><body>
              <div class="planet-hit" data-planet="14">
                <div class="rubble-orbit" name="rubblefield"></div>
                <div><span>Spacia Planet (Spacia)</span><div>14</div></div>
              </div>
              <textarea id="json-element">{
                "position": {"galaxy": 2, "system": 380},
                "planets": {
                  "14": {
                    "pla": 14, "uid": 2, "name": "Spacia Planet", "uname": "Spacia",
                    "rubblefield": [280000, 180000, 60000, 0, 1],
                    "rubblefield1": [0, 0, 0, 0, 1]
                  }
                }
              }</textarea>
            </body></html>
            """
            live_debris_html = """
            <html><body>
              <div class="planet-hit" data-planet="1">
                <div class="rubble-orbit" name="rubblefield"></div>
                <div><span>Free</span><div>1</div></div>
              </div>
              <textarea id="json-element">{
                "position": {"galaxy": 2, "system": 379},
                "planets": {
                  "1": {
                    "pla": 1, "uid": 0, "name": "", "uname": "",
                    "rubblefield1": [10000, 10000, 10000, 5000, 1]
                  }
                }
              }</textarea>
            </body></html>
            """

            class _FakeClient:
                def __init__(self) -> None:
                    self.open_galaxy_calls: list[tuple[int, int]] = []

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def open_galaxy(self, *, galaxy=None, system=None, priority="interactive"):
                    self.open_galaxy_calls.append((int(galaxy), int(system)))
                    if int(system) == 380:
                        return clean_gal_html
                    if int(system) == 378:
                        return ""
                    return live_debris_html

                def read_full_snapshot(self, *args, **kwargs):
                    return st

            fake_client = _FakeClient()
            exec_dispatched: list[PlannedAction] = []

            class _FakeExec:
                def execute_action(self, client, state, action, active_planet_confirmed=None):
                    exec_dispatched.append(action)
                    return ExecutionResult(action=action, executed=True, dry_run=False, message="Dispatched")

            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                db_path=db_path,
                log_dir=None,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            try:
                assert bridge._db is not None
                bridge._last_state = st
                bridge._live_executor = _FakeExec()  # type: ignore[assignment]
                # Seed stale has_debris=True for both targets (scanned long ago)
                bridge._db.record_galaxy_slots(
                    [
                        GalaxySlot(coords="02:380:14", position=14, name="Spacia Planet", owner="Spacia", status_color="yellow", has_debris=True, is_spacia=True),
                        GalaxySlot(coords="02:379:01", position=1, name="", owner="", status_color="free", has_debris=True),
                    ],
                    ts=100.0,
                )
                # Even if scanned 1s ago, RECYCLE MUST still open live internalGalaxy right before dispatch!
                bridge._scanned_systems["02:380"] = time.time()
                bridge._scanned_systems["02:379"] = time.time()
                bridge._scanned_systems["02:378"] = time.time()

                # 1. Clean planet 02:380:14 -> MUST open galaxy view, abort before executor, and clear has_debris in DB!
                act_clean = PlannedAction(
                    priority="P2",
                    kind="RECYCLE",
                    planet_id=p0.planet_id,
                    planet_coords=p0.coords,
                    target_id=11,
                    target_level=46,
                    target_name="Rubble -> 02:380:14",
                    reason="test clean",
                    executable_now=True,
                )
                res_clean = bridge._execute_and_summarize(fake_client, st, "step", act_clean, dry_run=False)
                self.assertTrue(res_clean["ok"])
                self.assertFalse(res_clean["executed"])
                self.assertIn("clean", res_clean["message"].lower())
                self.assertIn((2, 380), fake_client.open_galaxy_calls)
                self.assertEqual(len(exec_dispatched), 0)
                deb_after_clean = [t["coords"] for t in bridge._db.get_active_debris_targets()]
                self.assertNotIn("02:380:14", deb_after_clean)

                # 2. Live galaxy view fails/returns empty (02:378:05) -> MUST abort safely (never launch blind)
                act_unverified = PlannedAction(
                    priority="P2",
                    kind="RECYCLE",
                    planet_id=p0.planet_id,
                    planet_coords=p0.coords,
                    target_id=11,
                    target_level=5,
                    target_name="Rubble -> 02:378:05",
                    reason="test unverified",
                    executable_now=True,
                )
                res_unver = bridge._execute_and_summarize(fake_client, st, "step", act_unverified, dry_run=False)
                self.assertTrue(res_unver["ok"])
                self.assertFalse(res_unver["executed"])
                self.assertIn("could not confirm debris", res_unver["message"].lower())
                self.assertEqual(len(exec_dispatched), 0)

                # 3. Live debris 02:379:01 (35,000 total -> 2 Recyclers @ 25k cap) -> opens galaxy view & trims 46 down to 2!
                act_live = PlannedAction(
                    priority="P2",
                    kind="RECYCLE",
                    planet_id=p0.planet_id,
                    planet_coords=p0.coords,
                    target_id=11,
                    target_level=46,
                    target_name="Rubble -> 02:379:01",
                    reason="test live",
                    executable_now=True,
                )
                res_live = bridge._execute_and_summarize(fake_client, st, "step", act_live, dry_run=False)
                self.assertTrue(res_live["ok"])
                self.assertTrue(res_live["executed"])
                self.assertIn((2, 379), fake_client.open_galaxy_calls)
                self.assertEqual(len(exec_dispatched), 1)
                self.assertEqual(exec_dispatched[0].target_level, 2)
            finally:
                bridge.close()

    def test_spacia_spy_and_attack_pre_dispatch_galaxy_verification(self) -> None:
        from dataclasses import replace as dc_replace
        from spaceinvasion.combat.fleet import GalaxySlot
        from spaceinvasion.session.executor import ExecutionResult

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.yaml"
            cfg_path.write_text("allow_raids: true\nallow_galaxy_scan: true\n", encoding="utf-8")
            db_path = tmp_path / "spaceinvasion.db"

            base_st = _sample_state()
            p0 = dc_replace(base_st.planets[0], ships={"s1": 25, "s10": 5})
            st = dc_replace(base_st, planets=(p0,), tutorial=None)

            # System 248: Spacia at pos 15 despawned (now uninhabited)
            despawned_html = """
            <html><body>
              <textarea id="json-element">{
                "position": {"galaxy": 2, "system": 248},
                "planets": {
                  "15": {"pla": 15, "uid": 0, "name": "", "uname": ""}
                }
              }</textarea>
            </body></html>
            """
            # System 208: Spacia at pos 9 replaced by a protected player
            settled_player_html = """
            <html><body>
              <textarea id="json-element">{
                "position": {"galaxy": 2, "system": 208},
                "planets": {
                  "9": {"pla": 9, "uid": 777, "name": "NewColony", "uname": "RookiePlayer", "noob": 1}
                }
              }</textarea>
            </body></html>
            """
            # System 258: Spacia at pos 1 is STILL Spacia
            still_spacia_html = """
            <html><body>
              <textarea id="json-element">{
                "position": {"galaxy": 2, "system": 258},
                "planets": {
                  "1": {"pla": 1, "uid": 2, "name": "Spacia Planet", "uname": "Spacia"}
                }
              }</textarea>
            </body></html>
            """

            class _FakeClient:
                def __init__(self) -> None:
                    self.open_galaxy_calls: list[tuple[int, int]] = []

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def open_galaxy(self, *, galaxy=None, system=None, priority="interactive"):
                    self.open_galaxy_calls.append((int(galaxy), int(system)))
                    if int(system) == 248:
                        return despawned_html
                    if int(system) == 208:
                        return settled_player_html
                    if int(system) == 191:
                        return ""
                    return still_spacia_html

                def read_full_snapshot(self, *args, **kwargs):
                    return st

            fake_client = _FakeClient()
            exec_dispatched: list[PlannedAction] = []

            class _FakeExec:
                def execute_action(self, client, state, action, active_planet_confirmed=None):
                    exec_dispatched.append(action)
                    return ExecutionResult(action=action, executed=True, dry_run=False, message="Dispatched")

            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                db_path=db_path,
                log_dir=None,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            try:
                assert bridge._db is not None
                bridge._last_state = st
                bridge._live_executor = _FakeExec()  # type: ignore[assignment]
                bridge._db.record_galaxy_slots(
                    [
                        GalaxySlot(coords="02:248:15", position=15, name="Spacia Planet", owner="Spacia", status_color="yellow", is_spacia=True),
                        GalaxySlot(coords="02:208:09", position=9, name="Spacia Planet", owner="Spacia", status_color="yellow", is_spacia=True),
                        GalaxySlot(coords="02:191:06", position=6, name="Spacia Planet", owner="Spacia", status_color="yellow", is_spacia=True),
                        GalaxySlot(coords="02:258:01", position=1, name="Spacia Planet", owner="Spacia", status_color="yellow", is_spacia=True),
                    ],
                    ts=100.0,
                )

                # 1. Despawned Spacia (02:248:15) -> opens galaxy, aborts SPY before executor, clears is_spacia in DB
                act_despawn = PlannedAction(
                    priority="P2",
                    kind="SPY",
                    planet_id=p0.planet_id,
                    planet_coords=p0.coords,
                    target_id=10,
                    target_level=1,
                    target_name="02:248:15",
                    reason="Refresh Spacia spy intel",
                    executable_now=True,
                )
                res1 = bridge._execute_and_summarize(fake_client, st, "step", act_despawn, dry_run=False)
                self.assertTrue(res1["ok"])
                self.assertFalse(res1["executed"])
                self.assertIn("spacia despawned", res1["message"].lower())
                self.assertIn((2, 248), fake_client.open_galaxy_calls)
                self.assertEqual(len(exec_dispatched), 0)
                st_248 = bridge._db.get_spy_target_by_coords("02:248:15")
                assert st_248 is not None
                self.assertFalse(bool(st_248.get("is_spacia")))
                self.assertEqual(st_248.get("status_color"), "free")
                self.assertEqual(bridge._db.unread_notification_count(), 0)

                # 2. Settled by real player (02:208:09) -> aborts ATTACK before executor & updates owner
                act_settled = PlannedAction(
                    priority="P2",
                    kind="ATTACK",
                    planet_id=p0.planet_id,
                    planet_coords=p0.coords,
                    target_id=1,
                    target_level=10,
                    target_name="Raid -> 02:208:09",
                    reason="Farm Spacia",
                    executable_now=True,
                )
                res2 = bridge._execute_and_summarize(fake_client, st, "step", act_settled, dry_run=False)
                self.assertTrue(res2["ok"])
                self.assertFalse(res2["executed"])
                self.assertIn("no longer spacia", res2["message"].lower())
                self.assertIn((2, 208), fake_client.open_galaxy_calls)
                self.assertEqual(len(exec_dispatched), 0)
                st_208 = bridge._db.get_spy_target_by_coords("02:208:09")
                assert st_208 is not None
                self.assertFalse(bool(st_208.get("is_spacia")))
                self.assertEqual(st_208.get("owner"), "RookiePlayer")

                # 3. Unverifiable galaxy HTML (02:191:06) -> aborts safely before executor
                act_empty = PlannedAction(
                    priority="P2",
                    kind="SPY",
                    planet_id=p0.planet_id,
                    planet_coords=p0.coords,
                    target_id=10,
                    target_level=1,
                    target_name="02:191:06",
                    reason="Probe Spacia",
                    executable_now=True,
                )
                res3 = bridge._execute_and_summarize(fake_client, st, "step", act_empty, dry_run=False)
                self.assertTrue(res3["ok"])
                self.assertFalse(res3["executed"])
                self.assertIn("could not confirm spacia", res3["message"].lower())
                self.assertEqual(len(exec_dispatched), 0)

                # 4. Still Spacia (02:258:01) -> confirms live Spacia and dispatches through executor!
                act_ok = PlannedAction(
                    priority="P2",
                    kind="ATTACK",
                    planet_id=p0.planet_id,
                    planet_coords=p0.coords,
                    target_id=1,
                    target_level=10,
                    target_name="Raid -> 02:258:01",
                    reason="Farm Spacia",
                    executable_now=True,
                )
                res4 = bridge._execute_and_summarize(fake_client, st, "step", act_ok, dry_run=False)
                self.assertTrue(res4["ok"])
                self.assertTrue(res4["executed"])
                self.assertIn((2, 258), fake_client.open_galaxy_calls)
                self.assertEqual(len(exec_dispatched), 1)
                self.assertEqual(exec_dispatched[0].target_name, "Raid -> 02:258:01")
            finally:
                bridge.close()

    def test_hybrid_ai_planner_blast_furnace_auto_throttle_and_manual_queue_override(self) -> None:
        from dataclasses import replace as dc_replace
        import time
        from spaceinvasion.core.state import (
            BuildingOffer,
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
        )

        p_krypt = PlanetState(
            planet_id=851,
            name="KryptSpec",
            coords="01:250:04",
            planet_type="normal",
            temperature=-20,
            fields_used=60,
            fields_max=300,
            resources=Resources(pig_iron=15_000, metal=5_000, kryptonite=40_000, spice=10_000),
            storage=Resources(1_000_000, 1_000_000, 1_000_000, 1_000_000),
            energy=EnergyBalance(produced=4000, used=1200),
            buildings={"b1": 0, "b2": 0, "b3": 10, "b4": 6, "b5": 12, "b8": 8},
            building_offers=(
                BuildingOffer(3, "Kryptonite mine", 10, 11, Resources(80_000, 60_000, 0, 0), False, "Not enough resources", None),
            ),
        )
        p_metal_off = PlanetState(
            planet_id=852,
            name="MetalSpec",
            coords="01:250:08",
            planet_type="normal",
            temperature=25,
            fields_used=70,
            fields_max=300,
            resources=Resources(pig_iron=70_000, metal=10_000, kryptonite=10_000, spice=10_000),
            storage=Resources(1_000_000, 1_000_000, 1_000_000, 1_000_000),
            energy=EnergyBalance(produced=4000, used=1200),
            buildings={"b1": 14, "b2": 10, "b3": 4, "b4": 4, "b5": 12},
            economy_rates={"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 14, 15, Resources(100_000, 45_000, 0, 0), False, "Not enough resources", None),
            ),
        )
        base_st = GameState(
            universe_id=31,
            user_id=1,
            nickname="cmdr",
            timestamp=5000.0,
            planets=(p_krypt, p_metal_off),
            active_planet_id=851,
            researches={"r1": 8, "r2": 6, "r8": 5},
        )
        synthetic_plan = {
            "generated_at": "2026-11-18T13:00:00Z",
            "meta_parameters": {
                "reward_weeks": 4,
                "resource_spare_percent": {"pig_iron": 10.0, "metal": 10.0, "kryptonite": 10.0, "spice": 10.0},
            },
            "summary": {"actions_count": 2, "discounted_reward_spice_eq": 100000.0},
            "weekly_breakdown": [],
            "steps": [
                {
                    "seq": 1,
                    "start_sec": 0.0,
                    "duration_sec": 900.0,
                    "finish_sec": 900.0,
                    "week": 1,
                    "kind": "BUILD",
                    "planet_id": 851,
                    "planet_coords": "01:250:04",
                    "planet_name": "KryptSpec",
                    "entity_key": "b3",
                    "entity_name": "Kryptonite mine",
                    "target_level": 11,
                    "cost": {"pig_iron": 80_000, "metal": 60_000, "kryptonite": 0, "spice": 0},
                },
                {
                    "seq": 2,
                    "start_sec": 900.0,
                    "duration_sec": 900.0,
                    "finish_sec": 1800.0,
                    "week": 1,
                    "kind": "BUILD",
                    "planet_id": 852,
                    "planet_coords": "01:250:08",
                    "planet_name": "MetalSpec",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 15,
                    "cost": {"pig_iron": 100_000, "metal": 45_000, "kryptonite": 0, "spice": 0},
                },
            ],
        }

        with (
            patch("spaceinvasion.runtime.api_server.is_debug_port_open", return_value=False),
            tempfile.TemporaryDirectory() as td,
        ):
            td_path = Path(td)
            cfg_path = td_path / "ai_bf.yaml"
            cfg_path.write_text(
                (
                    "planner_mode: ai_planner\n"
                    "max_planets: 2\n"
                    "allow_multi_planet: true\n"
                    "allow_transport: true\n"
                    "auto_throttle_blast_furnace: true\n"
                    "merchant_mode: false\n"
                ),
                encoding="utf-8",
            )
            bridge = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                config_path=cfg_path,
                db_path=td_path / "ai_bf.db",
                log_dir=None,
                client_factory=lambda _: MagicMock(),
                ensure_session_fn=lambda: None,
            )
            try:
                def _seed(st_obj: GameState) -> None:
                    bridge._record_state(
                        st_obj,
                        fetched_buildings=True,
                        fetched_research=True,
                        fetched_shipyard=True,
                        fetched_home=True,
                        observed_active_planet=True,
                    )
                    bridge._last_spio_sync_at = time.monotonic()

                bridge._db.record_ai_planner_run(
                    synthetic_plan,
                    params=synthetic_plan["meta_parameters"],
                    trigger_source="test",
                    set_active=True,
                )

                # Phase 1: Reserved AI-Planner step on b2=0 KryptSpec lacks Metal -> MetalSpec (0%) resumes to 100%!
                _seed(base_st)
                resp_on = bridge.handle_command({"command": "plan", "refresh": False})
                self.assertEqual(resp_on["effective_planner_mode"], "ai_planner")
                throttles_on = [a for a in resp_on["actions"] if a["kind"] == "THROTTLE_BUILDING"]
                self.assertEqual(len(throttles_on), 1)
                self.assertEqual(throttles_on[0]["planet_id"], 852)
                self.assertEqual(throttles_on[0]["target_id"], 2)
                self.assertEqual(throttles_on[0]["target_level"], 100)
                self.assertEqual(throttles_on[0]["priority"], "P1")

                # Phase 2: Empire now has 70k Metal (>= 60k) while reserved step needs 80k Pig iron (only 35k stock)
                #          -> MetalSpec (100%) pauses Blast furnace to 0% (P1)!
                p_metal_rich_on = dc_replace(
                    p_metal_off,
                    resources=Resources(pig_iron=20_000, metal=65_000, kryptonite=10_000, spice=10_000),
                    economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                )
                _seed(dc_replace(base_st, planets=(p_krypt, p_metal_rich_on)))
                resp_off = bridge.handle_command({"command": "plan", "refresh": False})
                throttles_off = [a for a in resp_off["actions"] if a["kind"] == "THROTTLE_BUILDING"]
                self.assertEqual(len(throttles_off), 1)
                self.assertEqual(throttles_off[0]["planet_id"], 852)
                self.assertEqual(throttles_off[0]["target_level"], 0)
                self.assertEqual(throttles_off[0]["priority"], "P1")

                # Phase 3: In active ai_planner mode, pinning a manual Metal-needing BUILD does NOT
                #          cancel AI-Planner's b2 -> 0% pause (only AI-Planner + 100% Metal overflow
                #          guard control b2 automatically)!
                p_metal_need_local = dc_replace(
                    p_metal_rich_on,
                    resources=Resources(pig_iron=20_000, metal=65_000, kryptonite=10_000, spice=10_000),
                    building_offers=(
                        BuildingOffer(
                            3,
                            "Kryptonite mine",
                            4,
                            5,
                            Resources(10_000, 120_000, 0, 0),
                            False,
                            "Not enough resources",
                            None,
                        ),
                    ),
                )
                _seed(dc_replace(base_st, planets=(p_krypt, p_metal_need_local)))
                enq = bridge.handle_command(
                    {
                        "command": "queue_add",
                        "kind": "BUILD",
                        "planet_id": 852,
                        "target_id": 3,
                        "target_level": 5,
                        "target_name": "Kryptonite mine",
                    }
                )
                self.assertTrue(enq["ok"])
                self.assertIn("AI-Planner", enq.get("metal_production_review", {}).get("summary", ""))
                resp_pinned = bridge.handle_command({"command": "plan", "refresh": False})
                self.assertTrue(
                    any(
                        a["kind"] == "THROTTLE_BUILDING"
                        and a["planet_id"] == 852
                        and a["target_level"] == 0
                        for a in resp_pinned["actions"]
                    ),
                    msg=f"AI-Planner b2->0% must NOT be overridden by pinned BUILD: {resp_pinned['actions']}",
                )

                # Phase 3b: Fallback priority mode still preserves manual-queue Metal reconciliation!
                bridge.strategy_config = dc_replace(bridge.strategy_config, planner_mode="priority")
                resp_prio_fallback = bridge.handle_command({"command": "plan", "refresh": False})
                self.assertEqual(resp_prio_fallback["effective_planner_mode"], "priority")
                self.assertFalse(
                    any(
                        a["kind"] == "THROTTLE_BUILDING"
                        and a["planet_id"] == 852
                        and a["target_level"] == 0
                        for a in resp_prio_fallback["actions"]
                    ),
                    msg=f"priority fallback should keep manual-queue b2 reconciliation: {resp_prio_fallback['actions']}",
                )
                bridge.strategy_config = dc_replace(bridge.strategy_config, planner_mode="ai_planner")

                # Phase 3c: Explicit user-pinned THROTTLE_BUILDING queue item (80% != current 100%) is still respected!
                enq_th = bridge.handle_command(
                    {
                        "command": "queue_add",
                        "kind": "THROTTLE_BUILDING",
                        "planet_id": 852,
                        "target_id": 2,
                        "target_level": 80,
                        "target_name": "Blast furnace 80%",
                    }
                )
                self.assertTrue(enq_th["ok"])
                resp_manual_th = bridge.handle_command({"command": "plan", "refresh": False})
                self.assertFalse(
                    any(
                        a["kind"] == "THROTTLE_BUILDING"
                        and a["planet_id"] == 852
                        and a["target_level"] == 0
                        for a in resp_manual_th["actions"]
                    ),
                    msg=f"Explicit manual THROTTLE_BUILDING should supersede auto b2->0%: {resp_manual_th['actions']}",
                )
                self.assertTrue(
                    any(
                        a["kind"] == "THROTTLE_BUILDING"
                        and a["planet_id"] == 852
                        and a["target_level"] == 80
                        and a.get("manual_queue_id") is not None
                        for a in resp_manual_th["actions"]
                    ),
                    msg=f"Explicit manual THROTTLE_BUILDING should remain queued: {resp_manual_th['actions']}",
                )
                for qrow in bridge._db.get_manual_queue(status="pending", limit=50):
                    bridge._db.cancel_manual_queue_item(int(qrow["id"]))

                # Phase 4: 100% Metal overflow pause -> SQLite persistence across restart ->
                #          hysteresis hold at 96% -> immediate auto-resume at 94% (bypassing 60s
                #          cooldown) when AI wants ON -> clear flag & stay 0% when AI wants OFF!
                p_krypt_huge = dc_replace(
                    p_krypt,
                    building_offers=(
                        BuildingOffer(
                            3,
                            "Kryptonite mine",
                            10,
                            11,
                            Resources(80_000, 150_000, 0, 0),
                            False,
                            "Not enough resources",
                            None,
                        ),
                    ),
                )
                huge_plan = dict(synthetic_plan)
                huge_plan["steps"] = [
                    {
                        **synthetic_plan["steps"][0],
                        "cost": {"pig_iron": 80_000, "metal": 150_000, "kryptonite": 0, "spice": 0},
                    }
                ]
                bridge._db.record_ai_planner_run(
                    huge_plan,
                    params=huge_plan["meta_parameters"],
                    trigger_source="test_overflow",
                    set_active=True,
                )
                p_metal_100 = dc_replace(
                    p_metal_rich_on,
                    resources=Resources(pig_iron=50_000, metal=100_000, kryptonite=10_000, spice=10_000),
                    storage=Resources(100_000, 100_000, 100_000, 100_000),
                    economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                )
                _seed(dc_replace(base_st, planets=(p_krypt_huge, p_metal_100)))
                resp_ovf = bridge.handle_command({"command": "plan", "refresh": False})
                th_ovf = [a for a in resp_ovf["actions"] if a["kind"] == "THROTTLE_BUILDING"]
                self.assertEqual(len(th_ovf), 1)
                self.assertEqual(th_ovf[0]["target_level"], 0)
                self.assertIn("Metal storage full", th_ovf[0]["reason"])

                # Execute the live overflow pause via throttle_building command
                from spaceinvasion.session.executor import ExecutionResult

                p_metal_100_paused = dc_replace(
                    p_metal_100,
                    economy_rates={"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100},
                )
                post_pause_state = dc_replace(base_st, planets=(p_krypt_huge, p_metal_100_paused))
                mock_client1 = MagicMock()
                mock_client1.__enter__.return_value = mock_client1
                mock_client1.read_full_snapshot.return_value = post_pause_state
                mock_client1.last_snapshot_observed_active_planet = True
                mock_client1.current_url = "https://spaceinvasion.bitmeup.com/indexInternal.es"
                bridge._client_factory = lambda _: mock_client1
                mock_exec_ok = MagicMock(
                    side_effect=lambda _client, _state, action, **_kw: ExecutionResult(
                        executed=True,
                        dry_run=False,
                        action=action,
                        message=f"Set b2 to {action.target_level}%",
                        validated=True,
                    )
                )
                with patch.object(bridge._live_executor, "execute_action", mock_exec_ok):
                    exec_pause = bridge.handle_command(
                        {
                            "command": "throttle_building",
                            "planet_id": 852,
                            "building_id": 2,
                            "percentage": 0,
                            "reason": th_ovf[0]["reason"],
                            "dry_run": False,
                        }
                    )
                self.assertTrue(exec_pause["executed"])
                self.assertIn(852, bridge._metal_overflow_paused_planets)
                bot_st = bridge.get_bot_control()
                self.assertEqual(bot_st["metal_overflow_paused_planets"], [852])
            finally:
                bridge.close()

            # Restart a brand-new bridge against the same SQLite DB -> must hydrate overflow_paused!
            mock_client2 = MagicMock()
            mock_client2.__enter__.return_value = mock_client2
            mock_client2.current_url = "https://spaceinvasion.bitmeup.com/indexInternal.es"
            bridge2 = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                config_path=cfg_path,
                db_path=td_path / "ai_bf.db",
                log_dir=None,
                client_factory=lambda _: mock_client2,
                ensure_session_fn=lambda: None,
            )
            try:
                self.assertIn(852, bridge2._metal_overflow_paused_planets)

                def _seed2(st_obj: GameState) -> None:
                    bridge2._record_state(
                        st_obj,
                        fetched_buildings=True,
                        fetched_research=True,
                        fetched_shipyard=True,
                        fetched_home=True,
                        observed_active_planet=True,
                    )
                    bridge2._last_spio_sync_at = time.monotonic()

                # 96% Metal -> still in hysteresis band (stays 0%, no auto-resume yet)
                p_metal_96 = dc_replace(
                    p_metal_off,
                    resources=Resources(pig_iron=50_000, metal=96_000, kryptonite=10_000, spice=10_000),
                    storage=Resources(100_000, 100_000, 100_000, 100_000),
                    economy_rates={"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100},
                )
                _seed2(dc_replace(base_st, planets=(p_krypt_huge, p_metal_96)))
                resp_96 = bridge2.handle_command({"command": "plan", "refresh": False})
                self.assertEqual(
                    [a for a in resp_96["actions"] if a["kind"] == "THROTTLE_BUILDING"],
                    [],
                )
                self.assertIn(852, bridge2._metal_overflow_paused_planets)

                # 94% Metal (<95%) while AI wants ON (and still inside 60s cooldown!) -> immediate auto-resume 100%!
                p_metal_94 = dc_replace(
                    p_metal_96,
                    resources=Resources(pig_iron=50_000, metal=94_000, kryptonite=10_000, spice=10_000),
                )
                _seed2(dc_replace(base_st, planets=(p_krypt_huge, p_metal_94)))
                resp_94_on = bridge2.handle_command({"command": "plan", "refresh": False})
                th_94_on = [a for a in resp_94_on["actions"] if a["kind"] == "THROTTLE_BUILDING"]
                self.assertEqual(len(th_94_on), 1)
                self.assertEqual(th_94_on[0]["target_level"], 100)
                self.assertEqual(th_94_on[0]["priority"], "P1")
                self.assertIn("Metal overflow cleared", th_94_on[0]["reason"])
                # Still tracked until executed!
                self.assertIn(852, bridge2._metal_overflow_paused_planets)

                # Execute auto-resume 100% -> clears tracked overflow flag!
                from spaceinvasion.session.executor import ExecutionResult

                p_metal_94_resumed = dc_replace(
                    p_metal_94,
                    economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                )
                mock_client2.read_full_snapshot.return_value = dc_replace(
                    base_st, planets=(p_krypt_huge, p_metal_94_resumed)
                )
                mock_client2.last_snapshot_observed_active_planet = True
                mock_exec_ok2 = MagicMock(
                    side_effect=lambda _client, _state, action, **_kw: ExecutionResult(
                        executed=True,
                        dry_run=False,
                        action=action,
                        message=f"Set b2 to {action.target_level}%",
                        validated=True,
                    )
                )
                with patch.object(bridge2._live_executor, "execute_action", mock_exec_ok2):
                    exec_res = bridge2.handle_command(
                        {
                            "command": "throttle_building",
                            "planet_id": 852,
                            "building_id": 2,
                            "percentage": 100,
                            "reason": th_94_on[0]["reason"],
                            "dry_run": False,
                        }
                    )
                self.assertTrue(exec_res["executed"])
                self.assertNotIn(852, bridge2._metal_overflow_paused_planets)

                # If overflow-paused and Metal drops <95% while AI wants OFF (60k <= 99k) -> clear flag & stay 0%!
                bridge2._metal_overflow_paused_planets[852] = time.time()
                bridge2._db.record_ai_planner_run(
                    synthetic_plan,
                    params=synthetic_plan["meta_parameters"],
                    trigger_source="test_overflow_clear_off",
                    set_active=True,
                )
                _seed2(dc_replace(base_st, planets=(p_krypt, p_metal_94)))
                resp_94_off = bridge2.handle_command({"command": "plan", "refresh": False})
                self.assertEqual(
                    [a for a in resp_94_off["actions"] if a["kind"] == "THROTTLE_BUILDING"],
                    [],
                )
                self.assertNotIn(852, bridge2._metal_overflow_paused_planets)
            finally:
                bridge2.close()

    def test_fleet_research_config_validation_merge_and_hybrid_ai_plan(self) -> None:
        import time
        from dataclasses import replace as dc_replace
        from spaceinvasion.runtime.api_server import ApiRequestError
        from spaceinvasion.core.state import (
            BuildingOffer,
            EnergyBalance,
            GameState,
            PlanetState,
            ResearchOffer,
            Resources,
            UnitOffer,
        )

        # 11-colony empire unlocking live ai_planner mode + homeworld Research Lab (b8=8, b9=5)
        planets_list: list[PlanetState] = []
        for idx in range(11):
            pid = 910 + idx
            is_home = idx == 0
            planets_list.append(
                PlanetState(
                    planet_id=pid,
                    name="Homeworld" if is_home else f"Colony {idx}",
                    coords=f"02:380:{idx + 1:02d}",
                    planet_type="normal",
                    temperature=20,
                    fields_used=60,
                    fields_max=250,
                    resources=Resources(pig_iron=80_000, metal=80_000, kryptonite=80_000, spice=80_000)
                    if is_home
                    else Resources(pig_iron=20_000, metal=20_000, kryptonite=20_000, spice=20_000),
                    storage=Resources(500_000, 500_000, 500_000, 500_000),
                    energy=EnergyBalance(3000, 1000),
                    buildings={
                        "b1": 14,
                        "b2": 8,
                        "b3": 14,
                        "b4": 12,
                        "b5": 16,
                        "b7": 8,
                        "b8": 8 if is_home else 0,
                        "b9": 5 if is_home else 0,
                    },
                    ships={"s1": 6, "s10": 10} if is_home else {},
                    building_offers=(
                        BuildingOffer(
                            1,
                            "Pig iron mine",
                            14,
                            15,
                            Resources(120_000, 0, 40_000, 0),
                            False,
                            "Not enough resources",
                            None,
                        ),
                    )
                    if is_home
                    else (),
                    ship_offers=(
                        UnitOffer("ship", 4, "Small fighter", 0, Resources(3_000, 1_000, 0, 0), True, "ship[4]"),
                    )
                    if is_home
                    else (),
                )
            )
        st = GameState(
            universe_id=31,
            user_id=103603,
            nickname="tester",
            timestamp=7000.0,
            planets=tuple(planets_list),
            active_planet_id=910,
            researches={"r1": 8, "r2": 7, "r3": 6, "r4": 6, "r5": 4, "r6": 0, "r7": 4, "r8": 0, "r9": 0},
            research_offers=(
                ResearchOffer(9, "r9", "Shield technology", 0, 1, Resources(0, 200, 600, 0), True, "", "?r=9"),
                ResearchOffer(8, "r8", "Weapons technology", 0, 1, Resources(0, 800, 200, 0), True, "", "?r=8"),
            ),
            speedup_factor=10.0,
        )
        synthetic_plan = {
            "meta_parameters": {"weeks": 4, "max_planets": 11},
            "steps": [
                {
                    "seq": 1,
                    "week": 1,
                    "kind": "BUILD",
                    "planet_id": 910,
                    "planet_coords": "02:380:01",
                    "planet_name": "Homeworld",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 15,
                    "cost": {"pig_iron": 120_000, "metal": 0, "kryptonite": 40_000, "spice": 0},
                    "start_sec": 0.0,
                    "duration_sec": 600.0,
                    "finish_sec": 600.0,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cfg_path = td_path / "config.yaml"
            cfg_path.write_text(
                "\n".join(
                    [
                        "planner_mode: ai_planner",
                        "allow_research: true",
                        "allow_fleet_research: true",
                        "allow_shipyard: true",
                        "allow_raids: false",
                        "allow_galaxy_scan: false",
                        "allow_fleetsave: false",
                        "ai_resource_spare_pct: 50.0",
                        "shipyard_targets:",
                        "  starcruiser: 6",
                        "  small-fighter: 10",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            bridge = GameApiBridge(
                debug_port=19998,
                env_file=td_path / "no.env",
                config_path=cfg_path,
                db_path=td_path / "fleet_res.db",
                log_dir=None,
                client_factory=lambda _: MagicMock(),
                ensure_session_fn=lambda: None,
            )
            try:
                bridge._record_state(
                    st,
                    fetched_buildings=True,
                    fetched_research=True,
                    fetched_shipyard=True,
                    fetched_home=True,
                    observed_active_planet=True,
                )
                bridge._last_spio_sync_at = time.monotonic()
                bridge._db.record_ai_planner_run(
                    synthetic_plan,
                    params=synthetic_plan["meta_parameters"],
                    trigger_source="test",
                    set_active=True,
                )

                # 1. update_config validates & partial-merges fleet_research_targets + allow_fleet_research
                upd1 = bridge.handle_command(
                    {
                        "command": "update_config",
                        "config": {"fleet_research_targets": {"weapons-technology": 5, "r9": 4}},
                    }
                )
                self.assertTrue(upd1["ok"])
                self.assertEqual(upd1["config"]["fleet_research_targets"], {"r8": 5, "r9": 4})

                upd2 = bridge.handle_command(
                    {
                        "command": "update_config",
                        "config": {"fleet_research_targets": {"r9": 0, "r11": 3}},
                    }
                )
                self.assertTrue(upd2["ok"])
                self.assertEqual(upd2["config"]["fleet_research_targets"], {"r8": 5, "r11": 3})

                # Validation rejections
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command({"command": "action_graph", "config": {"allow_fleet_research": "yes"}})
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command({"command": "update_config", "config": {"fleet_research_targets": "r9"}})
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command({"command": "update_config", "config": {"fleet_research_targets": {"r9": 101}}})
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command({"command": "update_config", "config": {"fleet_research_targets": {"r9": -1}}})

                # 2. Hybrid AI-Planner `plan` emits P2 Shield technology (r9) executable under spare budget!
                plan_resp = bridge.handle_command({"command": "plan", "refresh": False})
                self.assertEqual(plan_resp["effective_planner_mode"], "ai_planner")
                r9_acts = [a for a in plan_resp["actions"] if a["kind"] == "RESEARCH" and a["target_id"] == 9]
                self.assertEqual(len(r9_acts), 1)
                self.assertEqual(r9_acts[0]["priority"], "P2")
                self.assertTrue(r9_acts[0]["executable_now"])

                # 3. fleet_planner_get honors preview override without mutating saved config
                fp_preview = bridge.handle_command(
                    {
                        "command": "fleet_planner_get",
                        "config": {"allow_fleet_research": False},
                    }
                )
                self.assertTrue(fp_preview["ok"])
                self.assertEqual(fp_preview["graph"]["summary"]["planned_research_actions"], 0)
                self.assertTrue(bridge.strategy_config.allow_fleet_research)

                # With default saved config (allow_fleet_research=True), fleet_planner_get shows r9 + r8 nodes
                fp_live = bridge.handle_command({"command": "fleet_planner_get"})
                self.assertTrue(fp_live["ok"])
                self.assertGreaterEqual(fp_live["graph"]["summary"]["planned_research_actions"], 2)

                # 4. Zero spare budget holds P2 fleet research cleanly
                bridge.handle_command({"command": "update_config", "config": {"ai_resource_spare_pct": 0.0}})
                plan_zero = bridge.handle_command({"command": "plan", "refresh": False})
                r9_zero = next(a for a in plan_zero["actions"] if a["kind"] == "RESEARCH" and a["target_id"] == 9)
                self.assertFalse(r9_zero["executable_now"])
                self.assertIn("spare % fleet research budget", r9_zero["reason"])

                # 5. Stale research_offers cache bug fix: _last_research_sync_sig resets when switching planets
                bridge._last_research_sync_sig = (8, (("r1", 8),), False)
                st_switched = dc_replace(st, active_planet_id=911, research_offers=())
                bridge._record_state(st_switched, observed_active_planet=True)
                self.assertIsNone(bridge._last_research_sync_sig)
            finally:
                bridge.close()

    def test_ai_planner_early_transport_in_progress_and_reruns_with_dispatch_delay(self) -> None:
        from dataclasses import replace as dc_replace
        import time
        from spaceinvasion.core.state import (
            EnergyBalance,
            GameState,
            PlanetState,
            QueueJob,
            Resources,
        )
        from spaceinvasion.runtime.api_server import ApiRequestError

        donor = PlanetState(
            planet_id=951,
            name="Hub",
            coords="02:380:01",
            planet_type="normal",
            temperature=20,
            fields_used=90,
            fields_max=300,
            resources=Resources(pig_iron=450_000, metal=300_000, kryptonite=200_000, spice=100_000),
            storage=Resources(2_000_000, 2_000_000, 2_000_000, 2_000_000),
            energy=EnergyBalance(produced=6000, used=2000),
            buildings={"b1": 12, "b2": 8, "b3": 10, "b4": 8, "b5": 14, "b7": 6, "b8": 8, "b10": 4, "b11": 10, "b12": 10, "b13": 10, "b14": 10},
            ships={"s2": 30, "s10": 5},
        )
        dest_busy_soon = PlanetState(
            planet_id=952,
            name="BuilderSoon",
            coords="02:380:05",
            planet_type="normal",
            temperature=-10,
            fields_used=80,
            fields_max=300,
            resources=Resources(pig_iron=1_000, metal=500, kryptonite=500, spice=500),
            storage=Resources(2_000_000, 2_000_000, 2_000_000, 2_000_000),
            energy=EnergyBalance(produced=5000, used=1500),
            buildings={"b1": 14, "b2": 0, "b3": 0, "b4": 0, "b5": 16, "b7": 6, "b10": 2, "b11": 10, "b12": 10, "b13": 10, "b14": 10},
            ships={},
        )
        st_soon = GameState(
            universe_id=31,
            user_id=1,
            nickname="early_api",
            timestamp=5000.0,
            planets=(donor, dest_busy_soon),
            active_planet_id=951,
            researches={"r1": 4, "r2": 6, "r4": 4, "r5": 4},
            active_queues=(
                QueueJob(
                    category="building",
                    name="Pig iron mine (Level 15)",
                    building_id=1,
                    target_level=15,
                    origin_coords="02:380:05",
                    remaining_seconds=1800,
                    remaining_text="00:30:00",
                ),
            ),
            speedup_factor=5.0,
        )

        client_factory = MagicMock()
        with (
            patch("spaceinvasion.runtime.api_server.is_debug_port_open", return_value=False),
            tempfile.TemporaryDirectory() as td,
        ):
            td_path = Path(td)
            cfg_path = td_path / "early.yaml"
            cfg_path.write_text(
                "planner_mode: ai_planner\nmax_planets: 2\nallow_transport: true\nallow_shipyard: false\nallow_raids: false\nallow_galaxy_scan: false\n",
                encoding="utf-8",
            )
            bridge = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                config_path=cfg_path,
                db_path=td_path / "early.db",
                log_dir=None,
                client_factory=client_factory,
                ensure_session_fn=lambda: None,
            )
            try:
                def _seed(st_obj: GameState) -> None:
                    bridge._record_state(
                        st_obj,
                        fetched_buildings=True,
                        fetched_research=True,
                        fetched_shipyard=True,
                        fetched_home=True,
                        observed_active_planet=True,
                    )
                    bridge._last_spio_sync_at = time.monotonic()

                _seed(st_soon)

                # Validation rejects out-of-range / non-numeric dispatch delay & non-bool refresh
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command({"command": "ai_planner_run", "params": {"transport_dispatch_delay_minutes": -1}})
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command({"command": "ai_planner_run", "params": {"transport_dispatch_delay_minutes": 2000}})
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command({"command": "ai_planner_run", "params": {"transport_dispatch_delay_minutes": True}})
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command({"command": "ai_planner_run", "refresh": "yes"})

                # Run AI-Planner with custom 90m dispatch delay -> persists delay, in_progress, and transport_matrix
                co_target = {
                    "schema_version": 1,
                    "generated_at": "2026-11-18T00:00:00Z",
                    "meta_parameters": {"min_fleet_per_planet": {"s2": 10}},
                    "inputs": {"universe_speedup_factor": 5.0},
                    "planned_research_levels": {"r1": 4, "r2": 6},
                    "summary": {"target_ship": "Imperial starbase", "ships_per_month": 10.0},
                    "planets": [
                        {
                            "slot": 1,
                            "planet_id": 951,
                            "coords": "02:380:01",
                            "name": "Hub",
                            "is_central_planet": True,
                            "pays_for_research": True,
                            "temperature": 20,
                            "fields_max": 300,
                            "buildings": [{"key": k, "target_level": v} for k, v in donor.buildings.items()],
                        },
                        {
                            "slot": 2,
                            "planet_id": 952,
                            "coords": "02:380:05",
                            "name": "BuilderSoon",
                            "temperature": -10,
                            "fields_max": 300,
                            "buildings": [{"key": k, "target_level": (16 if k == "b1" else v)} for k, v in dest_busy_soon.buildings.items()],
                        },
                    ],
                }
                bridge._db.record_config_optimizer_run(
                    co_target,
                    params={},
                    reserved_floors={},
                    trigger_source="test",
                    set_active=True,
                )

                run1 = bridge.handle_command(
                    {
                        "command": "ai_planner_run",
                        "params": {"reward_weeks": 2, "ga_generations": 0, "transport_dispatch_delay_minutes": 90},
                        "sync": True,
                        "refresh": False,
                    }
                )
                self.assertTrue(run1["ok"])
                self.assertEqual(run1["result"]["meta_parameters"]["transport_dispatch_delay_minutes"], 90.0)
                self.assertEqual(run1["result"]["summary"]["in_progress_buildings_count"], 1)

                ai_get = bridge.handle_command({"command": "ai_planner_get"})
                self.assertEqual(ai_get["meta_parameters"]["transport_dispatch_delay_minutes"], 90.0)
                self.assertEqual(len(ai_get["in_progress"]["buildings"]), 1)
                self.assertIn("pairs", ai_get["transport_matrix"])

                # Hybrid live `plan` dispatches early TRANSPORT to BuilderSoon WHILE b1 L15 is still in progress!
                plan_soon = bridge.handle_command({"command": "plan", "refresh": False})
                self.assertEqual(plan_soon["effective_planner_mode"], "ai_planner")
                tr_soon = [a for a in plan_soon["actions"] if a["kind"] == "TRANSPORT" and a["target_id"] == 952]
                self.assertEqual(len(tr_soon), 1)
                self.assertTrue(tr_soon[0]["executable_now"])
                self.assertIn("early pre-ship", tr_soon[0]["reason"])

                # If active build still has 50_000s remaining (> 90m dispatch + travel), NO premature early transport!
                st_far = dc_replace(
                    st_soon,
                    active_queues=(
                        QueueJob(
                            category="building",
                            name="Pig iron mine (Level 15)",
                            building_id=1,
                            target_level=15,
                            origin_coords="02:380:05",
                            remaining_seconds=50_000,
                            remaining_text="13:53:20",
                        ),
                    ),
                )
                _seed(st_far)
                plan_far = bridge.handle_command({"command": "plan", "refresh": False})
                tr_far = [a for a in plan_far["actions"] if a["kind"] == "TRANSPORT" and a["target_id"] == 952]
                self.assertEqual(tr_far, [])

                # Rerunning AI-Planner after BuilderSoon finished b1 L16 starts cleanly from the new live colony state!
                dest_done = dc_replace(dest_busy_soon, buildings={**dest_busy_soon.buildings, "b1": 16})
                _seed(dc_replace(st_soon, planets=(donor, dest_done), active_queues=()))
                run2 = bridge.handle_command(
                    {
                        "command": "ai_planner_run",
                        "params": {"reward_weeks": 2, "ga_generations": 0, "transport_dispatch_delay_minutes": 90},
                        "sync": True,
                        "refresh": False,
                    }
                )
                self.assertTrue(run2["ok"])
                self.assertEqual(run2["result"]["steps"], [])
                self.assertEqual(client_factory.call_count, 0)
            finally:
                bridge.close()

    def test_flight_expected_delivered_enrichment_planet_renaming_building_requests_and_v014_dashboard(self) -> None:
        import time as _time
        from spaceinvasion.combat.fleet import GalaxySlot, InboxMessage, SpyReport

        now_ts = _time.time()
        with tempfile.TemporaryDirectory(prefix="si_api_rc_") as tmp:
            db_path = Path(tmp) / "rc.db"
            cfg_path = Path(tmp) / "config.yaml"
            bridge = GameApiBridge(
                db_path=db_path,
                config_path=cfg_path,
                client_factory=lambda _port: MagicMock(),
            )
            try:
                assert bridge._db is not None
                # Seed spy intel for outbound ATTACK and debris for outbound RECYCLE
                bridge._db.record_spy_report(
                    SpyReport(
                        target_coords="02:378:15",
                        owner="IdleFarm",
                        resources=Resources(pig_iron=40_000, metal=20_000, kryptonite=10_000, spice=4_000),
                    ),
                    ts=now_ts - 120.0,
                )
                bridge._persist_galaxy_slots(
                    [
                        GalaxySlot(
                            coords="02:381:06",
                            position=6,
                            name="Rubble",
                            owner="Idle",
                            status_color="yellow",
                            has_debris=True,
                            debris_pig_iron=30_000,
                            debris_metal=20_000,
                            debris_kryptonite=10_000,
                            debris_spice=5_000,
                        )
                    ]
                )
                # Seed battle + recycle report for V014 loot_recycle_history
                bridge._db.record_game_messages(
                    [
                        InboxMessage(
                            msg_uid="id:rc_atk_1",
                            server_msg_id="rc_atk_1",
                            folder_type="attack",
                            category="battle_report",
                            msg_date_text="2026-11-18 14:00:00",
                            sender="System",
                            subject="Battle report [02:378:15]",
                            coords="02:378:15",
                            body_text="You won the battle and stole 6000 Pig iron, 4000 Metal.",
                            parsed_data={"outcome": "win", "loot": {"pig_iron": 6000, "metal": 4000, "kryptonite": 0, "spice": 0}},
                        ),
                        InboxMessage(
                            msg_uid="id:rc_rec_1",
                            server_msg_id="rc_rec_1",
                            folder_type="inbox",
                            category="recycle_report",
                            msg_date_text="2026-11-18 14:05:00",
                            sender="System",
                            subject="Space rubble [02:381:06] recycling report",
                            coords="02:381:06",
                            body_text="Your fleet (4 Recycler) processed 2500 Kryptonite, 500 Spice pieces of rubble there.",
                            parsed_data={"recycler_count": 4, "harvested": {"pig_iron": 0, "metal": 0, "kryptonite": 2500, "spice": 500}},
                        ),
                    ],
                    ts=now_ts - 60.0,
                )
                # Record a live transport dispatch so returning TRANSPORT matches delivered_cargo
                bridge._record_transport_dispatch_locked(
                    origin_coords="02:380:12",
                    dest_coords="02:380:08",
                    cargo=Resources(18_000, 12_000, 4_000, 1_000),
                    ships="2 Large transport",
                    source="dispatch",
                    ts=now_ts - 180.0,
                )

                p1 = PlanetState(
                    planet_id=501,
                    name="Homeworld",
                    coords="02:380:12",
                    planet_type="normal",
                    temperature=20,
                    fields_used=40,
                    fields_max=300,
                    resources=Resources(50_000, 50_000, 30_000, 20_000),
                    storage=Resources(200_000, 200_000, 200_000, 200_000),
                    energy=EnergyBalance(produced=500, used=200),
                    buildings={"b1": 12, "b2": 10, "b3": 8, "b4": 6, "b5": 10},
                    ships={"s9": 10},
                )
                p2 = PlanetState(
                    planet_id=502,
                    name="Colony Beta",
                    coords="02:380:08",
                    planet_type="normal",
                    temperature=10,
                    fields_used=20,
                    fields_max=310,
                    resources=Resources(100, 100, 100, 100),
                    storage=Resources(100_000, 100_000, 100_000, 100_000),
                    energy=EnergyBalance(produced=300, used=100),
                    buildings={"b1": 8, "b2": 6, "b3": 4, "b4": 3, "b5": 6},
                )
                queues = (
                    # Outbound ATTACK with 1 Large transport (30k cap < 37k 50% loot -> capped to 30k!)
                    QueueJob(
                        category="fleet",
                        name="Attack → [02:378:15] IdleFarm",
                        detail="outbound · 1 Large transport",
                        origin_coords="02:380:12",
                        remaining_seconds=300.0,
                        finish_ts=now_ts + 300.0,
                    ),
                    # Outbound RECYCLE with 1 Recycler (25k cap < 65k debris -> capped to 25k!)
                    QueueJob(
                        category="fleet",
                        name="Recycle → [02:381:06] Debris",
                        detail="outbound · 1 Recycler",
                        origin_coords="02:380:12",
                        remaining_seconds=400.0,
                        finish_ts=now_ts + 400.0,
                    ),
                    # Outbound TRANSPORT to Colony Beta (populates both expected_cargo and building_resource_requests!)
                    QueueJob(
                        category="fleet",
                        name="Transport → [02:380:08] Colony Beta",
                        detail="outbound · 2 Large transport",
                        origin_coords="02:380:12",
                        cargo=Resources(20_000, 10_000, 5_000, 0),
                        remaining_seconds=250.0,
                        finish_ts=now_ts + 250.0,
                    ),
                    # Returning TRANSPORT matching persisted dispatch
                    QueueJob(
                        category="fleet",
                        name="Transport ↩ [02:380:08] Colony Beta",
                        detail="returning · 2 Large transport",
                        origin_coords="02:380:12",
                        cargo=None,
                        remaining_seconds=220.0,
                        finish_ts=now_ts + 220.0,
                    ),
                    # Returning TRANSPORT with NO matching dispatch (still delivered=True, delivered_cargo=None)
                    QueueJob(
                        category="fleet",
                        name="Transport ↩ [02:399:01] Unknown",
                        detail="returning · 1 Small transport",
                        origin_coords="02:380:12",
                        cargo=None,
                        remaining_seconds=180.0,
                        finish_ts=now_ts + 180.0,
                    ),
                )
                st = GameState(
                    universe_id=31,
                    user_id=77,
                    nickname="cmdr",
                    timestamp=now_ts,
                    planets=(p1, p2),
                    active_planet_id=501,
                    active_queues=queues,
                    researches={"r1": 4, "r2": 4},
                )
                bridge._record_state(st, fetched_buildings=True, fetched_home=True, observed_active_planet=True)

                # Seed a Config Optimizer result and AI-Planner result to test custom name overlay
                bridge._db.record_config_optimizer_run(
                    {
                        "summary": {"total_planets": 2},
                        "planets": [
                            {"planet_id": 501, "coords": "02:380:12", "name": "Homeworld"},
                            {"planet_id": 502, "coords": "02:380:08", "name": "Colony Beta"},
                        ],
                    },
                    set_active=True,
                )
                bridge._db.record_ai_planner_run(
                    {
                        "summary": {"total_steps": 1},
                        "steps": [
                            {
                                "seq": 1,
                                "week": 1,
                                "kind": "BUILD",
                                "planet_id": 502,
                                "planet_coords": "02:380:08",
                                "planet_name": "Colony Beta",
                                "entity_key": "b2",
                                "entity_name": "Blast furnace",
                                "target_level": 7,
                            }
                        ],
                    },
                    set_active=True,
                )

                # 1. Planet Renaming: invalid planet_id rejected
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command({"command": "rename_planet", "planet_id": 9999, "name": "Nope"})

                # Single rename
                ren = bridge.handle_command({"command": "rename_planet", "planet_id": 501, "name": "Capital Prime"})
                self.assertTrue(ren["ok"])
                self.assertEqual(ren["name"], "Capital Prime")
                self.assertEqual(ren["custom_name"], "Capital Prime")
                self.assertEqual(ren["game_name"], "Homeworld")
                self.assertEqual(ren["custom_planet_names"], {"501": "Capital Prime"})

                # Batch rename
                ren_b = bridge.handle_command(
                    {"command": "rename_planet", "planet_names": {"502": "Metal Forge"}}
                )
                self.assertTrue(ren_b["ok"])
                self.assertEqual(ren_b["custom_planet_names"], {"501": "Capital Prime", "502": "Metal Forge"})

                # Raw _last_state planet names stay completely untouched (Selenium safety!)
                self.assertEqual(bridge._last_state.planets[0].name, "Homeworld")
                self.assertEqual(bridge._last_state.planets[1].name, "Colony Beta")

                # 2. Verify /dashboard payload: custom names, expected/delivered flights, building_resource_requests, V014 telemetry
                dash = bridge.dashboard(refresh=False)
                self.assertEqual(dash["custom_planet_names"], {"501": "Capital Prime", "502": "Metal Forge"})
                p_by_id = {p["planet_id"]: p for p in dash["state"]["planets"]}
                self.assertEqual(p_by_id[501]["name"], "Capital Prime")
                self.assertEqual(p_by_id[501]["game_name"], "Homeworld")
                self.assertEqual(p_by_id[501]["custom_name"], "Capital Prime")
                self.assertEqual(p_by_id[502]["name"], "Metal Forge")

                q_list = dash["state"]["active_queues"]
                atk_q = next(q for q in q_list if "Attack →" in q["name"])
                self.assertIsNotNone(atk_q["expected_cargo"])
                atk_tot = sum(atk_q["expected_cargo"].values())
                self.assertEqual(atk_tot, 30_000)

                rec_q = next(q for q in q_list if "Recycle →" in q["name"])
                self.assertIsNotNone(rec_q["expected_cargo"])
                self.assertEqual(sum(rec_q["expected_cargo"].values()), 25_000)

                trn_out_q = next(q for q in q_list if "Transport →" in q["name"])
                self.assertEqual(trn_out_q["expected_cargo"]["pig_iron"], 20_000)
                self.assertFalse(trn_out_q["delivered"])

                trn_ret_matched = next(
                    q for q in q_list if "Transport ↩" in q["name"] and "02:380:08" in q["name"]
                )
                self.assertTrue(trn_ret_matched["delivered"])
                self.assertIsNotNone(trn_ret_matched["delivered_cargo"])
                self.assertEqual(trn_ret_matched["delivered_cargo"]["pig_iron"], 18_000)
                self.assertEqual(trn_ret_matched["delivered_cargo"]["metal"], 12_000)

                trn_ret_unmatched = next(
                    q for q in q_list if "Transport ↩" in q["name"] and "02:399:01" in q["name"]
                )
                self.assertTrue(trn_ret_unmatched["delivered"])
                self.assertIsNone(trn_ret_unmatched["delivered_cargo"])

                # Building Queue resource requests include the in-flight transport to Metal Forge (502)
                reqs = dash["building_resource_requests"]
                in_flight_req = next((r for r in reqs if r["status"] == "in_flight" and r["planet_id"] == 502), None)
                self.assertIsNotNone(in_flight_req)
                assert in_flight_req is not None
                self.assertEqual(in_flight_req["planet_name"], "Metal Forge")
                self.assertEqual(in_flight_req["source_name"], "Capital Prime")
                self.assertEqual(in_flight_req["pig_iron"], 20_000)
                self.assertEqual(in_flight_req["metal"], 10_000)

                # V014 hourly production & loot/recycle telemetry on /dashboard
                self.assertGreater(dash["current_hourly_production"]["total_per_hour"], 0.0)
                self.assertEqual(dash["current_hourly_production"]["planet_count"], 2)
                self.assertGreaterEqual(len(dash["hourly_production_history"]), 1)
                self.assertEqual(len(dash["loot_recycle_history"]), 24)
                self.assertEqual(dash["loot_recycle_summary"]["total_stolen"], 10_000.0)
                self.assertEqual(dash["loot_recycle_summary"]["total_recycled"], 3_000.0)
                self.assertEqual(dash["loot_recycle_summary"]["grand_total"], 13_000.0)

                # 3. Verify /state, config_optimizer_get, and ai_planner_get reflect custom names
                st_out = bridge.get_state(include_buildings=False)
                self.assertEqual(st_out["custom_planet_names"], {"501": "Capital Prime", "502": "Metal Forge"})
                self.assertEqual(st_out["planets"][0]["name"], "Capital Prime")
                self.assertEqual(st_out["planets"][0]["game_name"], "Homeworld")

                co_get = bridge.handle_command({"command": "config_optimizer_get"})
                self.assertEqual(co_get["custom_planet_names"], {"501": "Capital Prime", "502": "Metal Forge"})
                self.assertEqual(co_get["active"]["result"]["planets"][0]["name"], "Capital Prime")
                self.assertEqual(co_get["active"]["result"]["planets"][0]["game_name"], "Homeworld")

                ai_get = bridge.handle_command({"command": "ai_planner_get"})
                self.assertEqual(ai_get["custom_planet_names"], {"501": "Capital Prime", "502": "Metal Forge"})
                self.assertEqual(ai_get["steps_page"]["items"][0]["planet_name"], "Metal Forge")

                # Clear override back to game name
                clr = bridge.handle_command({"command": "rename_planet", "planet_id": 501, "name": ""})
                self.assertEqual(clr["name"], "Homeworld")
                self.assertIsNone(clr["custom_name"])
                self.assertNotIn("501", clr["custom_planet_names"])
            finally:
                bridge.close()

    def test_parallel_building_subrank_multi_planet_queue_retention_and_dynamic_max_galaxies(self) -> None:
        import time as _time

        now_ts = _time.time()
        with tempfile.TemporaryDirectory(prefix="si_api_p100_103_") as tmp:
            db_path = Path(tmp) / "p100_103.db"
            cfg_path = Path(tmp) / "config.yaml"
            bridge = GameApiBridge(
                db_path=db_path,
                config_path=cfg_path,
                client_factory=lambda _port: MagicMock(),
            )
            try:
                # 1. Multi-planet building queue retention & active_builders count (#100)
                p1 = PlanetState(
                    planet_id=601, name="Alpha", coords="02:380:01", planet_type="normal",
                    temperature=20, fields_used=20, fields_max=300,
                    resources=Resources(10_000, 10_000, 5_000, 2_000),
                    storage=Resources(100_000, 100_000, 100_000, 100_000),
                    energy=EnergyBalance(produced=300, used=100),
                    buildings={"b1": 10},
                )
                p2 = PlanetState(
                    planet_id=602, name="Beta", coords="02:380:02", planet_type="normal",
                    temperature=20, fields_used=20, fields_max=300,
                    resources=Resources(10_000, 10_000, 5_000, 2_000),
                    storage=Resources(100_000, 100_000, 100_000, 100_000),
                    energy=EnergyBalance(produced=300, used=100),
                    buildings={"b1": 8},
                )
                st_a = GameState(
                    universe_id=31, user_id=77, nickname="cmdr", timestamp=now_ts,
                    planets=(p1, p2), active_planet_id=601,
                    active_queues=(
                        QueueJob(
                            category="building",
                            name="Pig iron mine (Level 11)",
                            building_id=1,
                            target_level=11,
                            origin_coords="",
                            remaining_seconds=600.0,
                            finish_ts=now_ts + 600.0,
                        ),
                        QueueJob(
                            category="building",
                            name="Expired job (Level 2)",
                            building_id=2,
                            target_level=2,
                            origin_coords="02:380:01",
                            remaining_seconds=0.0,
                            finish_ts=now_ts - 60.0,
                        ),
                    ),
                )
                rec_a = bridge._record_state(st_a, fetched_buildings=True, fetched_home=True, observed_active_planet=True)
                self.assertEqual(rec_a.active_queues[0].origin_coords, "02:380:01")

                # Switch to planet Beta (602) with its own active build: Alpha's unexpired job is preserved, expired dropped!
                st_b = GameState(
                    universe_id=31, user_id=77, nickname="cmdr", timestamp=now_ts + 5.0,
                    planets=(p1, p2), active_planet_id=602,
                    active_queues=(
                        QueueJob(
                            category="building",
                            name="Metal mine (Level 9)",
                            building_id=2,
                            target_level=9,
                            origin_coords="",
                            remaining_seconds=500.0,
                            finish_ts=now_ts + 505.0,
                        ),
                    ),
                )
                rec_b = bridge._record_state(st_b, fetched_buildings=True, fetched_home=True, observed_active_planet=True)
                b_origins = {q.origin_coords for q in rec_b.active_queues if q.category == "building"}
                self.assertEqual(b_origins, {"02:380:01", "02:380:02"})
                self.assertEqual(len([q for q in rec_b.active_queues if q.category == "building"]), 2)
                self.assertEqual(bridge._count_active_builders(rec_b, now_ts=now_ts + 5.0), 2)

                # Non-home updates (e.g. galaxy scan) drop expired retained builds and never duplicate (origin, slot)
                from dataclasses import replace as _dc_replace
                bridge._last_state = _dc_replace(
                    rec_b,
                    active_queues=tuple(
                        list(rec_b.active_queues)
                        + [
                            QueueJob(
                                category="building",
                                name="Stale finished mine (Level 5)",
                                building_id=1,
                                target_level=5,
                                origin_coords="02:380:01",
                                queue_slot=2,
                                remaining_seconds=0.0,
                                finish_ts=now_ts - 120.0,
                            )
                        ]
                    ),
                )
                st_scan = GameState(
                    universe_id=31, user_id=77, nickname="cmdr", timestamp=now_ts + 10.0,
                    planets=(p1, p2), active_planet_id=602, active_queues=(),
                )
                rec_scan = bridge._record_state(st_scan, fetched_home=False, fetched_buildings=False, observed_active_planet=True)
                scan_b_jobs = [q for q in rec_scan.active_queues if q.category == "building"]
                self.assertEqual(len(scan_b_jobs), 2)
                self.assertEqual({(q.origin_coords, q.queue_slot) for q in scan_b_jobs}, {("02:380:01", 1), ("02:380:02", 1)})

                # Custom planet names from Config Optimizer propagate to fleet_planner_get, action_graph, buildings_eta, plan, and active_queues
                bridge._last_state = _dc_replace(
                    rec_b,
                    active_queues=tuple(
                        list(rec_b.active_queues)
                        + [
                            QueueJob(
                                category="fleet",
                                name="Station → [02:380:02] Colony",
                                detail="Outbound · 6 Small fighter",
                                origin_coords="02:380:01",
                                remaining_seconds=600.0,
                                finish_ts=now_ts + 600.0,
                            )
                        ]
                    ),
                )
                bridge.handle_command({"command": "rename_planet", "planet_id": 601, "name": "#PP Planet"})
                bridge.handle_command({"command": "rename_planet", "planet_id": 602, "name": "#TB Planet"})
                dash_after_rename = bridge.dashboard()
                self.assertTrue(
                    any("#TB Planet" in str(q.get("name", "")) for q in (dash_after_rename["state"].get("active_queues") or [])),
                )
                ag_cmd = bridge.handle_command({"command": "action_graph"})
                ag_labels = [l.get("label") for l in (ag_cmd["action_graph"].get("lanes") or [])]
                self.assertIn("#PP Planet [02:380:01]", ag_labels)
                self.assertIn("#TB Planet [02:380:02]", ag_labels)
                fp_cmd = bridge.handle_command({"command": "fleet_planner_get"})
                fp_names = {b.get("planet_name") for b in (fp_cmd.get("slot_budgets") or [])}
                self.assertIn("#PP Planet", fp_names)
                self.assertIn("#TB Planet", fp_names)
                eta_cmd = bridge.handle_command({"command": "buildings_eta", "planet_id": 602})
                self.assertEqual(eta_cmd["buildings_eta"]["planet"]["name"], "#TB Planet")
                plan_cmd = bridge.handle_command({"command": "plan", "refresh": False})
                for act_row in plan_cmd.get("actions") or []:
                    if act_row.get("planet_id") == 601:
                        self.assertEqual(act_row.get("planet_name"), "#PP Planet")

                # Default StrategyConfig page_view_limit_per_minute is 60 and live update_config syncs PageViewLimiter
                self.assertEqual(bridge.strategy_config.page_view_limit_per_minute, 60)
                self.assertEqual(bridge._page_limiter.max_views_per_minute, 60)
                bridge.handle_command({"command": "update_config", "config": {"page_view_limit_per_minute": 45}})
                self.assertEqual(bridge.strategy_config.page_view_limit_per_minute, 45)
                self.assertEqual(bridge._page_limiter.max_views_per_minute, 45)

                # 2. Hybrid AI-Planner P1 subrank prioritizes emergency THROTTLE -> BUILD -> TRANSPORT -> SPY -> non-emerg THROTTLE (#100)
                fake_ai_mod = MagicMock()
                fake_ai_mod.reconcile_live_ai_actions.return_value = (
                    [
                        PlannedAction(
                            priority="P1",
                            kind="WAIT_RESOURCES",
                            planet_id=602,
                            planet_coords="02:380:02",
                            target_name="Mine L10",
                            reason="Waiting on resources",
                            executable_now=True,
                        ),
                        PlannedAction(
                            priority="P1",
                            kind="BUILD",
                            planet_id=602,
                            planet_coords="02:380:02",
                            target_name="Mine L9",
                            reason="Parallel colony build",
                            executable_now=True,
                        ),
                    ],
                    [],
                )
                fake_ai_mod.compute_early_transport_needs.return_value = {}
                with (
                    patch.object(bridge, "_load_ai_planner_mod", return_value=fake_ai_mod),
                    patch(
                        "spaceinvasion.runtime.api_server.plan_fleet_and_attack_actions",
                        return_value=[
                            PlannedAction(
                                priority="P1",
                                kind="SPY",
                                planet_id=601,
                                planet_coords="02:380:01",
                                target_name="[02:380:09]",
                                reason="Spy target",
                                executable_now=True,
                            ),
                            PlannedAction(
                                priority="P1",
                                kind="TRANSPORT",
                                planet_id=601,
                                planet_coords="02:380:01",
                                target_name="[02:380:02]",
                                reason="Feed colony",
                                executable_now=True,
                            ),
                        ],
                    ),
                    patch(
                        "spaceinvasion.runtime.api_server.plan_ai_blast_furnace_throttles",
                        return_value=[
                            PlannedAction(
                                priority="P1",
                                kind="THROTTLE_BUILDING",
                                planet_id=601,
                                planet_coords="02:380:01",
                                target_name="Blast furnace 80%",
                                reason="Keep pig iron positive",
                                executable_now=True,
                            ),
                            PlannedAction(
                                priority="P1",
                                kind="THROTTLE_BUILDING",
                                planet_id=602,
                                planet_coords="02:380:02",
                                target_name="Blast furnace 0%",
                                reason="Negative energy emergency",
                                executable_now=True,
                            ),
                        ],
                    ),
                ):
                    ranked = bridge._plan_hybrid_ai_actions_locked(
                        rec_b,
                        {"summary": {}, "steps": []},
                        bridge.strategy_config,
                        record_conflicts=False,
                    )
                p1_order = [(a.kind, a.target_name) for a in ranked if a.priority == "P1"]
                self.assertEqual(
                    p1_order,
                    [
                        ("THROTTLE_BUILDING", "Blast furnace 0%"),
                        ("BUILD", "Mine L9"),
                        ("TRANSPORT", "[02:380:02]"),
                        ("SPY", "[02:380:09]"),
                        ("THROTTLE_BUILDING", "Blast furnace 80%"),
                        ("WAIT_RESOURCES", "Mine L10"),
                    ],
                )

                # 3. Dynamic universe_max_galaxies observation, persistence, early sweep abort & scanned_sectors (#103)
                self.assertEqual(bridge._max_galaxies_locked(), 14)
                retro_html = '<script>SI_TPL.galaxy = {"maxGalaxies": 8, "maxSystem": 400};</script>'

                class _FakeRetroGalaxyClient:
                    last_galaxy_nav_method = "arrow"
                    last_galaxy_max_galaxies = 8

                    def __enter__(self) -> "_FakeRetroGalaxyClient":
                        return self

                    def __exit__(self, *_a: Any) -> None:
                        return None

                    def ensure_in_game(self, **_kw: Any) -> None:
                        return None

                    def step_galaxy_system(
                        self,
                        *,
                        galaxy: int,
                        system: int,
                        pace: bool = False,
                        priority: str = "normal",
                        on_intermediate_page: Any = None,
                    ) -> tuple[str, int, int]:
                        # Server wraps non-existent galaxy 9 -> galaxy 1
                        return (retro_html, 1 if galaxy > 8 else galaxy, system)

                bridge._client_factory = lambda _port: _FakeRetroGalaxyClient()
                sw_res = bridge.run_galaxy_sweep(
                    pages=5,
                    galaxy=9,
                    start_system=10,
                    delay_range=(0.0, 0.0),
                    dry_run=False,
                    priority="interactive",
                )
                self.assertTrue(sw_res["ok"])
                self.assertEqual(bridge._max_galaxies_locked(), 8)
                self.assertEqual(bridge._db.load_meta_json("universe_max_galaxies"), 8)
                # Broke cleanly on first out-of-range page rather than hammering all 5 pages of galaxy 9!
                self.assertEqual(sw_res["pages_scanned"], 1)
                self.assertIn("maxGalaxies=8", sw_res["message"])
                self.assertLessEqual(sw_res["galaxy_sweep"]["cursor_galaxy"], 8)
                self.assertEqual(sw_res["galaxy_sweep"].get("last_arrow_steps"), 1)

                sec_res = bridge.handle_command({"command": "scanned_sectors"})
                self.assertEqual(sec_res["galaxies_available"], list(range(1, 9)))
                self.assertEqual(len(sec_res["galaxy_stats"]), 8)
                self.assertEqual(sec_res["summary"]["universe_total_systems"], 3200)
            finally:
                bridge.close()

    def test_perf_and_stability_regressions_toctou_plan_fastpath_unknown_cmd_and_cmd_reconnect(self) -> None:
        import io
        import threading
        import time
        from contextlib import redirect_stdout
        import requests
        import bin.commander as commander_cli

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "perf_reg.db"
            client_calls = 0

            def _counting_factory(_port: int) -> Any:
                nonlocal client_calls
                client_calls += 1
                raise AssertionError("Should not open Selenium client on cached plan(refresh=False)")

            bridge = GameApiBridge(
                debug_port=9222,
                db_path=db_path,
                log_dir=None,
                client_factory=_counting_factory,
                ensure_session_fn=lambda: None,
            )
            try:
                st = _sample_state()
                bridge._record_state(
                    st,
                    fetched_buildings=False,
                    fetched_research=False,
                    fetched_shipyard=False,
                    fetched_home=False,
                    observed_active_planet=True,
                )
                # Force snapshot to look stale (>15s) to prove plan(refresh=False) never scrapes Chrome!
                bridge._last_snapshot_at = time.monotonic() - 120.0

                # 1. plan(refresh=False) reuses _last_state with 0 Selenium client calls
                p_res = bridge.handle_command({"command": "plan", "refresh": False})
                self.assertTrue(p_res["ok"])
                self.assertEqual(p_res["command"], "plan")
                self.assertEqual(client_calls, 0)

                # 2. dashboard(refresh=False) caches _plan_with_summary on repeated calls
                with patch.object(
                    bridge,
                    "_plan_with_summary",
                    wraps=bridge._plan_with_summary,
                ) as spy_plan:
                    d1 = bridge.dashboard(refresh=False)
                    d2 = bridge.dashboard(refresh=False)
                    d3 = bridge.dashboard(refresh=False)
                self.assertEqual(spy_plan.call_count, 1)
                self.assertEqual(len(d1["plan"]), len(d2["plan"]))
                self.assertEqual(len(d2["plan"]), len(d3["plan"]))

                # 3. rename_planet patches _last_dashboard_payload safely (never sets it to None)
                ren = bridge.handle_command(
                    {"command": "rename_planet", "planet_id": st.planets[0].planet_id, "name": "Home Prime"}
                )
                self.assertTrue(ren["ok"])
                self.assertIsInstance(bridge._last_dashboard_payload, dict)
                self.assertEqual(
                    bridge._last_dashboard_payload["custom_planet_names"].get(str(st.planets[0].planet_id)),
                    "Home Prime",
                )

                # 4. Unknown command rejects BEFORE acquiring bridge._lock, and dashboard(refresh=False)
                #    returns safe busy fallback without crashing even if another thread holds bridge._lock
                lock_held = threading.Event()
                release_lock = threading.Event()

                def _hold_lock() -> None:
                    with bridge._lock:
                        lock_held.set()
                        release_lock.wait(timeout=5.0)

                holder = threading.Thread(target=_hold_lock, daemon=True)
                holder.start()
                self.assertTrue(lock_held.wait(timeout=2.0))
                try:
                    t0 = time.perf_counter()
                    with self.assertRaises(ApiRequestError) as ctx:
                        bridge.handle_command({"command": "totally_nonexistent_cmd"})
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    self.assertIn("Unknown command", str(ctx.exception))
                    self.assertLess(elapsed_ms, 500.0)

                    # Simulate concurrent mutation of _last_dashboard_payload -> None after read starts
                    orig_payload = bridge._last_dashboard_payload
                    try:
                        # While lock is held (>2s), dashboard(refresh=False) must return busy=True dict cleanly
                        dash_busy = bridge.dashboard(refresh=False)
                        self.assertTrue(dash_busy.get("busy"))
                        self.assertIn("bot_control", dash_busy)
                    finally:
                        bridge._last_dashboard_payload = orig_payload
                finally:
                    release_lock.set()
                    holder.join(timeout=2.0)
            finally:
                bridge.close()

        # 5. Commander --loop cleanly waits for API reconnect on ConnectionRefusedError without raw urllib3 noise
        conn_err = requests.exceptions.ConnectionError(
            "HTTPConnectionPool(host='127.0.0.1', port=8787): Max retries exceeded with url: /state "
            "(Caused by NewConnectionError('Failed to establish a new connection: [Errno 111] Connection refused'))"
        )
        fake_api = MagicMock()
        fake_api.get_bot_control.side_effect = [conn_err, conn_err]
        fake_api.health.side_effect = [conn_err, {"ok": True}]
        buf = io.StringIO()
        with (
            patch.object(commander_cli, "GameApiClient", return_value=fake_api),
            patch.object(commander_cli.time, "sleep", return_value=None),
            redirect_stdout(buf),
        ):
            rc = commander_cli.main(
                ["--api-url", "http://127.0.0.1:8787", "--loop", "--interval", "1", "--max-ticks", "1"]
            )
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("API unavailable", out)
        self.assertNotIn("HTTPConnectionPool", out)
        self.assertNotIn("NewConnectionError", out)

    def test_spy_reserved_slots_only_and_config_optimizer_goals_api(self) -> None:
        """Verify spy_reserved_slots_only validation/holds/live guard, fleet planner slot fields, and config_optimizer_goals."""
        from dataclasses import replace
        import time
        from spaceinvasion.core.config import save_strategy_config
        from spaceinvasion.planner.action_graph import build_fleet_planner_graph

        p1 = PlanetState(
            planet_id=101,
            name="Home",
            coords="02:380:12",
            planet_type="normal",
            temperature=30,
            fields_used=90,
            fields_max=230,
            resources=Resources(50_000, 30_000, 20_000, 15_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(500, 250),
            buildings={"b1": 12, "b2": 10, "b3": 10, "b4": 8, "b5": 12, "b10": 3},  # max_slots = 4
            ships={"s10": 10, "s7": 10, "s2": 8},
        )
        st = GameState(
            universe_id=31,
            user_id=1,
            nickname="Cmdr",
            timestamp=time.time(),
            planets=(p1,),
            active_planet_id=101,
            researches={"r1": 5, "r2": 6, "r6": 3},
            active_queues=(
                QueueJob(
                    category="fleet",
                    name="Espionage → 02:380:04",
                    origin_coords="02:380:12",
                    remaining_seconds=60.0,
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            cfg_path = tp / "config.yaml"
            save_strategy_config(
                StrategyConfig(
                    allow_raids=True,
                    allow_galaxy_scan=True,
                    reserved_spy_slots=1,
                    spy_reserved_slots_only=False,
                ),
                cfg_path,
            )
            fake_client = MagicMock()
            bridge = GameApiBridge(
                client_factory=lambda: fake_client,
                config_path=cfg_path,
                db_path=tp / "state.db",
            )
            try:
                bridge._last_state = st
                bridge._last_snapshot_at = time.monotonic()

                # 1. action_graph strictly rejects non-bool spy_reserved_slots_only; update_config persists bool
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command(
                        {"command": "action_graph", "config": {"spy_reserved_slots_only": "yes"}}
                    )
                upd = bridge.handle_command(
                    {"command": "update_config", "config": {"spy_reserved_slots_only": True}}
                )
                self.assertTrue(upd["ok"])
                self.assertTrue(upd["config"]["spy_reserved_slots_only"])
                self.assertTrue(bridge.strategy_config.spy_reserved_slots_only)

                # 2. _apply_fleet_slot_holds enforces strict reserved spy cap AND releases remaining slots to ATTACK
                spy_a = PlannedAction(
                    priority="P1",
                    kind="SPY",
                    planet_id=101,
                    planet_coords="02:380:12",
                    target_id=10,
                    target_level=1,
                    target_name="02:380:05",
                    cost=Resources(),
                    missing=Resources(),
                    reason="Spy 02:380:05",
                    executable_now=True,
                )
                atk_a = PlannedAction(
                    priority="P2",
                    kind="ATTACK",
                    planet_id=101,
                    planet_coords="02:380:12",
                    target_id=0,
                    target_level=0,
                    target_name="02:380:08",
                    cost=Resources(),
                    missing=Resources(),
                    reason="Raid 02:380:08",
                    executable_now=True,
                )
                # 1 SPY already in flight (1/1 spy slot used) + 2 non-SPY flights -> 3/4 total slots used:
                st_3used = replace(
                    st,
                    active_queues=(
                        QueueJob(category="fleet", name="Espionage → 02:380:04", origin_coords="02:380:12", remaining_seconds=60.0),
                        QueueJob(category="fleet", name="Attack → 02:380:01", origin_coords="02:380:12", remaining_seconds=120.0),
                        QueueJob(category="fleet", name="Attack → 02:380:02", origin_coords="02:380:12", remaining_seconds=120.0),
                    ),
                )
                held = bridge._apply_fleet_slot_holds(st_3used, [spy_a, atk_a])
                self.assertFalse(held[0].executable_now)
                self.assertIn("restricted to reserved", held[0].reason.lower())
                # ATTACK remains executable because the 1 reserved spy slot is already occupied by in-flight SPY!
                self.assertTrue(held[1].executable_now)

                # 3. Live _execute_and_summarize aborts SPY when spy_used >= spy_cap (0 executor calls)
                exec_res = bridge._execute_and_summarize(fake_client, st_3used, "execute_action", spy_a, False)
                self.assertTrue(exec_res["ok"])
                self.assertFalse(exec_res["executed"])
                self.assertIn("restricted to reserved", exec_res["message"].lower())

                # 4. build_fleet_planner_graph surfaces spy_used_slots / reserved_spy_slots / spy_reserved_slots_only
                fgraph = build_fleet_planner_graph(st_3used, [], config=bridge.strategy_config)
                sb0 = fgraph["slot_budgets"][0]
                self.assertEqual(sb0["spy_used_slots"], 1)
                self.assertEqual(sb0["reserved_spy_slots"], 1)
                self.assertTrue(sb0["spy_reserved_slots_only"])

                # 5. config_optimizer_goals exposed in dashboard & buildings_eta and refreshes when active run updates
                d_empty = bridge.dashboard(refresh=False)
                self.assertIsNone(d_empty["config_optimizer_goals"])
                bridge._db.record_config_optimizer_run(
                    {
                        "summary": {"total_sp_eq_per_hr": 12345.0},
                        "planets": [
                            {
                                "planet_id": 101,
                                "coords": "02:380:12",
                                "detected_specialization": "pig_iron",
                                "buildings": [
                                    {"key": "b1", "building_id": 1, "built_level": 12, "target_level": 24},
                                    {"key": "b2", "building_id": 2, "built_level": 10, "target_level": 20},
                                ],
                            }
                        ],
                    },
                    set_active=True,
                )
                d_pop = bridge.dashboard(refresh=False)
                g_dash = d_pop["config_optimizer_goals"]
                self.assertIsNotNone(g_dash)
                self.assertEqual(g_dash["by_planet_id"]["101"]["specialization"], "pig_iron")
                self.assertEqual(g_dash["by_planet_id"]["101"]["goals"]["b1"], 24)
                self.assertEqual(g_dash["by_planet_id"]["101"]["goals"]["b2"], 20)
                self.assertEqual(g_dash["by_coords"]["02:380:12"]["goals"]["b1"], 24)

                eta_res = bridge.handle_command({"command": "buildings_eta", "refresh": False, "planet_id": 101})
                self.assertTrue(eta_res["ok"])
                self.assertEqual(eta_res["config_optimizer_goals"]["by_planet_id"]["101"]["goals"]["b1"], 24)
                self.assertEqual(
                    eta_res["buildings_eta"]["config_optimizer_goals"]["by_planet_id"]["101"]["goals"]["b2"],
                    20,
                )
            finally:
                bridge.close()

    def test_plan_v2_adaptive_config_24h_history_buckets_and_ai_planner_get(self) -> None:
        """Verify Plan v2 Step 0/4a API contracts: ai_adaptive_execution config, 24h history limit, 10m idle buckets, transport util EWMA, and ai_planner_get enrichment."""
        import time
        from spaceinvasion.core.config import save_strategy_config

        now_ts = time.time()
        p1 = PlanetState(
            planet_id=701,
            name="Alpha",
            coords="02:380:01",
            planet_type="normal",
            temperature=30,
            fields_used=60,
            fields_max=300,
            resources=Resources(50_000, 10_000, 20_000, 15_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(800, 400),
            buildings={"b1": 12, "b2": 8, "b3": 10, "b4": 8, "b5": 12, "b9": 6, "b10": 4},
            economy_rates={"resource2": 0},
            ships={"s1": 10, "s2": 2},
        )
        p2 = PlanetState(
            planet_id=702,
            name="Beta",
            coords="02:380:02",
            planet_type="normal",
            temperature=25,
            fields_used=55,
            fields_max=300,
            resources=Resources(40_000, 500, 15_000, 10_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(700, 350),
            buildings={"b1": 10, "b2": 6, "b3": 9, "b4": 7, "b5": 11, "b9": 4, "b10": 3},
            economy_rates={"resource2": 100},
            ships={"s1": 4},
        )
        st = GameState(
            universe_id=31,
            user_id=1,
            nickname="Cmdr",
            timestamp=now_ts,
            planets=(p1, p2),
            active_planet_id=701,
            researches={"r1": 5, "r2": 6, "r6": 3},
            specs={"miner": 0},
            active_queues=(
                QueueJob(
                    category="building",
                    name="Pig iron mine",
                    origin_coords="02:380:01",
                    building_id=1,
                    target_level=13,
                    remaining_seconds=600.0,
                    finish_ts=now_ts + 600.0,
                ),
                QueueJob(
                    category="fleet",
                    name="Transport → 02:380:02",
                    detail="Outbound · 5 Small transport",
                    origin_coords="02:380:01",
                    remaining_seconds=300.0,
                    finish_ts=now_ts + 300.0,
                    cargo=Resources(0, 24_000, 0, 0),
                    expected_cargo=Resources(0, 24_000, 0, 0),
                ),
            ),
        )
        fake_plan = {
            "schema_version": 1,
            "generated_at": "2026-11-19T00:00:00+00:00",
            "config_generated_at": "2026-11-19T00:00:00+00:00",
            "summary": {
                "colonies_founded": 2,
                "colonies_target": 2,
                "planned_cargo_per_hour_24h": 150_000.0,
            },
            "search": {
                "best_strategy": "tuned_ga",
                "best_parameters": {"patience": 0.3, "speed_weight": 1.6},
                "tuned_genes": {"patience": 0.35, "speed_weight": 1.75},
                "best_reward": 123456.0,
                "genetic_algorithm_ran": True,
                "why": "improved",
            },
            "transport_demand": {
                "cargo_4h": 400_000,
                "cargo_per_hour_4h": 100_000.0,
                "cargo_24h": 3_600_000,
                "cargo_per_hour_24h": 150_000.0,
                "max_single_shipment_cargo": 60_000,
                "shipment_steps_4h": 5,
                "shipment_steps_24h": 20,
                "avg_one_way_seconds": 720.0,
                "round_trips_per_hour": 2.5,
                "utilization_assumed": 0.5,
                "recommended_cargo_capacity": 120_000,
                "recommended_large_transports": 4,
                "recommended_small_transports": 20,
            },
            "steps": [
                {
                    "seq": 1,
                    "week": 1,
                    "kind": "BUILD",
                    "planet_id": 702,
                    "planet_coords": "02:380:02",
                    "planet_name": "Beta",
                    "entity_key": "b3",
                    "entity_name": "Kryptonite mine",
                    "target_level": 10,
                    "cost": {"pig_iron": 10_000, "metal": 120_000, "kryptonite": 5_000, "spice": 0},
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            cfg_path = tp / "config.yaml"
            db_path = tp / "state.db"
            save_strategy_config(StrategyConfig(planner_mode="ai_planner", max_planets=2), cfg_path)
            bridge = GameApiBridge(
                client_factory=lambda _p: MagicMock(),
                config_path=cfg_path,
                db_path=db_path,
                log_dir=None,
            )
            try:
                # 1. Default ai_adaptive_execution is False; strict bool validation rejects non-bool
                self.assertFalse(bridge.strategy_config.ai_adaptive_execution)
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command(
                        {"command": "action_graph", "config": {"ai_adaptive_execution": "yes"}}
                    )
                upd = bridge.handle_command(
                    {"command": "update_config", "config": {"ai_adaptive_execution": True}}
                )
                self.assertTrue(upd["ok"])
                self.assertTrue(upd["config"]["ai_adaptive_execution"])
                self.assertTrue(bridge.strategy_config.ai_adaptive_execution)
                upd_off = bridge.handle_command(
                    {"command": "update_config", "config": {"ai_adaptive_execution": False}}
                )
                self.assertFalse(upd_off["config"]["ai_adaptive_execution"])

                # 2. 24h hourly_production_history returns > 120 points (limit=1440)
                base_min = (int(now_ts // 60) - 165) * 60
                for idx in range(160):
                    bridge._db.record_hourly_production(
                        pig_iron_per_hour=1000.0 + idx,
                        metal_per_hour=500.0,
                        kryptonite_per_hour=300.0,
                        spice_per_hour=100.0,
                        total_per_hour=1900.0 + idx,
                        speq_per_hour=800.0,
                        planet_count=2,
                        active_builders=1,
                        ts=float(base_min + idx * 60),
                    )
                bridge._db.record_ai_planner_run(fake_plan, config_run_id=1, set_active=True)
                bridge._record_state(st, fetched_buildings=True, fetched_home=True, observed_active_planet=True)
                bridge.handle_command({"command": "rename_planet", "planet_id": 702, "name": "Smelter Prime"})

                dash = bridge.dashboard(refresh=False)
                self.assertFalse(dash["config"]["ai_adaptive_execution"])
                self.assertGreaterEqual(len(dash["hourly_production_history"]), 160)

                # 3. F2: Outbound-only transport does NOT skew realized_utilization away from 0.5 baseline;
                #    only persisted returning-leg deliveries update throughput utilization once >=4 deliveries span >=1h!
                self.assertAlmostEqual(bridge._transport_realized_utilization, 0.5, places=4)
                from dataclasses import replace as _dc_replace

                # Simulate 4 returning transport deliveries over 2 hours (20k each -> 80k / 2h = 40k/h on 174k*2.5 = 435k/h -> clamped 0.2;
                # use 80k each -> 320k / 2h = 160k/h vs 174k * 2.5 = 435k/h -> 0.3678 throughput utilization)
                deliv_offsets = (7200.0, 5400.0, 3600.0, 1800.0)
                for idx_d, age_s in enumerate(deliv_offsets):
                    q_ret = QueueJob(
                        category="fleet",
                        name="Transport ↩ 02:380:02",
                        detail="Returning · 5 Small transport",
                        origin_coords="02:380:01",
                        remaining_seconds=120.0,
                        finish_ts=(now_ts - age_s) + 120.0,
                        delivered=True,
                        delivered_cargo=Resources(0, 80_000, 0, 0),
                    )
                    st_ret = _dc_replace(st, active_queues=(st.active_queues[0], q_ret))
                    self.assertTrue(bridge._observe_transport_utilization_locked(st_ret, now_ts=now_ts - age_s))
                    # Repeating the exact same returning flight does NOT double-count
                    self.assertFalse(bridge._observe_transport_utilization_locked(st_ret, now_ts=now_ts - age_s + 15.0))
                u_first = bridge._recompute_transport_utilization_locked(rt_per_hour=2.5, now_ts=now_ts)
                # 320,000 / 2.0h = 160,000/h; theoretical = 174,000 * 2.5 = 435,000/h -> 0.3678
                self.assertAlmostEqual(u_first, round(160_000.0 / 435_000.0, 4), places=3)
                self.assertGreaterEqual(u_first, 0.2)
                self.assertLessEqual(u_first, 0.9)

                # 4. 10-minute 24h idle bucket deduplication, pruning >24h, and persistence across restart
                #    One bucket was already recorded by _record_state; re-recording in same 10m slot is a no-op
                self.assertFalse(
                    bridge._record_adaptive_idle_bucket_locked(st, fake_plan, now_ts=now_ts)
                )
                #    Record a previous 10m bucket + inject an expired (>24h) bucket
                self.assertTrue(
                    bridge._record_adaptive_idle_bucket_locked(st, fake_plan, now_ts=now_ts - 600.0)
                )
                old_bk = int((now_ts - 200 * 600.0) // 600.0)
                bridge._adaptive_idle_buckets[old_bk] = {
                    "bucket": old_bk,
                    "ts": now_ts - 200 * 600.0,
                    "total_colonies": 2,
                    "active_builders": 0,
                    "idle_with_affordable": 2,
                    "ready_not_started": 0,
                    "counts": {"waiting_metal": 2},
                }
                bridge._persist_runtime_state()
                self.assertNotIn(old_bk, bridge._adaptive_idle_buckets)
                self.assertEqual(len(bridge._adaptive_idle_buckets), 2)

                # 5. ai_planner_get exposes search, transport_demand, and adaptive_status with custom names & delivered/h
                ai_get = bridge.handle_command({"command": "ai_planner_get"})
                self.assertTrue(ai_get["ok"])
                self.assertEqual(ai_get["search"]["best_strategy"], "tuned_ga")
                self.assertEqual(ai_get["search"]["tuned_genes"]["patience"], 0.35)
                self.assertEqual(ai_get["search"]["best_parameters"]["speed_weight"], 1.6)
                self.assertEqual(ai_get["transport_demand"]["cargo_per_hour_24h"], 150_000.0)
                self.assertAlmostEqual(ai_get["transport_demand"]["realized_utilization"], u_first, places=3)

                astat = ai_get["adaptive_status"]
                self.assertIsNotNone(astat)
                self.assertFalse(astat["enabled"])
                self.assertEqual(astat["active_builders"], 1)
                self.assertEqual(astat["total_colonies"], 2)
                col_by_pid = {c["planet_id"]: c for c in astat["colonies"]}
                self.assertEqual(col_by_pid[702]["name"], "Smelter Prime")
                self.assertEqual(astat["window_24h"]["bucket_count"], 2)
                self.assertEqual(astat["window_24h"]["colony_samples"], 4)
                # Parked: 14*6000 + 2*30000 = 144,000; In-flight: 5*6000 = 30,000 -> total 174,000
                tf = astat["transport_fleet"]
                self.assertEqual(tf["owned_parked_cargo"], 144_000)
                self.assertEqual(tf["in_flight_cargo"], 30_000)
                self.assertEqual(tf["total_fleet_cargo"], 174_000)
                self.assertEqual(tf["planned_cargo_per_hour_24h"], 150_000.0)
                self.assertEqual(tf["delivered_cargo_24h"], 320_000)
                self.assertEqual(tf["delivered_flights_24h"], 4)
                self.assertGreater(tf["delivered_cargo_per_hour_24h"], 150_000.0)
                self.assertEqual(tf["delivered_cargo_per_hour_24h"], tf["delivered_cargo_per_hour"])
                self.assertIsNotNone(tf["delivered_vs_planned_pct"])
                self.assertAlmostEqual(tf["realized_utilization"], u_first, places=3)
                # Sizing never uses a utilization below the assumed 0.5: an idle fleet does not grow the target.
                self.assertEqual(tf["sizing_utilization"], max(0.5, tf["realized_utilization"]))
                self.assertEqual(tf["target_fleet_cargo"], math.ceil(150_000.0 / 2.5 / tf["sizing_utilization"]))
                self.assertEqual(astat["auto_replan"]["cooldown_seconds"], 1800)

                # R2: Tab 3 summary and planner share identical delivery-time capacity basis even if current fleet grows 5x
                st_big_fleet = _dc_replace(
                    st,
                    planets=(
                        _dc_replace(p1, ships={"s1": 50, "s2": 10}),
                        _dc_replace(p2, ships={"s1": 20}),
                    ),
                )
                tf_big = bridge._build_transport_fleet_summary_locked(st_big_fleet, fake_plan["transport_demand"])
                u_planner_big = bridge._recompute_transport_utilization_locked(rt_per_hour=2.5, now_ts=now_ts)
                self.assertEqual(tf_big["realized_utilization"], u_planner_big)
                self.assertEqual(tf_big["realized_utilization"], bridge._transport_realized_utilization)

                # F6: ai_planner_get gracefully degrades if diagnose_ai_colony_idle_states raises
                import spaceinvasion.planner.ai_planner as _ai_mod_err

                with patch.object(_ai_mod_err, "diagnose_ai_colony_idle_states", side_effect=RuntimeError("boom")):
                    ai_get_err = bridge.handle_command({"command": "ai_planner_get"})
                self.assertTrue(ai_get_err["ok"])
                self.assertIsNotNone(ai_get_err["adaptive_status"])
                self.assertEqual(ai_get_err["adaptive_status"]["colonies"], [])
            finally:
                bridge.close()

            # 6. Verify buckets, delivered events & throughput util persist across restart (zero re-sampling!)
            bridge2 = GameApiBridge(
                client_factory=lambda _p: MagicMock(),
                config_path=cfg_path,
                db_path=db_path,
                log_dir=None,
            )
            try:
                self.assertEqual(len(bridge2._adaptive_idle_buckets), 2)
                self.assertEqual(len(bridge2._delivered_transport_events), 4)
                self.assertAlmostEqual(bridge2._transport_realized_utilization, u_first, places=3)
            finally:
                bridge2.close()

    def test_plan_v2_hybrid_wiring_and_step6_drift_auto_replan(self) -> None:
        """Verify hybrid wiring (inbound cargo, in-progress rem, donor smelting, transport_demand) and Step 6 drift auto-replan + 30m cooldown without _lock deadlock."""
        from dataclasses import replace
        import time
        from spaceinvasion.core.config import save_strategy_config

        now_ts = time.time()
        p1 = PlanetState(
            planet_id=801,
            name="Prime",
            coords="02:380:01",
            planet_type="normal",
            temperature=30,
            fields_used=60,
            fields_max=300,
            resources=Resources(100_000, 10_000, 20_000, 15_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(800, 400),
            buildings={"b1": 12, "b2": 8, "b3": 10, "b4": 8, "b5": 12, "b9": 6, "b10": 4},
            economy_rates={"resource2": 0},
            ships={"s1": 10},
        )
        p2 = PlanetState(
            planet_id=802,
            name="Secundus",
            coords="02:380:02",
            planet_type="normal",
            temperature=25,
            fields_used=55,
            fields_max=300,
            resources=Resources(50_000, 5_000, 15_000, 10_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(700, 350),
            buildings={"b1": 10, "b2": 6, "b3": 9, "b4": 7, "b5": 11, "b9": 4, "b10": 3},
            economy_rates={"resource2": 0},
            ships={"s1": 4},
        )
        st = GameState(
            universe_id=31,
            user_id=1,
            nickname="Cmdr",
            timestamp=now_ts,
            planets=(p1, p2),
            active_planet_id=801,
            researches={"r1": 5, "r2": 6, "r6": 3},
            active_queues=(
                QueueJob(
                    category="building",
                    name="Pig iron mine",
                    origin_coords="02:380:01",
                    building_id=1,
                    target_level=13,
                    remaining_seconds=900.0,
                    finish_ts=now_ts + 900.0,
                ),
                QueueJob(
                    category="fleet",
                    name="Transport → 02:380:02",
                    detail="Outbound · 5 Small transport",
                    origin_coords="02:380:01",
                    remaining_seconds=300.0,
                    finish_ts=now_ts + 300.0,
                    cargo=Resources(0, 15_000, 0, 0),
                ),
            ),
        )
        plan_res = {
            "schema_version": 1,
            "generated_at": "2026-11-19T00:00:00+00:00",
            "config_generated_at": "2026-11-19T00:00:00+00:00",
            "summary": {"colonies_founded": 2, "colonies_target": 2},
            "attack_telemetry": {"median_loot": {"spice_eq": 10_000.0}},
            "search": {
                "best_strategy": "tuned",
                "best_parameters": {"patience": 0.25},
                "tuned_genes": {"patience": 0.42, "speed_weight": 1.8},
            },
            "transport_demand": {
                "cargo_per_hour_24h": 200_000.0,
                "round_trips_per_hour": 2.5,
                "utilization_assumed": 0.5,
            },
            "steps": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            cfg_path = tp / "config.yaml"
            db_path = tp / "state.db"
            save_strategy_config(
                StrategyConfig(planner_mode="ai_planner", max_planets=2, ai_adaptive_execution=False),
                cfg_path,
            )
            bridge = GameApiBridge(
                client_factory=lambda _p: MagicMock(),
                config_path=cfg_path,
                db_path=db_path,
                log_dir=None,
            )
            try:
                bridge._last_state = st
                bridge._last_snapshot_at = time.monotonic()

                # 1. Verify _plan_hybrid_ai_actions_locked wires new kwargs for both switch OFF and switch ON
                for adaptive_flag in (False, True):
                    cfg_cur = StrategyConfig(
                        planner_mode="ai_planner",
                        max_planets=2,
                        ai_adaptive_execution=adaptive_flag,
                    )
                    with (
                        patch(
                            "spaceinvasion.runtime.api_server.compute_spare_ship_budget",
                            return_value={
                                "available_budget": {"pig_iron": 0.0, "metal": 0.0, "kryptonite": 0.0, "spice": 0.0},
                                "infra_transport_budget": {"metal": 50_000.0, "kryptonite": 30_000.0},
                                "urgent_zero_count_ships": [],
                            },
                        ) as m_budget,
                        patch(
                            "spaceinvasion.runtime.api_server.plan_fleet_and_attack_actions",
                            return_value=[],
                        ) as m_fleet,
                        patch(
                            "spaceinvasion.runtime.api_server.plan_ai_blast_furnace_throttles",
                            return_value=[],
                        ) as m_bf,
                    ):
                        bridge._plan_hybrid_ai_actions_locked(st, plan_res, cfg_cur, record_conflicts=False)
                    self.assertEqual(m_budget.call_args.kwargs["adaptive_execution"], adaptive_flag)
                    self.assertIs(m_budget.call_args.kwargs["config"], cfg_cur)
                    td_arg = m_fleet.call_args.kwargs["transport_demand"]
                    self.assertEqual(m_bf.call_args.kwargs["enable_donor_smelting"], adaptive_flag)
                    if adaptive_flag:
                        self.assertEqual(td_arg["cargo_per_hour_24h"], 200_000.0)
                        self.assertIn("realized_utilization", td_arg)
                        self.assertEqual(m_bf.call_args.kwargs["inbound_cargo_by_planet"][802].metal, 15_000)
                        self.assertGreater(m_bf.call_args.kwargs["in_progress_rem_by_planet"][801], 0.0)
                        self.assertIsNotNone(m_bf.call_args.kwargs["in_progress_research_rem_seconds"])
                        self.assertIsNotNone(m_budget.call_args.kwargs["infra_spend_last_hour"])
                    else:
                        # F6: switch-off path executes zero adaptive helpers and passes None
                        self.assertIsNone(td_arg)
                        self.assertIsNone(m_bf.call_args.kwargs["inbound_cargo_by_planet"])
                        self.assertIsNone(m_bf.call_args.kwargs["in_progress_rem_by_planet"])
                        self.assertIsNone(m_bf.call_args.kwargs["in_progress_research_rem_seconds"])
                        self.assertIsNone(m_budget.call_args.kwargs["infra_spend_last_hour"])

                # F3: infra transport spend ledger records live builds and debits 1h spend
                bridge._record_infra_transport_spend_locked(
                    PlannedAction(
                        priority="P2",
                        kind="BUILD_SHIP",
                        planet_id=801,
                        planet_coords="02:380:01",
                        target_id=2,
                        target_name="Large transport",
                        target_level=2,
                        cost=Resources(0, 16_000, 12_000, 0),
                        reason="Fleet build target [Plan infra cargo target]",
                        executable_now=True,
                    ),
                    now_ts=now_ts,
                )
                sp_1h, carry_1h = bridge._get_infra_transport_spend_last_hour_locked(now_ts=now_ts + 60.0)
                self.assertEqual(sp_1h, {"metal": 16_000.0, "kryptonite": 12_000.0})
                self.assertEqual(carry_1h, 1.0)
                # After 1h expires + 2h idle, ledger drains to 0 and carryover reaches 2h
                sp_after, carry_after = bridge._get_infra_transport_spend_last_hour_locked(now_ts=now_ts + 7200.0)
                self.assertEqual(sp_after, {"metal": 0.0, "kryptonite": 0.0})
                self.assertAlmostEqual(carry_after, 2.0, places=2)

                # 2. Step 6 drift auto-replan: seed active config optimizer + active AI plan aged 5 hours
                bridge._active_config_opt_mem = {
                    "run_id": 10,
                    "updated_ts": now_ts - 18_000.0,
                    "result": {"generated_at": "2026-11-19T00:00:00+00:00"},
                }
                bridge._db.close()
                bridge._db = None
                bridge._active_ai_plan_mem = {
                    "run_id": 20,
                    "config_run_id": 10,
                    "updated_ts": now_ts - 18_000.0,  # 5 hours old
                    "result": dict(plan_res),
                }

                calls: list[dict[str, object]] = []

                def _fake_start_ai(**kwargs: object) -> dict[str, object]:
                    # Prove self._lock is NOT held (zero deadlock risk!)
                    acquired = bridge._lock.acquire(blocking=False)
                    self.assertTrue(acquired, "_start_ai_planner_job called while bridge._lock was held!")
                    if acquired:
                        bridge._lock.release()
                    calls.append(dict(kwargs))
                    return {"ok": True, "job": {"status": "running"}}

                with (
                    patch.object(bridge, "_start_ai_planner_job", side_effect=_fake_start_ai),
                    patch.object(
                        bridge,
                        "_compute_live_attack_telemetry_locked",
                        return_value={"median_loot": {"spice_eq": 10_000.0}},
                    ),
                ):
                    # Switch OFF -> never triggers even when 5h old
                    self.assertIsNone(bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts))
                    self.assertEqual(len(calls), 0)

                    # Switch ON, paused -> never triggers
                    bridge.strategy_config = StrategyConfig(
                        planner_mode="ai_planner", max_planets=2, ai_adaptive_execution=True
                    )
                    bridge._bot_control["paused"] = True
                    self.assertIsNone(bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts))
                    self.assertEqual(len(calls), 0)
                    bridge._bot_control["paused"] = False

                    # Switch ON, CAPTCHA blocked -> never triggers
                    bridge._bot_control["captcha_blocked"] = True
                    self.assertIsNone(bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts))
                    self.assertEqual(len(calls), 0)
                    bridge._bot_control["captcha_blocked"] = False

                    # Switch ON, job running -> never triggers
                    bridge._ai_planner_job["status"] = "running"
                    self.assertIsNone(bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts))
                    self.assertEqual(len(calls), 0)
                    bridge._ai_planner_job["status"] = "idle"

                    # F8: planner_mode != ai_planner -> never triggers even with switch ON and 5h old plan
                    bridge.strategy_config = StrategyConfig(
                        planner_mode="priority", max_planets=2, ai_adaptive_execution=True
                    )
                    self.assertIsNone(bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts))
                    self.assertEqual(len(calls), 0)
                    bridge.strategy_config = StrategyConfig(
                        planner_mode="ai_planner", max_planets=2, ai_adaptive_execution=True
                    )

                    # F5: when bridge._lock is held, _maybe_trigger_ai_drift_replan returns None non-blockingly
                    with bridge._lock:
                        self.assertIsNone(bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts))
                    self.assertEqual(len(calls), 0)

                    # Trigger (d): plan_age_4h via handle_command("plan", refresh=False)
                    bridge.handle_command({"command": "plan", "refresh": False})
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(calls[-1]["trigger_source"], "auto_drift:plan_age_4h")
                    self.assertFalse(calls[-1]["sync"])
                    self.assertFalse(calls[-1]["refresh"])
                    self.assertEqual(calls[-1]["params"], {"warm_start_genes": {"patience": 0.42, "speed_weight": 1.8}})

                    # Immediate retry within 30-min cooldown -> suppressed
                    self.assertIsNone(bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts + 600.0))
                    self.assertEqual(len(calls), 1)

                    # Trigger (a): config_optimizer_changed (after 30m cooldown, plan itself only 1h old)
                    bridge._last_auto_ai_replan_ts = now_ts - 2000.0
                    bridge._active_ai_plan_mem["updated_ts"] = now_ts - 3600.0
                    bridge._active_config_opt_mem["run_id"] = 11
                    bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts)
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(calls[-1]["trigger_source"], "auto_drift:config_optimizer_changed")

                    # Trigger (b): colony_count_changed (reset co_run_id match, add 3rd normal planet)
                    bridge._active_config_opt_mem["run_id"] = 10
                    bridge._last_auto_ai_replan_ts = now_ts - 2000.0
                    p3 = replace(p2, planet_id=803, name="Tertius", coords="02:380:03")
                    bridge._last_state = replace(st, planets=(p1, p2, p3))
                    bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts)
                    self.assertEqual(len(calls), 3)
                    self.assertEqual(calls[-1]["trigger_source"], "auto_drift:colony_count_changed")

                # Trigger (c): loot_shift_25pct (back to 2 colonies, live median spice_eq shifted 10k -> 13k = +30%)
                bridge._last_state = st
                bridge._last_auto_ai_replan_ts = now_ts - 2000.0
                with (
                    patch.object(bridge, "_start_ai_planner_job", side_effect=_fake_start_ai),
                    patch.object(
                        bridge,
                        "_compute_live_attack_telemetry_locked",
                        return_value={"median_loot": {"spice_eq": 13_000.0}},
                    ),
                ):
                    bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts)
                self.assertEqual(len(calls), 4)
                self.assertEqual(calls[-1]["trigger_source"], "auto_drift:loot_shift_25pct")

                # Plan younger than 30 min -> cooldown suppresses even when drift exists
                bridge._last_auto_ai_replan_ts = 0.0
                bridge._active_ai_plan_mem["updated_ts"] = now_ts - 600.0
                with (
                    patch.object(bridge, "_start_ai_planner_job", side_effect=_fake_start_ai),
                    patch.object(
                        bridge,
                        "_compute_live_attack_telemetry_locked",
                        return_value={"median_loot": {"spice_eq": 20_000.0}},
                    ),
                ):
                    self.assertIsNone(bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts))
                self.assertEqual(len(calls), 4)

                # F1: warm_start_genes passed to one auto_drift run NEVER persists or leaks into later manual runs
                import spaceinvasion.planner.ai_planner as _ai_mod_warm

                captured_params: list[dict[str, object]] = []

                def _fake_run_ai(state_arg, target_cfg, attack_telemetry=None, params=None, progress_cb=None):
                    captured_params.append(dict(params or {}))
                    return {
                        "schema_version": 1,
                        "summary": {"colonies_founded": 2},
                        "steps": [],
                        "search": {"tuned_genes": {"patience": 0.5}},
                    }

                with (
                    patch.object(_ai_mod_warm, "run_ai_planner", side_effect=_fake_run_ai),
                    patch.object(_ai_mod_warm, "compute_attack_telemetry", return_value={}),
                ):
                    bridge._start_ai_planner_job(
                        params={"warm_start_genes": {"patience": 0.42, "speed_weight": 1.8}},
                        sync=True,
                        refresh=False,
                        trigger_source="auto_drift:plan_age_4h",
                    )
                    self.assertIn("warm_start_genes", captured_params[-1])
                    self.assertNotIn(
                        "warm_start_genes",
                        (bridge._active_ai_plan_mem or {}).get("params", {}),
                    )
                    # Turn switch OFF and trigger manual run with draft params + empty params -> NO leak!
                    bridge.strategy_config = StrategyConfig(
                        planner_mode="ai_planner", max_planets=2, ai_adaptive_execution=False
                    )
                    bridge.handle_command(
                        {"command": "ai_planner_run", "sync": True, "params": {"patience": 0.25}}
                    )
                    self.assertNotIn("warm_start_genes", captured_params[-1])
                    bridge.handle_command({"command": "ai_planner_run", "sync": True})
                    self.assertNotIn("warm_start_genes", captured_params[-1])
            finally:
                bridge.close()


if __name__ == "__main__":
    unittest.main()
