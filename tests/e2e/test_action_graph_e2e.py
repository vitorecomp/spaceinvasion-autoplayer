"""End-to-End API tests for the 4-Tab Action Graph workspace, isolated simulation, and bridge-restart persistence."""

from __future__ import annotations

import json
import unittest

import requests

from backend.core.config import StrategyConfig
from backend.db.config_store import load_strategy_config
from tests.e2e.harness import E2ETestHarness, FakeSpaceInvasionServer


def _make_two_colony_server(*, hostile: bool = False) -> FakeSpaceInvasionServer:
    planets = {
        12476: {
            "planet_id": 12476,
            "name": "Homeworld",
            "coords": "02:380:12",
            "planet_type": "normal",
            "temperature": 28,
            "fields_max": 230,
            "resources": [18000, 12000, 9000, 6000],
            "storage": [100000, 100000, 100000, 100000],
            "energy": {"produced": 320, "used": 140},
            "buildings": {
                "b1": 4,
                "b2": 3,
                "b3": 4,
                "b4": 3,
                "b5": 6,
                "b7": 3,
                "b8": 2,
                "b9": 2,
                "b10": 4,
            },
            "ships": {"s1": 6, "s4": 10, "s10": 4},
            "defense": {},
            "economy_rates": {},
            "building_queue": [],
        },
        12477: {
            "planet_id": 12477,
            "name": "OuterColony",
            "coords": "02:380:15",
            "planet_type": "normal",
            "temperature": -45,
            "fields_max": 220,
            "resources": [14000, 9000, 7000, 5000],
            "storage": [100000, 100000, 100000, 100000],
            "energy": {"produced": 260, "used": 95},
            "buildings": {"b1": 3, "b2": 2, "b3": 3, "b4": 4, "b5": 5, "b7": 2, "b10": 2},
            "ships": {},
            "defense": {},
            "economy_rates": {},
            "building_queue": [],
        },
    }
    hostile_alerts = ["Hostile attack fleet approaching [02:380:12]"] if hostile else []
    return FakeSpaceInvasionServer(
        planets=planets,
        researches={"r1": 1, "r2": 2, "r3": 2, "r4": 2},
        hostile_alerts=hostile_alerts,
        speedup_factor=4.0,
    )


class TestActionGraphEndToEnd(unittest.TestCase):
    def test_ag_e2e_01_action_graph_switch_priority_and_ai_planner_modes(self) -> None:
        srv = _make_two_colony_server(hostile=False)
        cfg = StrategyConfig(planner_mode="priority")
        with E2ETestHarness(server=srv, config=cfg) as h:
            dash = h.get_dashboard(refresh=1)
            self.assertIn("action_graph", dash)
            self.assertEqual(dash["action_graph"]["mode"], "priority")
            self.assertGreater(len(dash["action_graph"]["nodes"]), 0)

            ai_res = h.api.send_command({"command": "action_graph", "planner_mode": "ai_planner"})
            self.assertTrue(ai_res["ok"])
            self.assertEqual(ai_res["action_graph"]["mode"], "ai_planner")
            self.assertGreater(len(ai_res["action_graph"]["nodes"]), 0)

            prio_res = h.api.send_command({"command": "action_graph", "planner_mode": "priority"})
            self.assertTrue(prio_res["ok"])
            self.assertEqual(prio_res["action_graph"]["mode"], "priority")
            self.assertGreater(len(prio_res["action_graph"]["nodes"]), 0)

    def test_ag_e2e_02_isolated_parameter_simulation_zero_active_config_mutation(self) -> None:
        srv = _make_two_colony_server(hostile=False)
        cfg = StrategyConfig(
            planner_mode="priority",
            playstyle="fleeter",
            allow_multi_planet=True,
            allow_raids=False,
        )
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.get_dashboard(refresh=1)
            srv.page_view_log.clear()
            disk_before = h.config_path.read_text(encoding="utf-8")
            cfg_before = h.api.send_command({"command": "config"})["config"]

            sim_res = h.api.send_command(
                {
                    "command": "action_graph",
                    "simulated": True,
                    "config": {
                        "planner_mode": "ai_planner",
                        "playstyle": "miner",
                        "storage_warn_ratio": 0.75,
                        "combat_safety_margin": 1.8,
                        "allow_multi_planet": False,
                        "allow_raids": True,
                    },
                }
            )
            self.assertTrue(sim_res["ok"])
            graph = sim_res["action_graph"]
            self.assertTrue(graph["simulated"])
            self.assertEqual(graph["mode"], "ai_planner")
            self.assertIn("planner_mode", graph.get("changed_keys", []))
            self.assertIn("allow_multi_planet", graph.get("changed_keys", []))

            # Zero browser navigations & zero active StrategyConfig / disk config mutation
            self.assertEqual(len(srv.page_view_log), 0)
            cfg_after = h.api.send_command({"command": "config"})["config"]
            self.assertEqual(cfg_after, cfg_before)
            self.assertEqual(h.get_dashboard(refresh=0)["planner_mode"], "priority")
            self.assertEqual(h.config_path.read_text(encoding="utf-8"), disk_before)

    def test_ag_e2e_03_explicit_apply_persists_across_bridge_restart(self) -> None:
        srv = _make_two_colony_server(hostile=False)
        cfg = StrategyConfig(planner_mode="priority", playstyle="fleeter", allow_raids=False)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.get_dashboard(refresh=1)
            apply_res = h.api.send_command(
                {
                    "command": "update_config",
                    "config": {
                        "planner_mode": "ai_planner",
                        "playstyle": "hybrid",
                        "combat_safety_margin": 1.8,
                        "min_colony_fields": 315,
                        "allow_raids": True,
                        "allow_fleetsave": True,
                    },
                }
            )
            self.assertTrue(apply_res["ok"])
            self.assertEqual(apply_res["config"]["planner_mode"], "ai_planner")
            self.assertEqual(apply_res["config"]["min_colony_fields"], 315)

            # Restart bridge and prove StrategyConfig persisted cleanly
            h.restart_bridge()
            reloaded_disk = load_strategy_config(h.config_path)
            self.assertEqual(reloaded_disk.planner_mode, "ai_planner")
            self.assertEqual(reloaded_disk.playstyle, "hybrid")
            self.assertEqual(reloaded_disk.min_colony_fields, 315)
            self.assertAlmostEqual(reloaded_disk.combat_safety_margin, 1.8, places=3)
            self.assertTrue(reloaded_disk.allow_raids)
            self.assertTrue(reloaded_disk.allow_fleetsave)

            post_restart_cfg = h.api.send_command({"command": "config"})["config"]
            self.assertEqual(post_restart_cfg["planner_mode"], "ai_planner")
            self.assertEqual(post_restart_cfg["min_colony_fields"], 315)

    def test_ag_e2e_04_hostile_p0_multi_planet_invalid_input_and_english_only_verification(self) -> None:
        srv = _make_two_colony_server(hostile=True)
        cfg = StrategyConfig(planner_mode="priority", allow_fleetsave=True, allow_multi_planet=True)
        with E2ETestHarness(server=srv, config=cfg) as h:
            dash = h.get_dashboard(refresh=1)
            ag_p0 = dash["action_graph"]
            kinds = [n["kind"] for n in ag_p0["nodes"]]
            self.assertIn("ALERT_HOSTILE", kinds)
            self.assertIn("ESCAPE_FLIGHT", kinds)
            self.assertTrue(any(e["primary_type"] == "priority_gate" for e in ag_p0["edges"]))

            for bad_patch, bad_key in (
                ({"ga_population_size": 0}, "ga_population_size"),
                ({"combat_safety_margin": 0.2}, "combat_safety_margin"),
            ):
                bad_resp = requests.post(
                    f"{h.base_url}/command",
                    json={"command": "action_graph", "config": bad_patch},
                    timeout=5,
                )
                self.assertEqual(bad_resp.status_code, 400)
                self.assertIn(bad_key, bad_resp.json()["error"])

            self.assertEqual(h.api.send_command({"command": "config"})["config"]["planner_mode"], "priority")

            # Verify JSON contains zero NaN/Infinity/non-English words
            dumped = json.dumps(ag_p0, allow_nan=False)
            for forbidden in ("grafo", "parâmetros", "simular", "aplicar", "configuração"):
                self.assertNotIn(forbidden, dumped.lower())

    def test_ag_e2e_05_4tab_workspace_config_optimizer_ai_planner_gate_and_fleet_sync(self) -> None:
        srv = _make_two_colony_server(hostile=False)
        cfg = StrategyConfig(planner_mode="priority", raid_min_loot_spice_eq=1500)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.get_dashboard(refresh=1)

            # 1) Tab 2: Config Optimizer run
            co_res = h.api.send_command(
                {
                    "command": "config_optimizer_run",
                    "sync": True,
                    "auto_replan_ai": False,
                    "params": {"ga_population_size": 6, "ga_generations": 2},
                }
            )
            self.assertTrue(co_res["ok"])
            co_get = h.api.send_command({"command": "config_optimizer_get"})
            self.assertTrue(co_get["ok"])
            self.assertIsNotNone(co_get.get("active"))
            self.assertGreaterEqual(len(co_get["active"]["result"].get("planets", [])), 2)

            # 2) Tab 3: AI-Planner run + 2/11 Colony Gate locked
            ai_res = h.api.send_command(
                {
                    "command": "ai_planner_run",
                    "sync": True,
                    "params": {"reward_weeks": 2, "ga_population_size": 4, "ga_generations": 1},
                }
            )
            self.assertTrue(ai_res["ok"])
            ai_get = h.api.send_command({"command": "ai_planner_get"})
            self.assertTrue(ai_get["ok"])
            self.assertFalse(ai_get.get("live_ai_unlocked", True))
            self.assertEqual(ai_get.get("colonies_owned"), 2)
            self.assertGreater(len(ai_get.get("graph", {}).get("nodes", [])), 0)

            # 3) Tab 4: Fleet / Attack Planner + Apply persists across bridge restart
            flt_get = h.api.send_command({"command": "fleet_planner_get"})
            self.assertTrue(flt_get["ok"])
            slot_budgets = flt_get.get("slot_budgets")
            if isinstance(slot_budgets, dict):
                slot_rows = slot_budgets.get("planets", slot_budgets.get("rows", list(slot_budgets.values())))
            else:
                slot_rows = slot_budgets or []
            self.assertEqual(len(slot_rows), 2)

            upd = h.api.send_command(
                {
                    "command": "update_config",
                    "config": {
                        "raid_min_loot_spice_eq": 3300,
                        "shipyard_targets": {"starcruiser": 19},
                    },
                }
            )
            self.assertTrue(upd["ok"])

            h.restart_bridge()
            reloaded = load_strategy_config(h.config_path)
            self.assertEqual(int(reloaded.raid_min_loot_spice_eq), 3300)
            self.assertEqual(int(reloaded.shipyard_targets.get("starcruiser", 0)), 19)
