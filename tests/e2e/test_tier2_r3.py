"""Tier 2 E2E Boundary & Corner Tests for R3 (ETA Math, API/Config Sync & Operator UI): 19 tests."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import requests

from tests.e2e.fixtures import (
    make_game_state,
    make_planet,
)
from tests.e2e.harness import E2ETestHarness, FakeSpaceInvasionServer
from backend.core import catalog as game_data
from backend.combat import points as ranking
from backend.core.config import StrategyConfig
from backend.db.config_store import load_strategy_config
from backend.core.model import (
    EnergyBalance,
    Resources,
)
from backend.core.model import PlannedAction
from backend.planner.heuristics import plan_actions


def _import_eta_helpers(test: unittest.TestCase):
    try:
        from backend.planner.eta import (
            attach_action_etas,
            compute_action_eta,
        )
    except ImportError as exc:
        test.fail(f"R3 ETA helpers missing from backend.combat.ranking: {exc}")
    return compute_action_eta, attach_action_etas


def _verify_eta_fields_present(test: unittest.TestCase, obj: dict | PlannedAction) -> None:
    for k in (
        "wait_hours",
        "wait_seconds",
        "wait_eta",
        "duration_seconds",
        "duration_text",
        "total_hours",
        "total_seconds",
        "total_eta",
        "eta_bottleneck",
    ):
        if isinstance(obj, dict):
            test.assertIn(k, obj, f"ETA field {k!r} missing from action dict")
        else:
            test.assertTrue(hasattr(obj, k), f"ETA field {k!r} missing from PlannedAction")


class TestTier2R3(unittest.TestCase):
    # ------------------------------------------------------------------
    # Area R3.A Boundary & Corner Cases (6 tests)
    # ------------------------------------------------------------------

    def test_t2_r3a_01_blast_furnace_paused_0pct_blocks_metal_unblocks_pig(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(
            buildings={"b1": 4, "b2": 5, "b3": 4, "b5": 8, "b7": 2},
            resources=Resources(0, 0, 10_000, 10_000),
            economy_rates={"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100},
        )
        state = make_game_state(planets=(p,), speedup_factor=1.0)
        metal_act = PlannedAction(
            "P2",
            "BUILD",
            p.planet_id,
            p.coords,
            "Development center",
            "needs metal",
            target_id=7,
            target_level=3,
            cost=Resources(0, 2400, 1200, 800),
            missing=Resources(0, 500, 0, 0),
        )
        e_met = compute_action_eta(state, metal_act)
        self.assertIsNone(e_met["wait_hours"])
        self.assertIsNone(e_met["wait_seconds"])
        self.assertEqual(e_met["wait_eta"], "—")
        self.assertIsNone(e_met["total_hours"])
        self.assertIsNone(e_met["total_seconds"])
        self.assertEqual(e_met["total_eta"], "—")
        self.assertEqual(e_met["eta_bottleneck"], "metal")

        pig_act = PlannedAction(
            "P2",
            "BUILD",
            p.planet_id,
            p.coords,
            "Pig iron mine",
            "pig only",
            target_id=1,
            target_level=5,
            cost=Resources(400, 0, 100, 0),
            missing=Resources(200, 0, 0, 0),
        )
        e_pig = compute_action_eta(state, pig_act)
        self.assertIsNotNone(e_pig["wait_hours"])
        self.assertGreater(e_pig["wait_hours"], 0.0)
        self.assertEqual(e_pig["eta_bottleneck"], "pig_iron")

    def test_t2_r3a_02_blast_furnace_2_1_over_drain_blocks_pig_no_zero_div(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        # b1=2 (63/h) vs b2=6 (290/h used -> net -227/h Pig)
        p = make_planet(
            buildings={"b1": 2, "b2": 6, "b5": 9},
            resources=Resources(0, 5000, 5000, 5000),
            economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
        )
        state = make_game_state(planets=(p,))
        act = PlannedAction(
            "P2",
            "BUILD",
            p.planet_id,
            p.coords,
            "Pig iron mine",
            "over-drained",
            target_id=1,
            target_level=3,
            missing=Resources(100, 0, 0, 0),
        )
        eta = compute_action_eta(state, act)
        self.assertIsNone(eta["wait_hours"])
        self.assertEqual(eta["wait_eta"], "—")
        self.assertEqual(eta["total_eta"], "—")
        self.assertEqual(eta["eta_bottleneck"], "pig_iron")

    def test_t2_r3a_03_multi_planet_isolation_temp_and_r1_mining_tech(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p1_cold = make_planet(
            planet_id=101,
            coords="02:380:15",
            temperature=-120,
            buildings={"b4": 5, "b5": 10, "b7": 6, "b17": 1},
            resources=Resources(10_000, 10_000, 10_000, 0),
        )
        p2_hot = make_planet(
            planet_id=202,
            coords="02:380:02",
            temperature=120,
            buildings={"b4": 5, "b5": 10, "b7": 0, "b17": 0},
            resources=Resources(10_000, 10_000, 10_000, 0),
        )
        state = make_game_state(
            planets=(p1_cold, p2_hot), active_planet_id=202, researches={"r1": 5}, speedup_factor=1.0
        )
        act_p1 = PlannedAction(
            "P2",
            "BUILD",
            101,
            p1_cold.coords,
            "Spice mine",
            "cold spice",
            target_id=4,
            target_level=6,
            missing=Resources(0, 0, 0, 1000),
        )
        act_p2 = PlannedAction(
            "P2",
            "BUILD",
            202,
            p2_hot.coords,
            "Spice mine",
            "hot spice",
            target_id=4,
            target_level=6,
            missing=Resources(0, 0, 0, 1000),
        )
        e1 = compute_action_eta(state, act_p1)
        e2 = compute_action_eta(state, act_p2)
        expected_spice_p1 = 75.0 * (1.0 + 0.02 * 5)  # 82.5/h
        self.assertAlmostEqual(e1["wait_hours"], 1000.0 / expected_spice_p1, delta=0.1)
        self.assertLess(e1["wait_hours"], e2["wait_hours"])
        base_s = float(game_data.building_level_stats(4, 6)["base_time_s"])
        self.assertAlmostEqual(e1["duration_seconds"], base_s / ((6 + 1) * 2), delta=0.2)
        self.assertAlmostEqual(e2["duration_seconds"], base_s / 1.0, delta=0.2)

    def test_t2_r3a_04_wait_resources_building_vs_ship_advisory_durations(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(buildings={"b3": 4, "b5": 8, "b7": 2, "b17": 0})
        state = make_game_state(planets=(p,), speedup_factor=1.0)
        wait_b = PlannedAction(
            "P2",
            "WAIT_RESOURCES",
            p.planet_id,
            p.coords,
            "Kryptonite mine",
            "queued/waiting",
            target_id=3,
            target_level=5,
            cost=Resources(pig_iron=600, kryptonite=300),
            missing=Resources(0, 0, 100, 0),
        )
        ship_adv = PlannedAction(
            "P3",
            "SHIP",
            p.planet_id,
            p.coords,
            "Small transport",
            "advisory",
            target_id=None,
            cost=Resources(0, 3000, 3000, 0),
            missing=Resources(0, 0, 500, 0),
        )
        ew = compute_action_eta(state, wait_b)
        es = compute_action_eta(state, ship_adv)
        self.assertGreater(ew["wait_hours"], 0.0)
        self.assertGreater(ew["duration_seconds"], 0.0)
        self.assertAlmostEqual(ew["total_seconds"], ew["wait_seconds"] + ew["duration_seconds"], delta=0.2)

        self.assertGreater(es["wait_hours"], 0.0)
        self.assertEqual(es["duration_seconds"], 0.0)
        self.assertEqual(es["duration_text"], "Instant")
        self.assertAlmostEqual(es["total_seconds"], es["wait_seconds"], delta=0.2)

    def test_t2_r3a_05_zero_weapons_factory_and_total_energy_blackout(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(
            buildings={"b1": 4, "b3": 4, "b9": 0},
            energy=EnergyBalance(produced=0, used=100),
        )
        state = make_game_state(planets=(p,), speedup_factor=1.0)
        act = PlannedAction(
            "P2",
            "BUILD_SHIP",
            p.planet_id,
            p.coords,
            "Small transport",
            "blackout",
            target_id=1,
            target_level=1,
            cost=Resources(0, 3000, 3000, 0),
            missing=Resources(0, 0, 500, 0),
        )
        eta = compute_action_eta(state, act)
        base_s = float(game_data.SHIPS["small-transport"]["base_build_time_s"])
        self.assertAlmostEqual(eta["duration_seconds"], base_s / 1.0, delta=0.2)
        self.assertIsNone(eta["wait_hours"])
        self.assertEqual(eta["wait_eta"], "—")
        self.assertEqual(eta["total_eta"], "—")

    def test_t2_r3a_06_extrapolated_research_l42_and_unknown_target_id(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(buildings={"b8": 4}, resources=Resources(0, 0, 0, 0))
        state = make_game_state(planets=(p,), researches={"r1": 41}, speedup_factor=1.0)
        res_42 = PlannedAction(
            "P2",
            "RESEARCH",
            p.planet_id,
            p.coords,
            "Mining technology",
            "l42",
            target_id=1,
            target_level=42,
            missing=Resources(),
        )
        e42 = compute_action_eta(state, res_42)
        l40_s = float(game_data.RESEARCH_BY_ID["r1"]["levels"][40]["base_time_s"])
        self.assertAlmostEqual(e42["duration_seconds"], (l40_s * 4.0) / 5.0, delta=2.0)

        bad_b = PlannedAction("P2", "BUILD", p.planet_id, p.coords, "Unknown", "bad", target_id=999, target_level=1)
        e_bad = compute_action_eta(state, bad_b)
        self.assertEqual(e_bad["duration_seconds"], 0.0)

    # ------------------------------------------------------------------
    # Area R3.B Boundary & Corner Cases (6 tests)
    # ------------------------------------------------------------------

    def test_t2_r3b_01_empty_planets_returns_empty_plan_cleanly(self) -> None:
        _import_eta_helpers(self)
        empty = make_game_state(planets=(), active_planet_id=None)
        self.assertEqual(plan_actions(empty, config=StrategyConfig()), [])
        srv = FakeSpaceInvasionServer(planets={})
        with E2ETestHarness(server=srv) as h:
            self.assertEqual(h.api.send_command({"command": "plan"})["actions"], [])
            dash = requests.get(f"{h.base_url}/dashboard?refresh=0", timeout=5.0).json()
            self.assertEqual(dash["plan"], [])

    def test_t2_r3b_02_corrupted_planner_mode_json_and_http_update(self) -> None:
        _import_eta_helpers(self)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.json"
            p.write_text('{"planner_mode": "invalid_xyz", "playstyle": "miner"}\n', encoding="utf-8")
            loaded = load_strategy_config(p)
            self.assertTrue(hasattr(loaded, "planner_mode"), "StrategyConfig missing planner_mode")
            self.assertEqual(loaded.planner_mode, "priority")

        with E2ETestHarness(raw_config_text='{"planner_mode": "invalid_xyz", "playstyle": "fleeter"}\n') as h:
            cfg = h.api.send_command({"command": "config"})["config"]
            self.assertEqual(cfg["planner_mode"], "priority")
            resp = requests.post(
                f"{h.base_url}/command",
                json={"command": "update_config", "config": {"planner_mode": "not_a_mode"}},
                timeout=5.0,
            )
            self.assertIn(resp.status_code, (200, 400))
            stored = load_strategy_config(h.config_path)
            self.assertEqual(stored.planner_mode, "priority")

    def test_t2_r3b_03_update_config_syncs_live_and_dry_executors(self) -> None:
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            atk_blocked = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertFalse(atk_blocked["executed"])

            h.api.send_command(
                {
                    "command": "update_config",
                    "config": {"max_actions_per_hour": 2, "allow_raids": True, "allow_fleetsave": True},
                }
            )
            self.assertEqual(h.api.health()["remaining_hourly_actions"], 2)
            dash = h.get_dashboard(refresh=0)
            self.assertEqual(dash["config"]["max_actions_per_hour"], 2)
            self.assertTrue(dash["config"]["allow_raids"])
            self.assertTrue(dash["config"]["allow_fleetsave"])

            # Dry executor synced allow_raids=True -> produces [DRY-RUN] preview instead of disabled guard
            atk_dry = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": True})
            self.assertTrue(atk_dry["dry_run"])
            self.assertFalse(atk_dry["executed"])
            self.assertIn("[DRY-RUN]", atk_dry["message"])

            # Live executor synced allow_raids=True and max_actions_per_hour=2
            atk_live_1 = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertTrue(atk_live_1["executed"], atk_live_1)
            self.assertEqual(h.api.health()["remaining_hourly_actions"], 1)

            atk_live_2 = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertTrue(atk_live_2["executed"], atk_live_2)
            self.assertEqual(h.api.health()["remaining_hourly_actions"], 0)

            # 3rd action blocked by synced max_actions_per_hour=2 until budget raised
            atk_capped = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertFalse(atk_capped["executed"])
            h.api.send_command({"command": "update_config", "config": {"max_actions_per_hour": 5}})
            self.assertEqual(h.api.health()["remaining_hourly_actions"], 3)

    def test_t2_r3b_04_cached_economy_rates_preserved_in_dashboard_and_plan(self) -> None:
        _import_eta_helpers(self)
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            res = h.api.send_command(
                {"command": "throttle_building", "building_id": 2, "percentage": 0, "dry_run": False}
            )
            self.assertTrue(res["executed"])
            plan = h.api.send_command({"command": "plan"})["actions"]
            dash = requests.get(f"{h.base_url}/dashboard?refresh=0", timeout=5.0).json()

            # Metal-missing actions should report blocked wait_eta="—" with eta_bottleneck="metal"
            metal_missing = [a for a in dash["plan"] if a["missing"]["metal"] > 0]
            for a in metal_missing:
                self.assertEqual(a["wait_eta"], "—")
                self.assertEqual(a["eta_bottleneck"], "metal")
            self.assertEqual(
                [a["wait_eta"] for a in plan[:3]],
                [a["wait_eta"] for a in dash["plan"][:3]],
            )

    def test_t2_r3b_05_large_deficits_format_into_days(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(buildings={"b3": 10, "b5": 15, "b7": 3}, energy=EnergyBalance(1000, 500))
        state = make_game_state(planets=(p,), speedup_factor=1.0)
        kry_rate = float(ranking.estimate_planet_hourly_economy(state, p)["kryptonite_per_hr"])
        miss_kry = round(200.0 * kry_rate)
        act = PlannedAction(
            "P2",
            "BUILD",
            p.planet_id,
            p.coords,
            "Kryptonite mine",
            "long wait",
            target_id=3,
            target_level=11,
            missing=Resources(0, 0, miss_kry, 0),
        )
        eta = compute_action_eta(state, act)
        self.assertAlmostEqual(eta["wait_hours"], 200.0, delta=0.5)
        self.assertIn("d", eta["wait_eta"])
        self.assertIn("d", eta["total_eta"])

    def test_t2_r3b_06_sid_redaction_preserved_with_etas_in_logs(self) -> None:
        _import_eta_helpers(self)
        srv = FakeSpaceInvasionServer(sid="deadbeef1234cafe")
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            tick = h.api.send_command({"command": "tick_once", "dry_run": True})
            self.assertTrue(tick["ok"])
            _verify_eta_fields_present(self, tick["action"])
            dash = requests.get(f"{h.base_url}/dashboard?refresh=0", timeout=5.0).json()
            self.assertNotIn("deadbeef1234cafe", json.dumps(dash["plan"]))
            api_jsonl = (h.log_dir / "game_api.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("deadbeef1234cafe", api_jsonl)
            last_entry = json.loads(api_jsonl.strip().splitlines()[-1])
            _verify_eta_fields_present(self, last_entry["actual_action"])
