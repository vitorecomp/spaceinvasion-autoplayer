"""Tier 1 E2E Feature Coverage Tests for R3 (Queue ETA Engine, API Payloads & Operator UI): 19 tests."""

from __future__ import annotations

import html
import json
import re
import unittest

import requests

from e2e_tests.fixtures import (
    make_building_offer,
    make_ga_nn_config,
    make_game_state,
    make_planet,
    make_research_offer,
    make_ship_offer,
)
from e2e_tests.harness import (
    E2ETestHarness,
    FakeClock,
    FakeSpaceInvasionServer,
    inspect_ui_html,
    run_ui_in_node,
)
from spaceinvasion import game_data, ranking
from spaceinvasion.config import StrategyConfig
from spaceinvasion.state import EnergyBalance, FleetAlert, QueueJob, Resources, TutorialStatus, TutorialTask
from spaceinvasion.strategy import FUSION_PLANT, PIG_IRON_MINE, PlannedAction, plan_actions


def _import_eta_helpers(test: unittest.TestCase):
    try:
        from spaceinvasion.ranking import attach_action_etas, compute_action_eta
    except ImportError as exc:
        test.fail(f"R3 ETA helpers missing from spaceinvasion.ranking: {exc}")
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
        expected_dur = base_s / ((3 + 1) * (2 ** 1) * 2.0)

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
        wrong_with_micro = expected_no_micro / (2 ** 4)
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
        expected = (4 * base_s) / (5 * (2 ** 1) * 2.0)
        self.assertAlmostEqual(eta["duration_seconds"], expected, delta=0.2)

    def test_t1_r3a_05_instant_actions_zero_duration_and_escape_cargo_zero_wait(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(buildings={"b1": 4, "b2": 3, "b5": 7})
        state = make_game_state(planets=(p,))
        samples = [
            PlannedAction("P2", "CLAIM_TUTORIAL", p.planet_id, p.coords, "Tutorial 1/11 Reward", "claim", executable_now=True),
            PlannedAction("P1", "THROTTLE_BUILDING", p.planet_id, p.coords, "Blast furnace 0%", "pause", target_id=2, target_level=0, executable_now=True),
            PlannedAction("P2", "SCAN_GALAXY", p.planet_id, p.coords, "Galaxy [02:380]", "scan", executable_now=True),
            PlannedAction("P0", "ESCAPE_FLIGHT", p.planet_id, p.coords, "Save -> 02:380:13", "save", cost=Resources(0, 5000, 3000, 2000), missing=Resources(), executable_now=True),
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

        a_ok = PlannedAction("P2", "BUILD", 1, p_ok.coords, "Kryptonite mine", "wait", target_id=3, target_level=6, missing=Resources(0, 0, 1000, 0))
        a_half = PlannedAction("P2", "BUILD", 2, p_half.coords, "Kryptonite mine", "wait", target_id=3, target_level=6, missing=Resources(0, 0, 1000, 0))
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

    def test_t1_r3b_02_ga_nn_plan_actions_attaches_matching_etas(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(
            buildings={"b1": 5, "b2": 4, "b3": 4, "b4": 2, "b5": 8, "b7": 2, "b8": 2},
            resources=Resources(10_000, 10_000, 8_000, 3_000),
            auto_offers=True,
        )
        state = make_game_state(planets=(p,), researches={"r2": 1})
        cfg = make_ga_nn_config(self, ga_seed=42)
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
            self.assertEqual(res_ga.get("planner_mode"), "ga_nn")
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
                if act["kind"] in ("BUILD", "WAIT_RESOURCES") and act.get("target_id") in by_bid and act["planet_id"] == 12476:
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

    # ------------------------------------------------------------------
    # Area R3.C: Operator Control UI Queue ETA, Config & Rate-Limit UI
    # ------------------------------------------------------------------

    def test_t1_r3c_01_ui_html_table_headers_colspan_and_planner_mode_select(self) -> None:
        with E2ETestHarness() as h:
            html_text = requests.get(f"{h.base_url}/", timeout=5.0).text
            info = inspect_ui_html(html_text)
            self.assertTrue(info["dash_plan"]["found"])
            self.assertTrue(info["ops_plan"]["found"])
            self.assertEqual(
                info["dash_plan"]["placeholder_colspans"],
                [info["dash_plan"]["th_count"]],
                f"Dashboard table colspan mismatch: {info['dash_plan']}",
            )
            self.assertEqual(
                info["ops_plan"]["placeholder_colspans"],
                [info["ops_plan"]["th_count"]],
                f"Operations table colspan mismatch: {info['ops_plan']}",
            )
            dash_hdr = " ".join(info["dash_plan"]["headers"]).lower()
            ops_hdr = " ".join(info["ops_plan"]["headers"]).lower()
            self.assertIn("wait", dash_hdr, f"Dashboard headers missing Wait ETA: {info['dash_plan']['headers']}")
            self.assertTrue("total" in dash_hdr or "eta" in dash_hdr, f"Dashboard headers missing Total ETA: {info['dash_plan']['headers']}")
            self.assertIn("wait", ops_hdr, f"Operations headers missing Wait ETA: {info['ops_plan']['headers']}")
            self.assertTrue("total" in ops_hdr or "eta" in ops_hdr, f"Operations headers missing Total ETA: {info['ops_plan']['headers']}")

            self.assertTrue(info["has_cfg_planner_mode"], "Missing #cfg-planner_mode in #view-config")
            self.assertIn("priority", info["cfg_planner_mode_options"])
            self.assertIn("ga_nn", info["cfg_planner_mode_options"])

    def test_t1_r3c_02_ui_dashboard_renders_wait_and_total_etas(self) -> None:
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [10000, 100, 10000, 5000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 250, "used": 140},
                "buildings": {"b1": 5, "b2": 4, "b3": 4, "b4": 2, "b5": 7, "b7": 2},
                "ships": {},
                "defense": {},
                "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                "building_queue": [],
            }
        }
        srv = FakeSpaceInvasionServer(planets=planets)
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            dash = requests.get(f"{h.base_url}/dashboard?refresh=0", timeout=5.0).json()
            self.assertGreater(len(dash["plan"]), 0)
            _verify_eta_fields_present(self, dash["plan"][0])

            res = run_ui_in_node(
                h.base_url,
                """
                const ok = await __waitUntil(() => __serializeTable("dash-plan-tbody").length > 0 && __serializeTable("dash-plan-tbody")[0].length > 1 && !document.querySelector("#dash-plan-tbody td.empty-cell"));
                if (!ok) throw new Error("dash-plan-tbody did not populate");
                return {
                  dashRows: __serializeTable("dash-plan-tbody"),
                  thCount: document.querySelectorAll('#view-dashboard thead th').length
                };
                """,
            )
            self.assertEqual(res["errors"], [])
            th_count = res["htmlInspection"]["dash_plan"]["th_count"]
            self.assertGreaterEqual(th_count, 6, f"Expected Wait ETA & Total ETA columns in dashboard table; headers={res['htmlInspection']['dash_plan']['headers']}")
            for idx, row in enumerate(res["scenario"]["dashRows"]):
                self.assertEqual(len(row), th_count)
                row_txt = " ".join(c["text"] for c in row)
                plan_item = dash["plan"][idx]
                self.assertIn(plan_item["wait_eta"], row_txt)
                self.assertIn(plan_item["total_eta"], row_txt)

    def test_t1_r3c_03_ui_operations_renders_all_etas_and_ready_filter(self) -> None:
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [1000, 100, 500, 0],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 250, "used": 140},
                "buildings": {"b1": 5, "b2": 4, "b3": 4, "b4": 2, "b5": 7, "b7": 2, "b8": 2, "b9": 2},
                "ships": {},
                "defense": {},
                "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                "building_queue": [],
            }
        }
        srv = FakeSpaceInvasionServer(planets=planets)
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            dash = requests.get(f"{h.base_url}/dashboard?refresh=0", timeout=5.0).json()
            _verify_eta_fields_present(self, dash["plan"][0])
            expected_all = len(dash["plan"])
            expected_ready = len([a for a in dash["plan"] if a["executable_now"]])
            self.assertGreater(expected_all, expected_ready)

            res = run_ui_in_node(
                h.base_url,
                """
                await __waitUntil(() => __serializeTable("plan-tbody").length > 0 && __serializeTable("plan-tbody")[0].length > 1);
                document.querySelector('[data-page="operations"]').click();
                await __sleep(20);
                const allRows = __serializeTable("plan-tbody");
                document.querySelector('[data-filter="ready"]').click();
                await __sleep(20);
                const readyRows = __serializeTable("plan-tbody");
                const badge = __snapshotElement("plan-count-badge").textContent;
                return { allRows, readyRows, badge };
                """,
            )
            self.assertEqual(res["errors"], [])
            th_count = res["htmlInspection"]["ops_plan"]["th_count"]
            self.assertGreaterEqual(th_count, 7)
            self.assertEqual(len(res["scenario"]["allRows"]), expected_all)
            self.assertEqual(len(res["scenario"]["readyRows"]), expected_ready)
            self.assertIn(f"{expected_ready} TASKS", res["scenario"]["badge"])

    def test_t1_r3c_04_ui_blocked_eta_pill_and_bottleneck_tag(self) -> None:
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [10_000, 0, 10_000, 1_000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 250, "used": 140},
                "buildings": {"b1": 5, "b2": 4, "b3": 4, "b4": 2, "b5": 7, "b7": 1},
                "ships": {},
                "defense": {},
                "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                "building_queue": [],
            }
        }
        srv = FakeSpaceInvasionServer(planets=planets)
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            thr = h.api.send_command(
                {"command": "throttle_building", "building_id": 2, "percentage": 0, "planet_id": 12476, "dry_run": False}
            )
            self.assertTrue(thr["executed"], thr)
            res = run_ui_in_node(
                h.base_url,
                """
                const readyOk = await __waitUntil(() => __serializeTable("dash-plan-tbody").length > 0 && __serializeTable("dash-plan-tbody")[0].length > 1 && !document.querySelector("#dash-plan-tbody td.empty-cell"));
                if (!readyOk) throw new Error("dash-plan-tbody failed to populate");
                document.querySelector('[data-page="operations"]').click();
                const opsOk = await __waitUntil(() => __serializeTable("plan-tbody").length > 0 && !document.querySelector("#plan-tbody td.empty-cell"));
                if (!opsOk) throw new Error("plan-tbody failed to populate");
                return {
                  dashRows: __serializeTable("dash-plan-tbody"),
                  planRows: __serializeTable("plan-tbody")
                };
                """,
            )
            self.assertEqual(res["errors"], [])
            blocked_pills = [
                pill
                for row in res["scenario"]["planRows"]
                for cell in row
                for pill in cell.get("pills", [])
                if "is-blocked" in pill.get("className", "")
            ]
            self.assertGreaterEqual(len(blocked_pills), 1, f"Expected .eta-pill.is-blocked on b2=0% blocked action; rows={res['scenario']['planRows']}")
            row_texts = " ".join(c["text"] for r in res["scenario"]["planRows"] for c in r)
            self.assertIn("—", row_texts)
            self.assertTrue("MET" in row_texts or "METAL" in row_texts.upper(), row_texts)

    def test_t1_r3c_05_ui_config_planner_mode_dirty_guard_and_save_roundtrip(self) -> None:
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            res = run_ui_in_node(
                h.base_url,
                """
                const initOk = await __waitUntil(() => __snapshotElement("cfg-playstyle") !== null && state.dashboard !== null);
                if (!initOk) throw new Error("Initial dashboard/config did not load");
                document.querySelector('[data-page="config"]').click();
                const sel = document.getElementById("cfg-planner_mode");
                if (!sel) throw new Error("Missing #cfg-planner_mode in DOM");
                const initialVal = sel.value;
                sel.value = "ga_nn";
                sel.dispatchEvent("change");
                // Trigger background sync via real #btn-sync DOM click — dirty tracking must preserve "ga_nn"
                const dashBefore = fetchLog.filter((f) => String(f.url || "").startsWith("/dashboard")).length;
                document.getElementById("btn-sync").click();
                const syncOk = await __waitUntil(() => fetchLog.filter((f) => String(f.url || "").startsWith("/dashboard")).length > dashBefore);
                if (!syncOk) throw new Error("#btn-sync did not trigger /dashboard refresh");
                await __waitUntil(() => String(__snapshotElement("feedback-banner").textContent || "").includes("Synced"));
                const preservedVal = sel.value;
                document.getElementById("btn-save-config").click();
                const saveOk = await __waitUntil(() => fetchLog.some((f) => f.url === "/command" && f.body && f.body.command === "update_config"));
                if (!saveOk) throw new Error("update_config POST not sent");
                await __waitUntil(() => String(__snapshotElement("feedback-banner").textContent || "").toLowerCase().includes("saved") || String(__snapshotElement("feedback-banner").textContent || "").toLowerCase().includes("config"));
                return { initialVal, preservedVal, afterSaveVal: sel.value };
                """,
            )
            self.assertEqual(res["errors"], [])
            sc = res["scenario"]
            self.assertEqual(sc["initialVal"], "priority")
            self.assertEqual(sc["preservedVal"], "ga_nn")
            self.assertEqual(sc["afterSaveVal"], "ga_nn")
            cfg = h.api.send_command({"command": "config"})["config"]
            self.assertEqual(cfg["planner_mode"], "ga_nn")

    def test_t1_r3c_06_ui_timeout_resilience_and_rate_limit_banner_keeps_chrome_online(self) -> None:
        srv = FakeSpaceInvasionServer()
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            srv.force_rate_limit_remaining = 2
            res = run_ui_in_node(
                h.base_url,
                """
                const initOk = await __waitUntil(() => state.dashboard !== null);
                if (!initOk) throw new Error("Dashboard failed to initialize");
                document.querySelector('[data-page="combat"]').click();
                document.getElementById("btn-scan-galaxy").click();
                const sentOk = await __waitUntil(() => fetchLog.some((f) => f.url === "/command" && f.body && f.body.command === "scan_galaxy"));
                if (!sentOk) throw new Error("scan_galaxy command not dispatched");
                const bannerOk = await __waitUntil(() => {
                  const b = __snapshotElement("feedback-banner");
                  return b && !b.hidden && !String(b.textContent || "").includes("Scanning home Galaxy");
                });
                if (!bannerOk) throw new Error("Scan galaxy response banner did not settle");
                return {
                  chromeText: __snapshotElement("chrome-status-text").textContent,
                  banner: __snapshotElement("feedback-banner"),
                };
                """,
            )
            self.assertEqual(res["errors"], [])
            self.assertTrue(
                any(d >= 15000 for d in res["recordedTimeoutDelays"]),
                f"Expected apiCommand timeout >= 15000ms; got delays={res['recordedTimeoutDelays']}",
            )
            sc = res["scenario"]
            self.assertIn("ONLINE", sc["chromeText"], f"Rate limit incorrectly flipped Chrome offline: {sc['chromeText']}")
            self.assertFalse(sc["banner"]["hidden"])
            self.assertIn("is-warn", sc["banner"]["className"], f"Expected .is-warn cooldown banner: {sc['banner']}")

    def test_t1_r3c_07_ui_ga_nn_radial_ring_scanner_and_telemetry_hud(self) -> None:
        cfg = make_ga_nn_config(self, planner_mode="ga_nn", ga_generations=10, ga_population_size=16)
        srv = FakeSpaceInvasionServer()
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            html_resp = requests.get(f"{h.base_url}/", timeout=5.0)
            css_resp = requests.get(f"{h.base_url}/styles.css", timeout=5.0)
            js_resp = requests.get(f"{h.base_url}/app.js", timeout=5.0)
            self.assertEqual(html_resp.status_code, 200)
            self.assertEqual(css_resp.status_code, 200)
            self.assertEqual(js_resp.status_code, 200)

            html_txt = html_resp.text
            css_txt = css_resp.text
            stripped_css = re.sub(r"/\*.*?\*/", "", css_txt, flags=re.DOTALL)
            low_html = html_txt.lower()

            # 1) HTML must contain an SVG radial-ring calculation scanner (concentric circles/paths, not rnk-chart)
            svg_blocks = re.findall(r"<svg\b[^>]*>.*?</svg>", html_txt, flags=re.IGNORECASE | re.DOTALL)
            non_chart_svgs = [b for b in svg_blocks if "rnk-chart" not in b]
            self.assertGreater(len(non_chart_svgs), 0, "Expected radial-ring scanner <svg> element in index.html (F21)")
            has_concentric_primitives = any(
                (len(re.findall(r"<(?:circle|path)\b", b, flags=re.IGNORECASE)) >= 2)
                for b in non_chart_svgs
            )
            self.assertTrue(has_concentric_primitives, f"Radial-ring scanner <svg> must contain concentric <circle>/<path> rings: {non_chart_svgs}")

            # Readout metrics referencing Generation, Neural, Fitness, Genome search telemetry
            for metric_kw in ("generation", "neural", "fitness", "genome"):
                self.assertIn(metric_kw, low_html, f"Expected GA+NN HUD metric keyword {metric_kw!r} in index.html (F21)")

            # 2) CSS styling: dark #06090b HUD ground, @keyframes bound to CSS rules, & valid reduced-motion animation suppression
            self.assertIn("#06090b", stripped_css.lower(), "Expected dark #06090b HUD ground in styles.css (F21)")
            keyframes = re.findall(r"@keyframes\s+([A-Za-z0-9_-]+)", stripped_css)
            self.assertGreater(len(keyframes), 0, "Expected @keyframes animation in styles.css (F21)")

            bound_selectors: list[str] = []
            kf_alt = "|".join(re.escape(k) for k in keyframes)
            for m_rule in re.finditer(r"([^{}@]+)\{([^{}]+)\}", stripped_css):
                sel_raw, body = m_rule.group(1).strip(), m_rule.group(2)
                if re.search(rf"animation(?:-name)?\s*:[^;]*\b(?:{kf_alt})\b", body):
                    for part in sel_raw.split(","):
                        clean_sel = re.sub(r"::[A-Za-z0-9_-]+|:[A-Za-z0-9_-]+(?:\([^)]*\))?", "", part).strip()
                        if clean_sel:
                            bound_selectors.append(clean_sel)
            self.assertGreater(len(bound_selectors), 0, f"Expected CSS selector binding @keyframes {keyframes} to a rule in styles.css")

            rm_matches = re.findall(
                r"@media\s*\([^)]*prefers-reduced-motion\s*:\s*reduce[^)]*\)\s*\{((?:[^{}]|\{[^{}]*\})*)\}",
                stripped_css,
                flags=re.IGNORECASE | re.DOTALL,
            )
            self.assertGreater(len(rm_matches), 0, "Expected @media (prefers-reduced-motion: reduce) block in styles.css (F21)")
            rm_anim_selectors: list[str] = []
            for blk in rm_matches:
                for m_rm in re.finditer(r"([^{}@]+)\{([^{}]+)\}", blk):
                    sel_raw, body = m_rm.group(1).strip(), m_rm.group(2)
                    if re.search(
                        r"animation(?:-duration|-name)?\s*:\s*(?:0(?:\.\d+)?\s*(?:ms|s)?|none)",
                        body,
                        flags=re.IGNORECASE,
                    ):
                        for part in sel_raw.split(","):
                            clean_sel = re.sub(r"::[A-Za-z0-9_-]+|:[A-Za-z0-9_-]+(?:\([^)]*\))?", "", part).strip()
                            if clean_sel:
                                rm_anim_selectors.append(clean_sel)
            self.assertGreater(
                len(rm_anim_selectors),
                0,
                f"@media (prefers-reduced-motion: reduce) block does not disable animations: {rm_matches}",
            )

            # Extract baseline static HTML text for comparison to verify JS dynamically populates telemetry
            static_text_norm = re.sub(r"<[^>]+>", " ", html_txt)
            static_text_norm = re.sub(r"\s+", " ", static_text_norm).strip()
            static_html_stripped = re.sub(
                r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
                "",
                html_txt,
                flags=re.IGNORECASE | re.DOTALL,
            )
            static_text_compact = re.sub(
                r"\s+",
                "",
                html.unescape(re.sub(r"<[^>]+>", "", static_html_stripped)),
            ).lower()

            # 3) Node VM executes app.js under ga_nn mode and renders GA+NN calculation HUD visibly with live metrics
            res = run_ui_in_node(
                h.base_url,
                f"""
                const boundSelectors = {json.dumps(bound_selectors)};
                const rmAnimSelectors = {json.dumps(rm_anim_selectors)};
                const readyOk = await __waitUntil(() => document.querySelectorAll('#dash-plan-tbody tr').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 4000);
                if (!readyOk) throw new Error("dash-plan-tbody did not populate non-placeholder rows");

                function isElementHidden(el) {{
                  let cur = el;
                  while (cur && cur.nodeType === 1) {{
                    if (cur.hidden) return true;
                    if (cur.classList && (cur.classList.contains('hidden') || cur.classList.contains('is-hidden'))) return true;
                    if (cur.getAttribute && cur.getAttribute('aria-hidden') === 'true') return true;
                    if (cur.style && (cur.style.display === 'none' || cur.style.visibility === 'hidden')) return true;
                    cur = cur.parentElement || cur.parentNode;
                  }}
                  return false;
                }}

                function findGaNnHudPanel() {{
                  const svgs = Array.from(document.querySelectorAll('svg')).filter((s) => s.id !== 'rnk-chart');
                  const stopClasses = ['app-shell', 'app-layout', 'hud-sidebar', 'main-column', 'hud-canvas', 'page-view'];
                  const candidates = [];
                  for (const svg of svgs) {{
                    let cur = svg.parentElement || svg.parentNode;
                    let steps = 0;
                    while (
                      cur &&
                      cur.nodeType === 1 &&
                      cur !== document.body &&
                      cur !== document.documentElement &&
                      cur.tagName !== 'BODY' &&
                      cur.tagName !== 'HTML' &&
                      cur.id !== 'view-stage' &&
                      cur.id !== 'app-sidebar' &&
                      !(cur.classList && stopClasses.some((cls) => cur.classList.contains(cls)))
                    ) {{
                      const txt = (cur.textContent || '').toLowerCase();
                      if (txt.includes('generation') && (txt.includes('fitness') || txt.includes('neural'))) {{
                        let depth = 0;
                        let anc = cur;
                        while (anc && anc.nodeType === 1) {{
                          depth += 1;
                          anc = anc.parentElement || anc.parentNode;
                        }}
                        const childCount = cur.querySelectorAll ? cur.querySelectorAll('*').length : 0;
                        candidates.push({{ el: cur, depth, childCount, steps }});
                        break;
                      }}
                      steps += 1;
                      cur = cur.parentElement || cur.parentNode;
                    }}
                  }}
                  if (candidates.length === 0) return null;
                  const innermost = candidates.filter(
                    (c) => !candidates.some((other) => {{
                      if (other.el === c.el) return false;
                      let p = other.el.parentElement || other.el.parentNode;
                      while (p) {{
                        if (p === c.el) return true;
                        p = p.parentElement || p.parentNode;
                      }}
                      return false;
                    }})
                  );
                  const pool = innermost.length > 0 ? innermost : candidates;
                  pool.sort((a, b) => (b.depth - a.depth) || (a.childCount - b.childCount) || (a.steps - b.steps));
                  return pool[0].el;
                }}

                const hudPanel = findGaNnHudPanel();
                const hudBeforeSync = hudPanel ? hudPanel.textContent.replace(/\\s+/g, ' ').trim() : '';

                // Verify @keyframes CSS selector matches elements inside hudPanel AND reduced-motion disables them
                const hudAnimatedEls = [];
                if (hudPanel) {{
                  for (const sel of boundSelectors) {{
                    try {{
                      const hits = document.querySelectorAll(sel);
                      for (const el of hits) {{
                        let p = el;
                        while (p) {{
                          if (p === hudPanel) {{
                            if (!hudAnimatedEls.includes(el)) hudAnimatedEls.push(el);
                            break;
                          }}
                          p = p.parentElement || p.parentNode;
                        }}
                      }}
                    }} catch (_) {{}}
                  }}
                }}
                const matchedAnimatedEl = hudAnimatedEls.length > 0;
                const rmMatchedSet = new Set();
                for (const rmSel of rmAnimSelectors) {{
                  try {{
                    for (const el of document.querySelectorAll(rmSel)) rmMatchedSet.add(el);
                  }} catch (_) {{}}
                }}
                const matchedReducedMotionEl = hudAnimatedEls.some((el) => rmMatchedSet.has(el));

                const dashBefore = fetchLog.filter((f) => String(f.url || '').startsWith('/dashboard')).length;
                const runPlanBtn = document.getElementById('btn-sync');
                if (runPlanBtn) {{
                  runPlanBtn.click();
                  await __waitUntil(() => fetchLog.filter((f) => String(f.url || '').startsWith('/dashboard')).length > dashBefore, 3000);
                  await __waitUntil(() => __serializeTable('dash-plan-tbody').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 3000);
                }}

                const hudAfter = hudPanel ? hudPanel.textContent.replace(/\\s+/g, ' ').trim() : '';
                return {{
                  foundHud: Boolean(hudPanel),
                  hidden: hudPanel ? isElementHidden(hudPanel) : true,
                  matchedAnimatedEl,
                  matchedReducedMotionEl,
                  hudBeforeSync,
                  hudText: hudAfter,
                  hasUndef: hudAfter.includes('undefined') || hudAfter.includes('NaN')
                }};
                """,
            )
            self.assertEqual(res["errors"], [])
            sc = res["scenario"]
            self.assertTrue(sc["foundHud"], "Innermost GA+NN radial-ring HUD container missing in DOM")
            self.assertFalse(sc["hidden"], "Expected GA+NN radial-ring calculation HUD visible when planner_mode == 'ga_nn'")
            self.assertTrue(sc["matchedAnimatedEl"], f"None of @keyframes CSS selectors {bound_selectors} matched elements inside GA+NN HUD")
            self.assertTrue(sc["matchedReducedMotionEl"], f"@media (prefers-reduced-motion: reduce) selectors {rm_anim_selectors} did not cover bound HUD animated elements")
            self.assertFalse(sc["hasUndef"], f"Rendered undefined/NaN in GA+NN HUD: {sc['hudText']!r}")
            self.assertRegex(sc["hudText"], r"[1-9]", f"Expected non-zero live numeric telemetry inside GA+NN HUD; got {sc['hudText']!r}")
            # Must not be unhydrated static placeholder text (spaced or tag-adjacent)
            self.assertNotIn(sc["hudText"], static_text_norm, "GA+NN HUD text matches static index.html without dynamic telemetry updates")
            hud_text_compact = re.sub(r"\s+", "", sc["hudText"]).lower()
            if hud_text_compact:
                self.assertNotIn(
                    hud_text_compact,
                    static_text_compact,
                    "GA+NN HUD compact text matches static index.html without dynamic telemetry updates",
                )
