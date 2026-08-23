"""Tests for the local Game HTTP API, Commander client, and hot-reloader."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import commander
from spaceinvasion.api_server import GameApiBridge, create_api_server
from spaceinvasion.commander_client import GameApiClient, GameApiError
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
from spaceinvasion.strategy import FUSION_PLANT, PIG_IRON_MINE


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
        import hashlib
        from spaceinvasion.storage import GameDatabase

        repo_hist = Path("data") / "attack_history.json"
        before_sha = hashlib.sha256(repo_hist.read_bytes()).hexdigest()

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

        after_sha = hashlib.sha256(repo_hist.read_bytes()).hexdigest()
        self.assertEqual(after_sha, before_sha)

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


if __name__ == "__main__":
    unittest.main()
