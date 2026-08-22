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
            self.assertEqual(len(dash["ranking"]["horizons"]), 5)

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


if __name__ == "__main__":
    unittest.main()
