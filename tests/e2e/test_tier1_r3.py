"""Tier 1 E2E Feature Coverage Tests for R3 (Queue ETA Engine, API Payloads & Operator UI): 19 tests."""

from __future__ import annotations

import unittest

import requests

from tests.e2e.fixtures import (
    make_building_offer,
    make_game_state,
    make_planet,
    make_research_offer,
)
from tests.e2e.harness import (
    E2ETestHarness,
    FakeSpaceInvasionServer,
)
from backend.core import catalog as game_data
from backend.combat import points as ranking
from backend.core.config import StrategyConfig
from backend.core.model import (
    EnergyBalance,
    Resources,
)
from backend.core.model import PlannedAction
from backend.planner.heuristics import (
    plan_actions,
)


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
    keys = (
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
    for k in keys:
        if isinstance(obj, dict):
            test.assertIn(k, obj, f"ETA field {k!r} missing from action dict")
        else:
            test.assertTrue(hasattr(obj, k), f"ETA field {k!r} missing from PlannedAction")


class TestTier1R3(unittest.TestCase):
    # ------------------------------------------------------------------
    # Area R3.A: Action & Queue ETA Math
    # ------------------------------------------------------------------

    def test_t1_r3a_01_affordable_build_ready_wait_and_exact_duration(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(
            buildings={"b1": 3, "b7": 3, "b17": 1},
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            auto_offers=True,
        )
        state = make_game_state(planets=(p,), speedup_factor=2.0)
        act = PlannedAction(
            priority="P2",
            kind="BUILD",
            planet_id=p.planet_id,
            planet_coords=p.coords,
            target_id=1,
            target_name="Pig iron mine",
            target_level=4,
            cost=Resources(**game_data.building_cost(1, 4)),
            missing=Resources(),
            reason="Upgrade Pig iron mine",
            executable_now=True,
        )
        eta = compute_action_eta(state, act)
        base_s = float(game_data.building_level_stats(1, 4)["base_time_s"])
        expected_dur = base_s / ((3 + 1) * (2**1) * 2.0)

        self.assertEqual(eta["wait_hours"], 0.0)
        self.assertEqual(eta["wait_seconds"], 0.0)
        self.assertEqual(eta["wait_eta"], "Ready")
        self.assertIsNone(eta["eta_bottleneck"])
        self.assertAlmostEqual(eta["duration_seconds"], expected_dur, delta=0.2)
        self.assertAlmostEqual(eta["total_seconds"], expected_dur, delta=0.2)
        self.assertAlmostEqual(eta["total_hours"], expected_dur / 3600.0, places=4)
        self.assertTrue(eta["total_eta"] and eta["total_eta"] not in ("—", "Ready"))

    def test_t1_r3a_02_unaffordable_build_wait_bottleneck_and_total(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(
            buildings={"b1": 4, "b2": 0, "b3": 3, "b5": 7, "b7": 2},
            resources=Resources(0, 0, 0, 0),
        )
        state = make_game_state(planets=(p,), speedup_factor=1.0)
        econ = ranking.estimate_planet_hourly_economy(state, p)
        pig_rate = float(econ["pig_iron_per_hr"])
        kry_rate = float(econ["kryptonite_per_hr"])
        miss_pig = round(2.0 * pig_rate)
        miss_kry = round(1.0 * kry_rate)

        act = PlannedAction(
            priority="P2",
            kind="BUILD",
            planet_id=p.planet_id,
            planet_coords=p.coords,
            target_id=1,
            target_name="Pig iron mine",
            target_level=5,
            cost=Resources(pig_iron=miss_pig, kryptonite=miss_kry),
            missing=Resources(pig_iron=miss_pig, kryptonite=miss_kry),
            reason="Wait resources for Pig iron mine",
            executable_now=False,
        )
        eta = compute_action_eta(state, act)
        expected_wait_h = miss_pig / pig_rate
        base_s = float(game_data.building_level_stats(1, 5)["base_time_s"])
        expected_dur_s = base_s / ((2 + 1) * 1.0)

        self.assertAlmostEqual(eta["wait_hours"], expected_wait_h, places=2)
        self.assertAlmostEqual(eta["wait_seconds"], expected_wait_h * 3600.0, delta=5.0)
        self.assertEqual(eta["eta_bottleneck"], "pig_iron")
        self.assertAlmostEqual(eta["duration_seconds"], expected_dur_s, delta=0.2)
        self.assertAlmostEqual(eta["total_seconds"], eta["wait_seconds"] + eta["duration_seconds"], delta=0.5)
        self.assertAlmostEqual(eta["total_hours"], eta["wait_hours"] + eta["duration_seconds"] / 3600.0, places=4)
        self.assertTrue(eta["wait_eta"] and eta["wait_eta"] != "Ready")
        self.assertTrue(eta["total_eta"] and eta["total_eta"] != "—")

    def test_t1_r3a_03_research_duration_ignores_microsystem_b17(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(
            buildings={"b8": 3, "b17": 4},
            resources=Resources(50_000, 50_000, 50_000, 50_000),
        )
        state = make_game_state(planets=(p,), researches={"r2": 2}, speedup_factor=2.0)
        act = PlannedAction(
            priority="P2",
            kind="RESEARCH",
            planet_id=p.planet_id,
            planet_coords=p.coords,
            target_id=2,
            target_name="Energy technology",
            target_level=3,
            cost=Resources(0, 1600, 3200, 1600),
            missing=Resources(),
            reason="Research Energy technology",
            executable_now=True,
        )
        eta = compute_action_eta(state, act)
        base_s = float(game_data.RESEARCH_BY_ID["r2"]["levels"][3]["base_time_s"])
        expected_no_micro = base_s / ((3 + 1) * 2.0)
        wrong_with_micro = expected_no_micro / (2**4)
        self.assertAlmostEqual(eta["duration_seconds"], expected_no_micro, delta=0.5)
        self.assertGreater(eta["duration_seconds"], wrong_with_micro * 4.0)

    def test_t1_r3a_04_build_ship_duration_uses_b9_not_b9_plus_1(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(
            buildings={"b9": 5, "b17": 1},
            resources=Resources(50_000, 50_000, 50_000, 50_000),
        )
        state = make_game_state(planets=(p,), speedup_factor=2.0)
        act = PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=p.planet_id,
            planet_coords=p.coords,
            target_id=1,
            target_name="Small transport",
            target_level=4,
            cost=Resources(0, 12000, 12000, 0),
            missing=Resources(),
            reason="Build Small transport ships",
            executable_now=True,
        )
        eta = compute_action_eta(state, act)
        base_s = float(game_data.SHIPS["small-transport"]["base_build_time_s"])
        expected = (4 * base_s) / (5 * (2**1) * 2.0)
        self.assertAlmostEqual(eta["duration_seconds"], expected, delta=0.2)

    def test_t1_r3a_05_instant_actions_zero_duration_and_escape_cargo_zero_wait(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(buildings={"b1": 4, "b2": 3, "b5": 7})
        state = make_game_state(planets=(p,))
        samples = [
            PlannedAction(
                "P2", "CLAIM_TUTORIAL", p.planet_id, p.coords, "Tutorial 1/11 Reward", "claim", executable_now=True
            ),
            PlannedAction(
                "P1",
                "THROTTLE_BUILDING",
                p.planet_id,
                p.coords,
                "Blast furnace 0%",
                "pause",
                target_id=2,
                target_level=0,
                executable_now=True,
            ),
            PlannedAction("P2", "SCAN_GALAXY", p.planet_id, p.coords, "Galaxy [02:380]", "scan", executable_now=True),
            PlannedAction(
                "P0",
                "ESCAPE_FLIGHT",
                p.planet_id,
                p.coords,
                "Save -> 02:380:13",
                "save",
                cost=Resources(0, 5000, 3000, 2000),
                missing=Resources(),
                executable_now=True,
            ),
            PlannedAction("P2", "ATTACK", p.planet_id, p.coords, "02:380:04", "raid", executable_now=True),
            PlannedAction("P0", "ALERT_HOSTILE", p.planet_id, p.coords, "Incoming", "alert", executable_now=False),
        ]
        for act in samples:
            eta = compute_action_eta(state, act)
            self.assertEqual(eta["wait_hours"], 0.0, f"Non-zero wait for {act.kind}")
            self.assertEqual(eta["wait_seconds"], 0.0)
            self.assertEqual(eta["wait_eta"], "Ready")
            self.assertEqual(eta["duration_seconds"], 0.0)
            self.assertEqual(eta["duration_text"], "Instant")
            self.assertEqual(eta["total_hours"], 0.0)
            self.assertEqual(eta["total_seconds"], 0.0)
            self.assertIn(eta["total_eta"], ("Ready", "Instant"))
            self.assertIsNone(eta["eta_bottleneck"])

    def test_t1_r3a_06_energy_deficit_throttles_wait_not_duration(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p_ok = make_planet(planet_id=1, buildings={"b3": 5, "b7": 2}, energy=EnergyBalance(1000, 1000))
        p_half = make_planet(planet_id=2, buildings={"b3": 5, "b7": 2}, energy=EnergyBalance(500, 1000))
        state = make_game_state(planets=(p_ok, p_half), active_planet_id=1)

        a_ok = PlannedAction(
            "P2",
            "BUILD",
            1,
            p_ok.coords,
            "Kryptonite mine",
            "wait",
            target_id=3,
            target_level=6,
            missing=Resources(0, 0, 1000, 0),
        )
        a_half = PlannedAction(
            "P2",
            "BUILD",
            2,
            p_half.coords,
            "Kryptonite mine",
            "wait",
            target_id=3,
            target_level=6,
            missing=Resources(0, 0, 1000, 0),
        )
        e_ok = compute_action_eta(state, a_ok)
        e_half = compute_action_eta(state, a_half)

        self.assertGreater(e_ok["wait_hours"], 0.0)
        self.assertAlmostEqual(e_half["wait_hours"], e_ok["wait_hours"] * 2.0, delta=0.05)
        self.assertAlmostEqual(e_half["duration_seconds"], e_ok["duration_seconds"], places=4)

    # ------------------------------------------------------------------
    # Area R3.B: Planner & HTTP API ETA Payloads
    # ------------------------------------------------------------------

    def test_t1_r3b_01_priority_plan_actions_attaches_all_9_eta_fields(self) -> None:
        _import_eta_helpers(self)
        p = make_planet(
            buildings={"b1": 5, "b2": 4, "b3": 4, "b4": 2, "b5": 8, "b7": 2, "b8": 2},
            resources=Resources(800, 200, 400, 50),
            building_offers=(
                make_building_offer(1, 5, can_build=False, href=None),
                make_building_offer(5, 8, can_build=False, href=None),
            ),
        )
        state = make_game_state(
            planets=(p,),
            researches={"r2": 1},
            research_offers=(make_research_offer("r2", cur_lvl=1, can_research=False, href=None),),
        )
        actions = plan_actions(state, config=StrategyConfig(allow_galaxy_scan=True))
        self.assertGreater(len(actions), 0)
        for act in actions:
            _verify_eta_fields_present(self, act)
            if act.wait_seconds is not None and act.total_seconds is not None:
                self.assertAlmostEqual(act.total_seconds, act.wait_seconds + act.duration_seconds, delta=0.5)

    def test_t1_r3b_02_ai_planner_plan_actions_attaches_matching_etas(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(
            buildings={"b1": 5, "b2": 4, "b3": 4, "b4": 2, "b5": 8, "b7": 2, "b8": 2},
            resources=Resources(10_000, 10_000, 8_000, 3_000),
            auto_offers=True,
        )
        state = make_game_state(planets=(p,), researches={"r2": 1})
        cfg = StrategyConfig(planner_mode="ai_planner")
        actions = plan_actions(state, config=cfg)
        self.assertGreater(len(actions), 0)
        for act in actions:
            _verify_eta_fields_present(self, act)
            ref = compute_action_eta(state, act)
            self.assertEqual(act.wait_eta, ref["wait_eta"])
            self.assertEqual(act.duration_text, ref["duration_text"])
            self.assertEqual(act.total_eta, ref["total_eta"])

    def test_t1_r3b_03_http_plan_endpoint_and_planner_mode_override(self) -> None:
        _import_eta_helpers(self)
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            res_pri = h.api.send_command({"command": "plan"})
            self.assertTrue(res_pri["ok"])
            self.assertGreater(len(res_pri["actions"]), 0)
            for item in res_pri["actions"]:
                _verify_eta_fields_present(self, item)

            res_ga = h.api.send_command({"command": "plan", "planner_mode": "ga_nn"})
            self.assertTrue(res_ga["ok"])
            self.assertEqual(res_ga.get("planner_mode"), "priority")
            for item in res_ga["actions"]:
                _verify_eta_fields_present(self, item)

            cfg_after = h.api.send_command({"command": "config"})["config"]
            self.assertEqual(cfg_after["planner_mode"], "priority")

    def test_t1_r3b_04_dashboard_plan_etas_consistent_with_buildings_eta(self) -> None:
        _import_eta_helpers(self)
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [500, 100, 200, 0],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 250, "used": 140},
                "buildings": {"b1": 5, "b2": 3, "b3": 4, "b4": 2, "b5": 7},
                "ships": {},
                "defense": {},
                "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                "building_queue": [],
            }
        }
        srv = FakeSpaceInvasionServer(planets=planets)
        with E2ETestHarness(server=srv) as h:
            dash = requests.get(f"{h.base_url}/dashboard?refresh=1", timeout=5.0).json()
            self.assertGreater(len(dash["plan"]), 0)
            by_bid = {b["building_id"]: b for b in dash["buildings_eta"]["buildings"]}
            matched = 0
            for act in dash["plan"]:
                _verify_eta_fields_present(self, act)
                if (
                    act["kind"] in ("BUILD", "WAIT_RESOURCES")
                    and act.get("target_id") in by_bid
                    and act["planet_id"] == 12476
                ):
                    row = by_bid[act["target_id"]]
                    if act["target_level"] == row["next_level"]:
                        self.assertEqual(act["duration_text"], row["build_time_text"])
                        matched += 1
            self.assertGreaterEqual(matched, 1)

    def test_t1_r3b_05_tick_once_and_action_commands_include_etas(self) -> None:
        _import_eta_helpers(self)
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            tick_res = h.api.send_command({"command": "tick_once", "dry_run": True})
            self.assertTrue(tick_res["ok"])
            self.assertIsNotNone(tick_res["action"])
            _verify_eta_fields_present(self, tick_res["action"])
            if tick_res.get("next_action"):
                _verify_eta_fields_present(self, tick_res["next_action"])

            ship_res = h.api.send_command({"command": "build_ship", "ship_id": 1, "quantity": 2, "dry_run": True})
            self.assertTrue(ship_res["ok"])
            _verify_eta_fields_present(self, ship_res["action"])

    def test_t1_r3b_06_legacy_planned_action_positional_backward_compatibility(self) -> None:
        act = PlannedAction("P2", "BUILD", 1, "01:001:01", "Pig iron mine", "legacy positional")
        self.assertEqual(act.priority, "P2")
        self.assertEqual(act.kind, "BUILD")
        _verify_eta_fields_present(self, act)
        self.assertEqual(act.wait_hours, 0.0)
        self.assertEqual(act.wait_eta, "Ready")
        self.assertEqual(act.duration_seconds, 0.0)
        self.assertEqual(act.duration_text, "Instant")
        self.assertEqual(act.total_eta, "Ready")
        self.assertIsNone(act.eta_bottleneck)
